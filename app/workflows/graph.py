"""Milestone 4 LangGraph workflow: Orchestrator + Record Analysis + Policy Research + Compliance & QA.

    START -> orchestrator_start -> record_analysis -> policy_research -> evidence_merge -> compliance_qa
          -> evidence_decision --CLEAR------------------------------> finalize_recommendations
                               --AMBIGUOUS / CONFLICTING--> tot ------> finalize_recommendations
                               --MISSING (once)-----------> targeted_retrieval -> evidence_merge
                               --ERROR--------------------> error_recovery -> record_analysis | finalize
          -> human_review (interrupt; waits for a person) -> complete -> END

Every path ends at human_review: nothing is released without a human decision, and the step
counter forces escalation to human review if the limit is reached.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from app import schemas
from app.agents.executors import ToolExecutor
from app.schemas import (
    PublicRecord,
    PublicRecordRequest,
    RedactionAction,
    RedactionRecommendation,
    RetrievedPassage,
)
from app.workflows import state as wstate
from app.workflows.agents import ComplianceQAAgent, PolicyResearchAgent, RecordAnalysisAgent
from app.workflows.state import (
    AuditEvent,
    CompletionStatus,
    EvidenceDecision,
    HumanDecision,
    HumanReviewStatus,
    RequestContext,
    RetryCounters,
    WorkflowConfig,
    WorkflowState,
)
from app.workflows.tot import build_tot_graph

ROUTES = ("standard", "tot", "targeted_retrieval", "error_recovery", "human_review")


def _serde() -> JsonPlusSerializer:
    """Allow the checkpointer to restore this project's pydantic models (and nothing else unexpected)."""
    allowed = []
    for module in (schemas, wstate):
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and obj.__module__ == module.__name__ and (issubclass(obj, BaseModel) or hasattr(obj, "__members__")):
                allowed.append((module.__name__, name))
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


class Orchestrator:
    """Owns the workflow: routes work, merges evidence, decides the path, enforces limits."""

    name = "orchestrator"

    def __init__(self, executor: ToolExecutor, config: WorkflowConfig | None = None) -> None:
        self.config = config or WorkflowConfig()
        self.record_agent = RecordAnalysisAgent(executor, self.config)
        self.policy_agent = PolicyResearchAgent(executor, self.config)
        self.qa_agent = ComplianceQAAgent(executor, self.config)
        self.run_tot = build_tot_graph(self.policy_agent, self.qa_agent, self.config.tool_timeout_s)
        self.transport = getattr(executor, "transport", "unknown")

    # --- helpers ---------------------------------------------------------------

    @staticmethod
    def _step(state: WorkflowState, node: str, agent: str, event: str, **detail: Any) -> dict[str, Any]:
        step = state.get("step_count", 0) + 1
        return {"step_count": step, "transitions": [node], "audit": [AuditEvent(step=step, node=node, agent=agent, event=event, detail=detail)]}

    def _limit_hit(self, state: WorkflowState) -> bool:
        return state.get("step_count", 0) >= state["config"].max_steps

    # --- nodes -----------------------------------------------------------------

    async def orchestrator_start(self, state: WorkflowState) -> dict[str, Any]:
        r = state["request"]
        return {
            **self._step(state, "orchestrator_start", self.name, "plan",
                         goal=f"Review {r.record_id} for request {r.request_id}",
                         tasks=["record_analysis", "policy_research (per finding category)", "compliance_qa", "evidence_decision"]),
            "retries": RetryCounters(), "human_review": HumanReviewStatus(), "completion": CompletionStatus.RUNNING,
            "disclosure_authorized": False, "evidence": [], "tot_results": [],
        }

    async def record_analysis(self, state: WorkflowState) -> dict[str, Any]:
        result = await self.record_agent.run(state["request"].record_id)
        update = self._step(state, "record_analysis", self.record_agent.name, f"detect_pii {result.status}",
                            findings=[f"{f.finding_id} {f.category.value} ({f.confidence})" for f in result.findings], errors=result.errors)
        retries = state["retries"].model_copy(update={"record_analysis": state["retries"].record_analysis + result.attempts})
        return {**update, "record_analysis": result, "findings": result.findings, "retries": retries,
                "tool_calls": self.record_agent.tools.take()}

    async def policy_research(self, state: WorkflowState) -> dict[str, Any]:
        categories = list(dict.fromkeys(f.category for f in state.get("findings", [])))
        evidence = await self.policy_agent.research(categories)
        update = self._step(state, "policy_research", self.policy_agent.name, "retrieved policy evidence",
                            per_category={e.category.value: {"status": e.status, "chunks": [c.chunk_id for c in e.passages]} for e in evidence})
        return {**update, "evidence": evidence, "tool_calls": self.policy_agent.tools.take()}

    async def evidence_merge(self, state: WorkflowState) -> dict[str, Any]:
        by_cat = {e.category: e for e in state.get("evidence", [])}
        merged = {f.finding_id: {"category": f.category.value, "evidence_status": by_cat[f.category].status if f.category in by_cat else "NONE",
                                 "chunks": [c.chunk_id for c in by_cat[f.category].passages] if f.category in by_cat else []}
                  for f in state.get("findings", [])}
        return self._step(state, "evidence_merge", self.name, "merged findings with policy evidence", findings=merged)

    async def compliance_qa(self, state: WorkflowState) -> dict[str, Any]:
        report = await self.qa_agent.review(state["request"].record_id, state["record_text"], state.get("findings", []), state.get("evidence", []))
        update = self._step(state, "compliance_qa", self.qa_agent.name, "validated proposal and assessed evidence",
                            assessments={a.finding_id: a.decision.value for a in report.assessments},
                            invalid_citations=report.invalid_citations, possible_missed=report.possible_missed, errors=report.errors)
        return {**update, "qa": report, "tool_calls": self.qa_agent.tools.take()}

    async def evidence_decision(self, state: WorkflowState) -> dict[str, Any]:
        decisions = {a.decision for a in state["qa"].assessments} if state.get("qa") else set()
        retries = state["retries"]
        if self._limit_hit(state):
            route, why = "human_review", "step limit reached"
        elif state["record_analysis"].status == "FAILED":
            route, why = "error_recovery", "record analysis failed"
        elif EvidenceDecision.MISSING in decisions and retries.missing_evidence < state["config"].missing_evidence_retries:
            route, why = "targeted_retrieval", "missing policy evidence; one targeted retry allowed"
        elif decisions & {EvidenceDecision.AMBIGUOUS, EvidenceDecision.CONFLICTING}:
            route, why = "tot", "ambiguous or conflicting evidence"
        else:
            route, why = "standard", "evidence clear (or missing after retry, or no findings)"
        update = self._step(state, "evidence_decision", self.name, f"route={route}", reason=why, decisions=sorted(d.value for d in decisions))
        extra = {"escalations": [f"Step limit {state['config'].max_steps} reached; escalated to human review."]} if route == "human_review" else {}
        return {**update, "route": route, **extra}

    async def targeted_retrieval(self, state: WorkflowState) -> dict[str, Any]:
        missing = [a.category for a in state["qa"].assessments if a.decision is EvidenceDecision.MISSING]
        evidence = {e.category: e for e in state.get("evidence", [])}
        for category in dict.fromkeys(missing):
            phrase = category.value.lower().replace("_", " ")
            evidence[category] = await self.policy_agent.targeted_search(
                category, f"{phrase} personal information exempt from public access redaction", evidence.get(category))
        retries = state["retries"].model_copy(update={"missing_evidence": state["retries"].missing_evidence + 1})
        update = self._step(state, "targeted_retrieval", self.policy_agent.name, "targeted retry for missing evidence",
                            categories=[c.value for c in dict.fromkeys(missing)],
                            result={c.value: evidence[c].status for c in dict.fromkeys(missing)})
        return {**update, "evidence": list(evidence.values()), "retries": retries, "tool_calls": self.policy_agent.tools.take()}

    async def error_recovery(self, state: WorkflowState) -> dict[str, Any]:
        retries = state["retries"]
        if retries.error_recovery < state["config"].max_error_recoveries and not self._limit_hit(state):
            update = self._step(state, "error_recovery", self.name, "retrying record analysis")
            return {**update, "route": "record_analysis", "retries": retries.model_copy(update={"error_recovery": retries.error_recovery + 1})}
        update = self._step(state, "error_recovery", self.name, "recovery exhausted; escalating")
        return {**update, "route": "finalize_recommendations",
                "escalations": ["Record analysis failed after bounded recovery; the record must be reviewed manually."]}

    async def tot(self, state: WorkflowState) -> dict[str, Any]:
        by_cat = {e.category: e for e in state.get("evidence", [])}
        findings = {f.finding_id: f for f in state["findings"]}
        results, calls = [], []
        for a in state["qa"].assessments:
            if a.decision in (EvidenceDecision.AMBIGUOUS, EvidenceDecision.CONFLICTING):
                result, tool_calls = await self.run_tot(findings[a.finding_id], state["record_text"],
                                                        by_cat[a.category].passages if a.category in by_cat else [], state["config"].tot)
                results.append(result)
                calls.extend(tool_calls)
        update = self._step(state, "tot", self.name, "bounded Tree of Thought",
                            runs={r.finding_id: {"outcome": r.outcome, "selected": r.selected_node_id, "score": r.selected_score,
                                                 "pruned": len(r.pruned), "depth": r.depth_reached, "tool_calls": r.tool_calls_used} for r in results})
        return {**update, "tot_results": results, "tool_calls": calls}

    async def finalize_recommendations(self, state: WorkflowState) -> dict[str, Any]:
        qa = state.get("qa")
        proposal = {r.finding_id: r for r in qa.proposal.recommendations} if qa and qa.proposal else {}
        assessments = {a.finding_id: a for a in qa.assessments} if qa else {}
        tot_by_finding = {r.finding_id: r for r in state.get("tot_results", [])}
        invalid = set(qa.invalid_citations) if qa else set()
        recs: list[RedactionRecommendation] = []
        escalations: list[str] = []

        for i, f in enumerate(state.get("findings", []), start=1):
            a = assessments.get(f.finding_id)
            base = proposal.get(f.finding_id)
            rid = f"{f.record_id}-R{i:02d}"
            if f.finding_id in tot_by_finding:
                t = tot_by_finding[f.finding_id]
                rec = recommendation_from_tot(f, a.decision.value if a else "n/a", t, rid)
                recs.append(rec)
                if rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED:
                    escalations.append(f"{f.finding_id}: {t.reason}")
            elif a is not None and a.decision is EvidenceDecision.MISSING:
                recs.append(RedactionRecommendation(
                    recommendation_id=rid, finding_id=f.finding_id, action=RedactionAction.HUMAN_REVIEW_REQUIRED,
                    rationale=f"Insufficient policy evidence: no retrieved passage names {f.category.value} after a targeted retry. Human review required.",
                    detection_confidence=f.confidence, open_questions=["Which provision, if any, applies to this information?"]))
                escalations.append(f"{f.finding_id}: insufficient policy evidence; human review required.")
            elif base is not None:
                bad = [r.chunk_id for r in base.policy_references if r.chunk_id in invalid]
                if bad:  # QA veto: a recommendation resting on an unverifiable citation goes to a human
                    base = base.model_copy(update={"action": RedactionAction.HUMAN_REVIEW_REQUIRED,
                                                   "rationale": f"QA could not validate citation(s) {bad}. " + base.rationale})
                    escalations.append(f"{f.finding_id}: citation validation failed.")
                recs.append(base.model_copy(update={"recommendation_id": rid}))
        if qa:
            escalations += [f"QA flag: {m}" for m in qa.possible_missed]
        update = self._step(state, "finalize_recommendations", self.name, "assembled recommendations",
                            recommendations={r.finding_id: r.action.value for r in recs})
        return {**update, "recommendations": recs, "escalations": escalations}

    async def human_review(self, state: WorkflowState) -> dict[str, Any]:
        package = {
            "request_id": state["request"].request_id,
            "record_id": state["request"].record_id,
            "recommendations": [r.model_dump(mode="json") for r in state.get("recommendations", [])],
            "escalations": state.get("escalations", []),
            "instruction": "Approve, reject, or return. Nothing is released without approval.",
        }
        raw = interrupt(package)  # pauses here until a person resumes the workflow with a decision
        decision = HumanDecision.model_validate(raw)
        status = {"approve": "APPROVED", "reject": "REJECTED", "return": "RETURNED_FOR_REVISION"}[decision.decision]
        update = self._step(state, "human_review", "human_reviewer", f"decision={decision.decision}", reviewer=decision.reviewer, notes=decision.notes)
        return {**update, "human_review": HumanReviewStatus(status=status, reviewer=decision.reviewer, notes=decision.notes,
                                                            decided_at=datetime.now(timezone.utc).isoformat(timespec="seconds")),
                "disclosure_authorized": decision.decision == "approve"}

    async def complete(self, state: WorkflowState) -> dict[str, Any]:
        completion = {"APPROVED": CompletionStatus.APPROVED_BY_HUMAN, "REJECTED": CompletionStatus.REJECTED_BY_HUMAN,
                      "RETURNED_FOR_REVISION": CompletionStatus.RETURNED_FOR_REVISION}[state["human_review"].status]
        update = self._step(state, "complete", self.name, f"completion={completion.value}")
        return {**update, "completion": completion}

    # --- graph -----------------------------------------------------------------

    def build(self, checkpointer=None):
        g = StateGraph(WorkflowState)
        for name in ("orchestrator_start", "record_analysis", "policy_research", "evidence_merge", "compliance_qa", "evidence_decision",
                     "targeted_retrieval", "error_recovery", "tot", "finalize_recommendations", "human_review", "complete"):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "orchestrator_start")
        g.add_edge("orchestrator_start", "record_analysis")
        g.add_conditional_edges("record_analysis", lambda s: "error_recovery" if s["record_analysis"].status == "FAILED" else "policy_research",
                                {"error_recovery": "error_recovery", "policy_research": "policy_research"})
        g.add_edge("policy_research", "evidence_merge")
        g.add_edge("evidence_merge", "compliance_qa")
        g.add_edge("compliance_qa", "evidence_decision")
        g.add_conditional_edges("evidence_decision", lambda s: s["route"], {
            "standard": "finalize_recommendations", "tot": "tot", "targeted_retrieval": "targeted_retrieval",
            "error_recovery": "error_recovery", "human_review": "finalize_recommendations"})
        g.add_edge("targeted_retrieval", "evidence_merge")
        g.add_conditional_edges("error_recovery", lambda s: s["route"],
                                {"record_analysis": "record_analysis", "finalize_recommendations": "finalize_recommendations"})
        g.add_edge("tot", "finalize_recommendations")
        g.add_edge("finalize_recommendations", "human_review")
        g.add_edge("human_review", "complete")
        g.add_edge("complete", END)
        return g.compile(checkpointer=checkpointer or InMemorySaver(serde=_serde()))


def recommendation_from_tot(finding, evidence_decision: str, t, recommendation_id: str) -> RedactionRecommendation:
    """Map a ToT result to a recommendation. REDACT only for a supported branch with cited evidence."""
    node = next((n for n in t.nodes if n.node_id == t.selected_node_id), None)
    refs = [RetrievedPassage(chunk=c, rank=j).to_policy_reference() for j, c in enumerate(node.evidence, 1)] if node else []
    questions = sorted({q for q in (node.unresolved_questions if node else t.unresolved)} |
                       {f"Confirm the condition '{asm.text}' (basis: {asm.evidence})."
                        for asm in (node.assumptions if node else []) if asm.evidence})
    action = RedactionAction.REDACT if (t.outcome == "SUPPORTED_RECOMMENDATION" and refs) else RedactionAction.HUMAN_REVIEW_REQUIRED
    return RedactionRecommendation(
        recommendation_id=recommendation_id, finding_id=finding.finding_id, action=action,
        rationale=f"Tree of Thought ({evidence_decision} evidence): {t.reason} {node.interpretation if node else ''}".strip(),
        policy_references=refs, detection_confidence=finding.confidence, open_questions=questions)


def initial_state(request: PublicRecordRequest, record: PublicRecord, config: WorkflowConfig) -> WorkflowState:
    return {
        "request": RequestContext(request_id=request.request_id, record_id=record.record_id, received_date=request.received_date,
                                  description=request.description, record_title=record.title, record_type=record.record_type.value),
        "record_text": record.text,
        "config": config,
        "step_count": 0,
        "tool_calls": [], "audit": [], "transitions": [], "escalations": [],
    }


def recursion_limit(config: WorkflowConfig) -> int:
    return 2 * config.max_steps + 10
