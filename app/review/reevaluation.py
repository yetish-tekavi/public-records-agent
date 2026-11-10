"""Reviewer-requested re-evaluation of one finding, using the existing Orchestrator's agents.

Called only when the reviewer explicitly asks (never automatically after a rejection). Reviewer
feedback enters as structured input: factual confirmations can resolve FACTUAL ToT conditions,
but nothing a reviewer says can satisfy a LEGAL condition; that still requires retrieved text.
"""

from __future__ import annotations

from typing import Any

from app.review.session import ItemStatus, ReevaluationRequest, ReviewError, ReviewSession
from app.schemas import RedactionAction, RedactionProposal, RedactionRecommendation
from app.tools.service import ToolError
from app.workflows import evidence as ev
from app.workflows.graph import Orchestrator, recommendation_from_tot
from app.workflows.state import CategoryEvidence, EvidenceDecision


def _compare(before: RedactionRecommendation, after: RedactionRecommendation, before_score: int | None, after_score: int | None) -> dict[str, Any]:
    b_ids = [r.chunk_id for r in before.policy_references]
    a_ids = [r.chunk_id for r in after.policy_references]
    return {
        "action_before": before.action.value, "action_after": after.action.value,
        "action_changed": before.action != after.action,
        "citations_added": [c for c in a_ids if c not in b_ids], "citations_removed": [c for c in b_ids if c not in a_ids],
        "tot_score_before": before_score, "tot_score_after": after_score,
    }


async def reevaluate(orchestrator: Orchestrator, session: ReviewSession, request: ReevaluationRequest) -> dict[str, Any]:
    """Request, run and record a bounded re-evaluation. Returns the recorded outcome payload."""
    event = session.request_reevaluation(request)
    if event.action == "MANUAL_RESOLUTION_REQUIRED":
        return {"status": "MANUAL_RESOLUTION_REQUIRED", "reason": event.payload["reason"]}

    state = session.item_state(request.finding_id)
    finding = session.item(request.finding_id).finding
    attempt = event.payload["attempt"]
    # Reviewer feedback accumulates across attempts: earlier factual answers stand unless re-answered.
    facts: dict[str, str] = {}
    for e in state.history:
        if e.action == "REEVALUATION_REQUESTED":
            facts.update(e.payload["request"].get("factual_confirmations", {}))
    previous = state.recommendation
    previous_score = state.tot_result.selected_score if state.tot_result else None
    evidence = list(state.evidence)
    retrieval_performed, retrieval_query = False, None
    try:
        # Retrieve more evidence only when the reviewer asks for it or there is none to work with.
        if request.additional_query or state.evidence_status == "MISSING":
            retrieval_query = request.additional_query or f"{finding.category.value.lower().replace('_', ' ')} personal information exempt redaction"
            found = await orchestrator.policy_agent.targeted_search(
                finding.category, retrieval_query, CategoryEvidence(category=finding.category, status="FOUND", passages=evidence))
            evidence, retrieval_performed = found.passages, True

        assessment = ev.assess_finding(finding, evidence, session.record_text)
        rid = f"{previous.recommendation_id}.re{attempt}"
        tot_result, method = None, "STANDARD"
        if assessment.decision in (EvidenceDecision.AMBIGUOUS, EvidenceDecision.CONFLICTING):
            tot_result, _ = await orchestrator.run_tot(finding, session.record_text, evidence, orchestrator.config.tot, facts)
            recommendation, method = recommendation_from_tot(finding, assessment.decision.value, tot_result, rid), "TOT"
        elif assessment.decision is EvidenceDecision.CLEAR:
            result = await orchestrator.qa_agent.tools.call("propose_redactions", {
                "record_id": session.record_id, "findings": [finding.model_dump(mode="json")],
                "policy_chunk_ids": [c.chunk_id for c in evidence][:20]})
            [recommendation] = RedactionProposal.model_validate(result).recommendations
            refs = {r.chunk_id: r.excerpt for r in recommendation.policy_references}
            checks = await orchestrator.qa_agent.validate([{"chunk_id": k, "excerpt": v} for k, v in refs.items()])
            if any(not c.exists or c.excerpt_matches is False for c in checks):
                recommendation = recommendation.model_copy(update={"action": RedactionAction.HUMAN_REVIEW_REQUIRED,
                                                                   "rationale": "QA could not validate a citation. " + recommendation.rationale})
            recommendation = recommendation.model_copy(update={"recommendation_id": rid})
        else:
            recommendation = RedactionRecommendation(
                recommendation_id=rid, finding_id=finding.finding_id, action=RedactionAction.HUMAN_REVIEW_REQUIRED,
                rationale=f"Insufficient policy evidence after re-evaluation: no retrieved passage names {finding.category.value}. Human review required.",
                detection_confidence=finding.confidence, open_questions=["Which provision, if any, applies to this information?"])

        recommendation = recommendation.model_copy(update={
            "rationale": f"Re-evaluation {attempt} (requested by {request.reviewer}: {request.reason}). {recommendation.rationale}"})
        tool_calls = [t.model_dump(mode="json") for t in orchestrator.policy_agent.tools.take() + orchestrator.qa_agent.tools.take()]
        outcome = {
            "attempt": attempt, "reviewer_feedback": request.model_dump(), "factual_confirmations_applied": facts,
            "previous_recommendation": previous.model_dump(mode="json"),
            "recommendation": recommendation.model_dump(mode="json"), "reasoning_method": method,
            "evidence_status": {"CLEAR": "SUPPORTED"}.get(assessment.decision.value, assessment.decision.value),
            "assessment_reasons": assessment.reasons, "evidence": [c.model_dump(mode="json") for c in evidence],
            "retrieval_performed": retrieval_performed, "retrieval_query": retrieval_query,
            "tot_result": tot_result.model_dump(mode="json") if tot_result else None,
            "comparison": _compare(previous, recommendation, previous_score, tot_result.selected_score if tot_result else None),
            "tool_calls": tool_calls,
        }
    except (ToolError, ReviewError) as exc:
        outcome = {"attempt": attempt, "error": f"{type(exc).__name__}: {exc}",
                   "tool_calls": [t.model_dump(mode="json") for t in orchestrator.policy_agent.tools.take() + orchestrator.qa_agent.tools.take()]}
    session.record_reevaluation(request.finding_id, outcome)
    return outcome
