"""Run evaluation scenarios through the real Milestone 4/5 workflow and record observed results.

Each scenario runs under Baseline A (ToT disabled) and Configuration B (selective ToT), with the
same corpus, retrieval settings, detector, and inputs. Faults are injected at the executor level.
Reviewer actions are SCRIPTED (simulated) from the scenario definition and applied to a real
ReviewSession; finalization is then attempted exactly as a reviewer would.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any

from app.agents.executors import LocalToolExecutor
from app.data_loader import PROJECT_ROOT, load_review_cases
from app.evaluation.dataset import DATA, injection_chunk
from app.policies.chunker import load_policy_chunks
from app.policies.retriever import PolicyRetriever
from app.policies.semantic import SemanticIndex
from app.review.redact import RedactionSpan
from app.review.session import ReviewError, ReviewSession
from app.tools.service import ToolError, ToolService
from app.workflows.graph import Orchestrator
from app.workflows.runner import start
from app.workflows.state import WorkflowConfig

REVIEWER = "Evaluation reviewer (scripted)"
SCENARIO_TIMEOUT_S = 180
CONFIGS = {"A": "Baseline A: ReAct-only (ToT disabled)", "B": "Configuration B: selective ToT enabled"}


class FaultInjectingExecutor:
    """Wraps a real executor; fails selected calls per tool to simulate tool outages."""

    def __init__(self, inner, faults: list[dict[str, Any]]) -> None:
        self.inner = inner
        self.transport = inner.transport
        self.faults = faults
        self.counts: dict[str, int] = {}
        self.injected: list[dict[str, Any]] = []

    def _should_fail(self, tool: str, n: int) -> bool:
        for f in self.faults:
            if f["tool"] != tool:
                continue
            calls = f.get("fail_calls")
            if calls == "all" or (isinstance(calls, list) and n in calls) or ("fail_from" in f and n >= f["fail_from"]):
                return True
        return False

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        n = self.counts[tool] = self.counts.get(tool, 0) + 1
        if self._should_fail(tool, n):
            self.injected.append({"tool": tool, "call": n})
            raise ToolError("TOOL_FAILURE", f"injected failure ({tool} call {n})")
        return await self.inner.call(tool, arguments)


def build_services() -> dict[str, ToolService]:
    """One official-corpus service and one with the synthetic prompt-injection fixture added."""
    official = ToolService(data_dir=DATA)
    chunks = load_policy_chunks() + [injection_chunk()]
    injected_retriever = PolicyRetriever(chunks, semantic=SemanticIndex(chunks, persist_dir=PROJECT_ROOT / "outputs" / "policy_index_eval"))
    return {"official": official, "official+injection": ToolService(data_dir=DATA, retriever=injected_retriever, detector=official.detector)}


def _find_item(session: ReviewSession, text: str):
    for it in session.items:
        f = it.finding
        if f.text == text or (f.start < session.record_text.index(text) + len(text) and session.record_text.index(text) < f.end):
            return it
    return None


def apply_review(session: ReviewSession, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply the scenario's scripted reviewer actions; record what was accepted or refused."""
    log = []
    expected_texts = {o["text"] for o in scenario["outcomes"]}
    for step in scenario["review"]:
        action = step["action"]
        try:
            if action == "reject_unexpected":
                for it in session.items:
                    if not any(t in it.finding.text or it.finding.text in t for t in expected_texts):
                        session.reject(it.finding.finding_id, REVIEWER, step.get("reason"))
                        log.append({"action": "reject", "finding_id": it.finding.finding_id, "ok": True})
                continue
            if action == "acknowledge_flags":
                pending = [e for e in session.session_escalations if e.escalation_id not in session.acknowledged()]
                for e in pending:
                    session.acknowledge_escalation(e.escalation_id, REVIEWER, step["reason"])
                log.append({"action": action, "acknowledged": [e.escalation_id for e in pending], "ok": bool(pending)})
                continue
            if action == "add_redaction":
                occurrences = session.find_occurrences(step["text"])
                if not occurrences:
                    raise ReviewError(f"text {step['text']!r} not found in the original record")
                for start, end in occurrences:
                    event = session.add_manual_redaction(REVIEWER, start, end, step["category"], step["reason"])
                    session.confirm_manual_redaction(event.payload["manual_id"], REVIEWER)
                log.append({"action": action, "text": step["text"], "spans": occurrences, "ok": True})
                continue
            item = _find_item(session, step["text"])
            if item is None:
                log.append({"action": action, "text": step["text"], "ok": False, "error": "no detected finding to act on"})
                continue
            fid = item.finding.finding_id
            if action == "approve":
                session.approve(fid, REVIEWER)
            elif action == "reject":
                session.reject(fid, REVIEWER, step.get("reason"))
            elif action in ("modify", "approve_or_modify", "modify_substring"):
                state = session.item_state(fid)
                if action == "approve_or_modify" and state.recommendation.action.value == "REDACT":
                    session.approve(fid, REVIEWER)
                    action = "approve"
                else:
                    target = step.get("substring", step["text"])
                    start = session.record_text.index(target)
                    span = RedactionSpan(start=start, end=start + len(target), expected_text=target,
                                         replacement=f"[REDACTED: {item.finding.category.value}]")
                    session.propose_modification(fid, REVIEWER, [span], step["reason"])
                    session.confirm_modification(fid, REVIEWER)
                    action = "modify"
            log.append({"action": action, "finding_id": fid, "ok": True})
        except ReviewError as exc:
            log.append({"action": action, "text": step.get("text"), "ok": False, "error": str(exc)})
    return log


def _summarize(state: dict[str, Any], session: ReviewSession, executor: FaultInjectingExecutor, review_log, finalization,
               elapsed_s: float, derivative_text: str | None) -> dict[str, Any]:
    assessments = {a.finding_id: a for a in state["qa"].assessments} if state.get("qa") else {}
    tots = {t.finding_id: t for t in state.get("tot_results", [])}
    recs = {r.finding_id: r for r in state.get("recommendations", [])}
    findings = []
    for f in state.get("findings", []):
        rec = recs.get(f.finding_id)
        t = tots.get(f.finding_id)
        findings.append({
            "finding_id": f.finding_id, "category": f.category.value, "text": f.text, "start": f.start, "end": f.end, "confidence": f.confidence,
            "evidence_decision": assessments[f.finding_id].decision.value if f.finding_id in assessments else None,
            "action": rec.action.value if rec else None,
            "citations": [{"chunk_id": r.chunk_id, "citation": r.citation, "source_url": r.source_url} for r in (rec.policy_references if rec else [])],
            "rationale": rec.rationale if rec else None,
            "tot": None if t is None else {
                "outcome": t.outcome, "action": t.action, "selected": t.selected_node_id, "selected_score": t.selected_score,
                "depth_reached": t.depth_reached, "tool_calls_used": t.tool_calls_used, "targeted_retrieval": t.targeted_retrieval_used,
                "budget_events": t.budget_events, "reason": t.reason, "unresolved": t.unresolved,
                "nodes": [{"node_id": n.node_id, "parent_id": n.parent_id, "depth": n.depth, "stage": n.stage, "action": n.action,
                           "provision_type": n.provision_type, "status": n.status, "terminal": n.terminal,
                           "cited_chunk_ids": n.cited_chunk_ids, "evidence_ids": [c.chunk_id for c in n.evidence],
                           "evidence_sources": [c.source_url for c in n.evidence],
                           "assumptions": [{"key": a.key, "kind": a.kind, "status": a.status} for a in n.assumptions],
                           "score": n.score.model_dump() if n.score else None} for n in t.nodes],
                "pruned": [p.model_dump() for p in t.pruned]},
        })
    calls = state.get("tool_calls", [])
    return {
        "completion": getattr(state.get("completion"), "value", None), "route": state.get("route"), "transitions": state.get("transitions", []),
        "step_count": state.get("step_count"), "max_steps": state["config"].max_steps,
        "record_analysis_status": state["record_analysis"].status if state.get("record_analysis") else None,
        "findings": findings, "escalations": state.get("escalations", []),
        "qa_invalid_citations": state["qa"].invalid_citations if state.get("qa") else [],
        "qa_possible_missed": state["qa"].possible_missed if state.get("qa") else [],
        "tool_calls": {"total": len(calls), "by_tool": _count(t.tool for t in calls), "by_status": _count(t.status for t in calls),
                       "by_agent": _count(t.agent for t in calls)},
        "tool_call_log": [t.model_dump(mode="json") for t in calls],
        "injected_faults": executor.injected, "llm_calls": 0,
        "review": {"actions": review_log, "statuses": {s.finding_id: s.status.value for s in session.states()},
                   "finalized": finalization.finalized, "blocked_reasons": finalization.reasons,
                   "events": [e.action for e in session.events], "event_log_integrity": session.verify_chain()},
        "derivative_text": derivative_text, "elapsed_s": round(elapsed_s, 3),
        "state_checks": {
            "step_count_matches_transitions": state.get("step_count") == len(state.get("transitions", [])),
            "within_step_limit": state.get("step_count", 0) <= state["config"].max_steps,
            "tool_agents_known": all(t.agent in {"record_analysis", "policy_research", "compliance_qa"} for t in calls),
            "paused_for_human": getattr(state.get("completion"), "value", None) == "AWAITING_HUMAN_REVIEW" and not state.get("disclosure_authorized"),
            "evidence_metadata_complete": all(c.source_url and c.title and c.chunk_id for e in state.get("evidence", []) for c in e.passages),
        },
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


async def run_scenario(scenario: dict[str, Any], config_name: str, services: dict[str, ToolService], derivative_dir: Path) -> dict[str, Any]:
    case = next(c for c in load_review_cases(DATA) if c.request.request_id == scenario["request_id"])
    record_path = DATA / "synthetic_records" / f"record_{scenario['record_id'][-3:]}.txt"
    before = hashlib.sha256(record_path.read_bytes()).hexdigest()
    executor = FaultInjectingExecutor(LocalToolExecutor(services[scenario["corpus"]]), scenario.get("faults", []))
    orchestrator = Orchestrator(executor, WorkflowConfig(enable_tot=config_name == "B"))
    started = time.perf_counter()
    error = None
    try:
        run = await asyncio.wait_for(start(orchestrator, case.request, case.record, thread_id=f"eval-{config_name}-{scenario['id']}"),
                                     timeout=SCENARIO_TIMEOUT_S)
    except Exception as exc:  # recorded as a workflow failure, never hidden
        return {"scenario": scenario["id"], "config": config_name, "error": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.perf_counter() - started, 3)}
    elapsed = time.perf_counter() - started
    session = ReviewSession.from_workflow(run.state, record_path, actor="orchestrator")
    calls_before_review = sum(executor.counts.values())
    review_log = apply_review(session, scenario)
    finalization = session.finalize(REVIEWER, authorize=True, output_dir=derivative_dir / config_name)
    derivative = Path(finalization.derivative_path).read_text(encoding="utf-8") if finalization.finalized else None
    result = _summarize(run.state, session, executor, review_log, finalization, elapsed, derivative)
    result.update({"scenario": scenario["id"], "config": config_name, "error": error,
                   "record_unchanged": hashlib.sha256(record_path.read_bytes()).hexdigest() == before,
                   "tool_calls_during_review": sum(executor.counts.values()) - calls_before_review})
    return result


async def run_all(scenarios: list[dict[str, Any]], configs=("A", "B"), derivative_dir: Path | None = None) -> list[dict[str, Any]]:
    services = build_services()
    derivative_dir = derivative_dir or PROJECT_ROOT / "outputs" / "eval_derivatives"
    results = []
    for config_name in configs:
        for s in scenarios:
            results.append(await run_scenario(s, config_name, services, derivative_dir))
    return results
