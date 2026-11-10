"""Structured audit report for a review session, built only from recorded session data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.review.session import ReviewSession, now


def build_audit_report(session: ReviewSession) -> dict[str, Any]:
    finalized = next((e for e in session.events if e.action == "FINALIZED"), None)
    findings = []
    for it in session.items:
        st = session.item_state(it.finding.finding_id)
        tot = st.tot_result
        findings.append({
            "finding": it.finding.model_dump(mode="json"),
            "position": {"start": it.finding.start, "end": it.finding.end},
            "initial": {
                "reasoning_method": it.reasoning_method, "evidence_status": it.evidence_status,
                "recommendation": it.recommendation.model_dump(mode="json"),
                "tot": _tot_summary(it.tot_result) if it.tot_result else None,
            },
            "current": {
                "status": st.status.value, "reasoning_method": st.reasoning_method, "evidence_status": st.evidence_status,
                "recommendation": st.recommendation.model_dump(mode="json"),
                "tot": _tot_summary(tot) if tot else None,
                "citations": [{"citation": r.citation, "page": r.page, "source_url": r.source_url, "chunk_id": r.chunk_id}
                              for r in st.recommendation.policy_references],
            },
            "reevaluation_attempts": st.reevaluation_attempts,
            "reevaluations": [e.payload | {"seq": e.seq, "timestamp": e.timestamp}
                              for e in st.history if e.action in ("REEVALUATION_COMPLETED", "REEVALUATION_FAILED")],
            "reviewer_decisions": [{"seq": e.seq, "timestamp": e.timestamp, "reviewer": e.actor, "action": e.action,
                                    "detail": {k: v for k, v in e.payload.items() if k not in ("previous_recommendation",)}}
                                   for e in st.history if e.action not in ("REEVALUATION_COMPLETED", "REEVALUATION_FAILED")],
        })
    return {
        "report_type": "public-records review audit (synthetic data)",
        "generated_at": now(),
        "session_id": session.session_id,
        "request_id": session.request.request_id,
        "record_id": session.record_id,
        "source": {"record_path": session.record_path, "text_sha256": session.source_text_sha256, "file_sha256": session.source_file_sha256},
        "workflow": session.workflow,
        "findings": findings,
        "session_escalations": [e.model_dump() for e in session.session_escalations],
        "acknowledged_escalations": sorted(session.acknowledged()),
        "final_redaction_plan": finalized.payload["plan"] if finalized else [s.model_dump() for s in session.redaction_plan()],
        "finalization": ({"finalized": True, "authorized_by": finalized.payload["authorized_by"], "timestamp": finalized.timestamp,
                          "derivative_path": finalized.payload["derivative_path"], "derivative_sha256": finalized.payload["derivative_sha256"],
                          "published": False}
                         if finalized else {"finalized": False,
                                            "blocked_attempts": [e.payload | {"timestamp": e.timestamp, "reviewer": e.actor}
                                                                 for e in session.events if e.action == "FINALIZATION_BLOCKED"]}),
        "events": [e.model_dump() for e in session.events],
        "event_log_integrity": session.verify_chain(),
    }


def _tot_summary(tot) -> dict[str, Any]:
    return {
        "outcome": tot.outcome, "action": tot.action, "selected_node_id": tot.selected_node_id, "selected_score": tot.selected_score,
        "reason": tot.reason, "depth_reached": tot.depth_reached, "tool_calls_used": tot.tool_calls_used,
        "targeted_retrieval_used": tot.targeted_retrieval_used, "budget_events": tot.budget_events, "unresolved": tot.unresolved,
        "branches": [{"node_id": n.node_id, "parent_id": n.parent_id, "depth": n.depth, "stage": n.stage, "action": n.action,
                      "provision_type": n.provision_type, "status": n.status, "cited_chunk_ids": n.cited_chunk_ids,
                      "assumptions": [{"key": a.key, "status": a.status, "evidence": a.evidence} for a in n.assumptions],
                      "score": n.score.model_dump() if n.score else None} for n in tot.nodes],
        "pruned": [p.model_dump() for p in tot.pruned],
        "score_note": "Scores measure support from retrieved text and record facts; they are not probabilities of legal correctness.",
    }


def write_audit_report(session: ReviewSession, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_audit_report(session), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
