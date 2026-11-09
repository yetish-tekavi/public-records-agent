"""Bounded Tree of Thought (beam search) for ambiguous or conflicting evidence.

Invoked by the Orchestrator for one finding at a time, as a separate LangGraph subgraph with
its own state (``ToTState``). It reuses the existing agents: the Policy Research Agent performs
at most one targeted retrieval, and the Compliance & QA Agent is the critic (citation checks via
the ``validate_citations`` MCP tool plus rubric scoring).

    depth 1  INTERPRETATION  candidate readings of the retrieved provisions (<= max_branching)
    depth 2  VERIFICATION    check each candidate's assumptions against the record and, once
                             per run, retrieve the provision text a candidate still needs
    depth 3  CONCLUSION      fix the candidate's final form

Rubric (100): policy relevance 30, citation validity 30, consistency with facts and conditions 25,
explanation and uncertainty handling 15. Fabricated citations and unsupported factual assumptions
are rejected outright. A high score is not proof of legal correctness, and no branch can authorise
disclosure: the best outcome is a recommendation for a human reviewer.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.schemas import PolicyChunk, SensitiveCategory, SensitiveDataFinding
from app.tools.service import ToolError
from app.workflows import evidence as ev
from app.workflows.agents import ComplianceQAAgent, PolicyResearchAgent, ToolCaller
from app.workflows.state import (
    Assumption,
    BranchScore,
    PruneRecord,
    ThoughtNode,
    ToTConfig,
    ToTResult,
    ToTState,
)

HUMAN_APPROVAL_NOTE = "Recommendation only; a human reviewer must approve any redaction or release."

CATEGORY_PHRASE = {
    SensitiveCategory.STREET_ADDRESS: "home address",
    SensitiveCategory.PERSON_NAME: "personal information",
    SensitiveCategory.PHONE_NUMBER: "personal telephone number",
    SensitiveCategory.EMAIL_ADDRESS: "personal email address",
}

# --- generation -------------------------------------------------------------------


def _phrase(category: SensitiveCategory) -> str:
    return CATEGORY_PHRASE.get(category, category.value.lower().replace("_", " "))


def generate_interpretations(finding: SensitiveDataFinding, evidence: list[PolicyChunk], config: ToTConfig,
                             record_text: str = "") -> list[ThoughtNode]:
    """Depth-1 candidates derived only from retrieved provisions, plus a conservative review candidate."""
    support = ev.supporting(evidence, finding.category)
    exclusion = ev.applicable_exclusion(finding, evidence, record_text)
    by_type: dict[str, ev.ProvisionAnalysis] = {}
    for a in support:  # authority order, so the first of each type is the strongest source
        if a.restricted_class:
            kind = "RESTRICTED_CLASS"
        elif a.operative:
            kind = "GENERAL_OPERATIVE"
        elif a.definition or a.balancing:
            kind = "DEFINITION_OR_BALANCING"
        else:
            continue
        by_type.setdefault(kind, a)

    candidates: list[ThoughtNode] = []
    if "RESTRICTED_CLASS" in by_type:
        a = by_type["RESTRICTED_CLASS"]
        candidates.append(ThoughtNode(
            node_id="", parent_id=None, depth=1, stage="INTERPRETATION", action="REDACT", provision_type="RESTRICTED_CLASS",
            interpretation=f"Redact: {a.chunk.section} protects this {_phrase(finding.category)} if the person belongs to a covered class.",
            cited_chunk_ids=[a.chunk.chunk_id], evidence=[a.chunk.model_copy()],
            assumptions=[Assumption(text="The person is a covered person (judicial officer, prosecutor, law enforcement officer, "
                                         "child protective investigator, or immediate family member).",
                                    kind="FACTUAL", check_terms=ev.COVERED_PERSON_TERMS)],
        ))
    if "GENERAL_OPERATIVE" in by_type:
        a = by_type["GENERAL_OPERATIVE"]
        candidates.append(ThoughtNode(
            node_id="", parent_id=None, depth=1, stage="INTERPRETATION", action="REDACT", provision_type="GENERAL_OPERATIVE",
            interpretation=f"Redact: {a.chunk.section} applies operative language to this {_phrase(finding.category)} generally.",
            cited_chunk_ids=[a.chunk.chunk_id], evidence=[a.chunk.model_copy()],
        ))
    if "DEFINITION_OR_BALANCING" in by_type:
        a = by_type["DEFINITION_OR_BALANCING"]
        candidates.append(ThoughtNode(
            node_id="", parent_id=None, depth=1, stage="INTERPRETATION", action="REDACT", provision_type="DEFINITION_OR_BALANCING",
            interpretation=f"Redact: {a.chunk.section} defines or weighs this information; redaction would follow only if an operative rule applies.",
            cited_chunk_ids=[a.chunk.chunk_id], evidence=[a.chunk.model_copy()],
            assumptions=[Assumption(text=f"An operative provision requires redacting this {_phrase(finding.category)}.", kind="LEGAL")],
        ))
    review_sources = support[:2]
    candidates.append(ThoughtNode(
        node_id="", parent_id=None, depth=1, stage="INTERPRETATION", action="HUMAN_REVIEW_REQUIRED", provision_type="CONDITIONAL_REVIEW",
        interpretation="Escalate: retrieved provisions apply only under conditions the record does not establish; a reviewer must decide.",
        cited_chunk_ids=[a.chunk.chunk_id for a in review_sources], evidence=[a.chunk.model_copy() for a in review_sources],
    ))
    # Conflicting evidence: every redaction reading must also survive the retrieved exclusion clause.
    if exclusion:
        excl_chunk = next(c for c in evidence if c.chunk_id == exclusion)
        for c in candidates:
            if c.action == "REDACT":
                c.assumptions.append(Assumption(text=ev.EXCLUSION_ASSUMPTION[finding.category], kind="FACTUAL",
                                                check_terms=ev.AGENCY_CONTEXT_TERMS, supported_when_found=False))
            if exclusion not in c.cited_chunk_ids:
                c.cited_chunk_ids.append(exclusion)
                c.evidence.append(excl_chunk.model_copy())
    # Keep the review candidate; trim the weakest interpretations beyond the branching limit.
    while len(candidates) > config.max_branching:
        candidates.pop(-2)
    for i, c in enumerate(candidates, start=1):
        c.node_id = f"d1.{i}"
        c.unresolved_questions = [a.text for a in c.assumptions]
    return candidates


def check_fact(assumption: Assumption, record_text: str, finding: SensitiveDataFinding) -> None:
    """Resolve a FACTUAL assumption against the record text near the finding (in place, on a branch copy)."""
    quote = ev.find_terms(record_text, finding, assumption.check_terms)
    if assumption.supported_when_found:
        assumption.status = "SUPPORTED" if quote else "UNSUPPORTED"
        assumption.evidence = f"record: '{quote}'" if quote else "no supporting term in the record"
    else:
        assumption.status = "UNSUPPORTED" if quote else "SUPPORTED"
        assumption.evidence = f"record contradicts it: '{quote}'" if quote else "no contrary term in the record"


def _authority(node: ThoughtNode) -> int:
    return min((ev.authority(c) for c in node.evidence), default=9)


def choose_best(candidates: list[ThoughtNode]) -> tuple[ThoughtNode | None, str | None]:
    """Highest score wins; ties go to stronger primary-source evidence; an unresolved tie between
    different actions is a material conflict and is escalated (returns None with the reason)."""
    if not candidates:
        return None, None
    ranked = sorted(candidates, key=lambda n: (-n.score.total, _authority(n), n.node_id))
    best = ranked[0]
    for other in ranked[1:]:
        if other.score.total == best.score.total and _authority(other) == _authority(best) and other.action != best.action:
            return None, (f"Unresolved material conflict between {best.node_id} ({best.action}) and {other.node_id} "
                          f"({other.action}) at equal score {best.score.total} and equal source strength.")
    return best, None


def needs_operative_support(node: ThoughtNode, finding: SensitiveDataFinding) -> bool:
    if node.action != "REDACT":
        return False
    return not any(ev.analyse_passage(c, finding.category).operative for c in node.evidence)


# --- critic (Compliance & QA) --------------------------------------------------------


def score_node(node: ThoughtNode, finding: SensitiveDataFinding, citations_valid: bool | None) -> BranchScore:
    analyses = [ev.analyse_passage(c, finding.category) for c in node.evidence]
    names = any(a.names_category for a in analyses)
    operative = any(a.operative for a in analyses)
    primary = any(c.doc_id == "opra_statute" for c in node.evidence)
    notes: list[str] = []

    if node.action == "REDACT":
        relevance = (15 if names else 0) + (10 if operative else 0) + (5 if primary else 0)
        if not operative:
            notes.append("cited passages do not contain operative redaction language")
    else:  # an escalation does not apply the evidence, so it earns relevance only for naming the category
        relevance = 15 if names else 0

    if citations_valid is None:
        citation = 0
        notes.append("citations could not be validated (budget)")
    elif node.action == "REDACT" and not operative:
        citation = 15
    else:
        citation = 30

    unverified = sum(a.status == "UNVERIFIED" for a in node.assumptions)
    consistency = max(0, 25 - 12 * unverified)
    if unverified:
        notes.append(f"{unverified} assumption(s) unverified")

    # Explanation: a stated interpretation (5), uncertainty made explicit when any gap remains (5),
    # and the human-approval condition (5, always attached to ToT output).
    gaps = unverified or citation < 30
    explanation = (5 if node.interpretation else 0) + (5 if (node.unresolved_questions or not gaps) else 0) + 5
    if gaps and not node.unresolved_questions:
        notes.append("uncertainty not stated")
    total = relevance + citation + consistency + explanation
    return BranchScore(policy_relevance=relevance, citation_validity=citation, consistency=consistency,
                       explanation=explanation, total=total, notes=notes)


def rejection_reason(node: ThoughtNode, invalid_ids: list[str]) -> str | None:
    if invalid_ids:
        return f"fabricated or altered citation(s): {invalid_ids}"
    if not node.cited_chunk_ids and node.action == "REDACT":
        return "redaction candidate cites no retrieved evidence"
    for a in node.assumptions:
        if a.kind == "FACTUAL" and a.status == "UNSUPPORTED":
            return f"unsupported factual assumption: {a.text}"
    return None


def selectable(node: ThoughtNode, finding: SensitiveDataFinding, config: ToTConfig) -> bool:
    if node.status in ("REJECTED", "PRUNED") or node.score is None or node.score.total < config.min_score:
        return False
    if node.action == "REDACT":  # mandatory constraint: no unsupported redaction recommendation
        return all(a.status == "SUPPORTED" for a in node.assumptions) and not needs_operative_support(node, finding)
    return True


# --- subgraph ------------------------------------------------------------------------


def build_tot_graph(policy: PolicyResearchAgent, qa: ComplianceQAAgent, tool_timeout_s: float = 120.0):
    tools = ToolCaller(qa.tools.executor, "compliance_qa", tool_timeout_s)

    def out_of_time(state: ToTState) -> bool:
        return time.monotonic() - state["started_at"] > state["config"].time_budget_s

    async def generate(state: ToTState) -> dict[str, Any]:
        finding, config = state["finding"], state["config"]
        depth = state["depth"] + 1
        nodes = dict(state["nodes"])
        events = list(state["budget_events"])
        used, retrieval_used = state["tool_calls_used"], state["targeted_retrieval_used"]

        if depth == 1:
            children = generate_interpretations(finding, state["evidence"], config, state["record_text"])
        else:
            children = []
            stage = "VERIFICATION" if depth == 2 else "CONCLUSION"
            for parent in state["frontier"]:
                child = parent.model_copy(deep=True)  # branch-local copy: no shared evidence or assumptions
                child.node_id, child.parent_id, child.depth, child.stage = f"{parent.node_id}>{depth}", parent.node_id, depth, stage
                child.status, child.score = "PENDING", None
                if stage == "VERIFICATION":
                    for a in child.assumptions:
                        if a.kind == "FACTUAL" and a.status == "UNVERIFIED":
                            check_fact(a, state["record_text"], finding)
                    factual_failed = any(a.kind == "FACTUAL" and a.status == "UNSUPPORTED" for a in child.assumptions)
                    wants_retrieval = not factual_failed and (
                        needs_operative_support(child, finding) or any(a.kind == "LEGAL" and a.status == "UNVERIFIED" for a in child.assumptions))
                    if wants_retrieval:
                        if not config.allow_targeted_retrieval or retrieval_used:
                            events.append(f"{child.node_id}: targeted retrieval not available (already used or disabled)")
                        elif used >= config.max_tool_calls:
                            events.append(f"{child.node_id}: targeted retrieval skipped, tool budget exhausted")
                        else:
                            qualifier = "judicial officer prosecutor law enforcement officer" if child.provision_type == "RESTRICTED_CLASS" else "personal identifying information"
                            query = f"custodian shall redact {_phrase(finding.category)} {qualifier}"
                            retrieval_used, used = True, used + 1
                            try:
                                found = await policy.targeted_search(finding.category, query, None)
                                for chunk in found.passages:
                                    a = ev.analyse_passage(chunk, finding.category)
                                    type_ok = a.restricted_class if child.provision_type == "RESTRICTED_CLASS" else not a.restricted_class
                                    if a.operative and type_ok and chunk.chunk_id not in child.cited_chunk_ids:
                                        child.evidence.append(chunk.model_copy())
                                        child.cited_chunk_ids.append(chunk.chunk_id)
                                        for asm in child.assumptions:
                                            if asm.kind == "LEGAL":
                                                asm.status, asm.evidence = "SUPPORTED", f"operative text in {chunk.chunk_id}"
                                        break
                                events.append(f"{child.node_id}: targeted retrieval {query!r} -> {[c.chunk_id for c in found.passages]}")
                            except ToolError as exc:
                                events.append(f"{child.node_id}: targeted retrieval failed ({exc.code})")
                child.unresolved_questions = [a.text for a in child.assumptions if a.status != "SUPPORTED"]
                if needs_operative_support(child, finding):
                    child.unresolved_questions.append("No retrieved operative provision directs this redaction.")
                if stage == "CONCLUSION":
                    child.terminal = True
                children.append(child)
            if len(children) > config.max_branching * config.beam_width:
                raise AssertionError("branching limit exceeded")
        for c in children:
            nodes[c.node_id] = c
        return {"depth": depth, "frontier": children, "nodes": nodes, "tool_calls_used": used,
                "targeted_retrieval_used": retrieval_used, "budget_events": events}

    async def evaluate(state: ToTState) -> dict[str, Any]:
        finding, config = state["finding"], state["config"]
        nodes, used = dict(state["nodes"]), state["tool_calls_used"]
        events = list(state["budget_events"])
        cache: dict[str, bool] = dict(state.get("citation_cache", {}))
        pending = [nodes[c.node_id] for c in state["frontier"]]
        unchecked = {cid: chunk.text for n in pending for cid, chunk in zip(n.cited_chunk_ids, n.evidence) if cid not in cache}
        if unchecked:
            if used >= config.max_tool_calls:
                events.append(f"depth {state['depth']}: citation validation skipped, tool budget exhausted")
            else:
                used += 1
                try:
                    checks = await qa.validate([{"chunk_id": cid, "excerpt": text} for cid, text in unchecked.items()], tools)
                    cache.update({c.chunk_id: c.exists and c.excerpt_matches is not False for c in checks})
                except ToolError as exc:
                    events.append(f"depth {state['depth']}: citation validation failed ({exc.code})")
        for node in pending:
            known = [cache.get(cid) for cid in node.cited_chunk_ids]
            invalid = [cid for cid, ok in zip(node.cited_chunk_ids, known) if ok is False]
            valid = None if any(k is None for k in known) else not invalid
            node = node.model_copy(update={"score": score_node(node, finding, valid), "status": "EVALUATED"})
            reason = rejection_reason(node, invalid)
            if reason:
                node.status = "REJECTED"
                node.unresolved_questions = node.unresolved_questions + [f"rejected: {reason}"]
            nodes[node.node_id] = node
        return {"nodes": nodes, "tool_calls_used": used, "budget_events": events, "citation_cache": cache,
                "frontier": [nodes[c.node_id] for c in state["frontier"]]}

    async def prune(state: ToTState) -> dict[str, Any]:
        config = state["config"]
        nodes, pruned = dict(state["nodes"]), list(state["pruned"])
        frontier = state["frontier"]
        for n in frontier:
            if n.status == "REJECTED":
                pruned.append(PruneRecord(node_id=n.node_id, depth=n.depth, reason=n.unresolved_questions[-1]))
        eligible = sorted((n for n in frontier if n.status != "REJECTED"),
                          key=lambda n: (-n.score.total, min((ev.authority(c) for c in n.evidence), default=9)))
        keep, drop = eligible[: config.beam_width], eligible[config.beam_width :]
        for n in drop:
            nodes[n.node_id] = n.model_copy(update={"status": "PRUNED"})
            pruned.append(PruneRecord(node_id=n.node_id, depth=n.depth, reason=f"beam width {config.beam_width}: score {n.score.total} not in top {config.beam_width}"))
        # A node is complete when nothing is left to verify (or it is a conclusion); only the rest expand.
        expandable = []
        for n in keep:
            open_gaps = any(a.status == "UNVERIFIED" for a in n.assumptions) or needs_operative_support(n, state["finding"])
            if n.terminal or not open_gaps:
                nodes[n.node_id] = n.model_copy(update={"terminal": True})
            else:
                expandable.append(nodes[n.node_id])
        return {"nodes": nodes, "pruned": pruned, "frontier": expandable}

    def route(state: ToTState) -> str:
        if not state["frontier"] or state["depth"] >= state["config"].max_depth or out_of_time(state):
            return "select"
        return "generate"

    async def select(state: ToTState) -> dict[str, Any]:
        finding, config = state["finding"], state["config"]
        nodes = dict(state["nodes"])
        events = list(state["budget_events"])
        frontier_ids = {n.node_id for n in state["frontier"]}
        if frontier_ids and state["depth"] >= config.max_depth:
            events.append(f"max depth {config.max_depth} reached with {len(frontier_ids)} node(s) still open")
        if frontier_ids and out_of_time(state):
            events.append(f"time budget {config.time_budget_s}s exhausted at depth {state['depth']}")
        candidates = [n for n in nodes.values() if (n.terminal or n.node_id in frontier_ids) and selectable(n, finding, config)]
        unresolved = sorted({q for n in nodes.values() if n.status != "REJECTED" for q in n.unresolved_questions})
        best, conflict = choose_best(candidates)

        if not candidates:
            result_kwargs = dict(outcome="ESCALATED", action="HUMAN_REVIEW_REQUIRED", selected_node_id=None, selected_score=None,
                                 reason=f"No branch reached {config.min_score}/100 with verified support.")
        else:
            if conflict:
                result_kwargs = dict(outcome="ESCALATED", action="HUMAN_REVIEW_REQUIRED", selected_node_id=None,
                                     selected_score=None, reason=conflict)
            else:
                nodes[best.node_id] = best.model_copy(update={"status": "SELECTED"})
                if best.action == "REDACT":
                    reason = f"Selected {best.node_id} ({best.provision_type}) with score {best.score.total}/100; all assumptions verified."
                else:
                    rejected = [f"{p.node_id} {p.reason}" for p in state["pruned"] if p.reason.startswith("rejected")]
                    unsupported = [n.node_id for n in nodes.values() if n.action == "REDACT" and n.status != "REJECTED" and not selectable(n, finding, config)]
                    reason = (f"Escalated: no redaction branch was supported, so {best.node_id} (conditional review, "
                              f"{best.score.total}/100) was selected."
                              + (f" Rejected: {'; '.join(rejected)}." if rejected else "")
                              + (f" Not selectable (unverified or below {config.min_score}): {unsupported}." if unsupported else ""))
                result_kwargs = dict(
                    outcome="SUPPORTED_RECOMMENDATION" if best.action == "REDACT" else "ESCALATED", action=best.action,
                    selected_node_id=best.node_id, selected_score=best.score.total, reason=reason,
                )
        result = ToTResult(finding_id=finding.finding_id, category=finding.category, nodes=list(nodes.values()),
                           pruned=state["pruned"], depth_reached=state["depth"], tool_calls_used=state["tool_calls_used"],
                           targeted_retrieval_used=state["targeted_retrieval_used"], budget_events=events,
                           unresolved=unresolved, **result_kwargs)
        return {"result": result, "nodes": nodes, "budget_events": events}

    graph = StateGraph(ToTState)
    graph.add_node("generate", generate)
    graph.add_node("evaluate", evaluate)
    graph.add_node("prune", prune)
    graph.add_node("select", select)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "evaluate")
    graph.add_edge("evaluate", "prune")
    graph.add_conditional_edges("prune", route, {"generate": "generate", "select": "select"})
    graph.add_edge("select", END)
    compiled = graph.compile()

    async def run(finding: SensitiveDataFinding, record_text: str, evidence: list[PolicyChunk], config: ToTConfig) -> tuple[ToTResult, list]:
        state = await compiled.ainvoke(
            {"finding": finding, "record_text": record_text, "evidence": [c.model_copy() for c in evidence], "config": config,
             "depth": 0, "frontier": [], "nodes": {}, "pruned": [], "tool_calls_used": 0, "targeted_retrieval_used": False,
             "started_at": time.monotonic(), "budget_events": []},
            config={"recursion_limit": 4 * config.max_depth + 6},
        )
        return state["result"], tools.take() + policy.tools.take()

    return run
