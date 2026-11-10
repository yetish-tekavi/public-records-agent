"""Review session: decisions, re-evaluation, limits, finalization controls, immutability, audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil

import pytest

from app.review.reevaluation import reevaluate
from app.review.report import build_audit_report
from app.review.session import ItemStatus, ReevaluationRequest, ReviewError, ReviewSession
from app.schemas import RedactionAction
from tests.m5_support import RECORD_PATHS, isolated_outputs, make_session  # noqa: F401  (fixture import)

REVIEWER = "Test Reviewer"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- standard and ToT recommendations ------------------------------------------------------


def test_standard_recommendation_review_and_finalization(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    [state] = session.states()
    assert state.reasoning_method == "STANDARD" and state.evidence_status == "SUPPORTED"
    assert state.status is ItemStatus.PENDING and state.tot_result is None
    before = file_hash(RECORD_PATHS["OPRA-TEST-201"])

    session.approve("TEST-201-F01", REVIEWER)
    result = session.finalize(REVIEWER, authorize=True)
    assert result.finalized, result.reasons
    derivative = open(result.derivative_path, encoding="utf-8").read()
    assert derivative == "During the interaction, the resident stated: My personal phone number is [REDACTED: PHONE_NUMBER]."
    assert file_hash(RECORD_PATHS["OPRA-TEST-201"]) == before  # original never overwritten
    assert str(isolated_outputs) in result.derivative_path


def test_tot_recommendation_carries_actual_branch_data(policy_chunks):
    session, _, _, state = make_session(policy_chunks, "OPRA-TEST-202")
    [item_state] = session.states()
    assert item_state.reasoning_method == "TOT" and item_state.evidence_status == "AMBIGUOUS"
    assert item_state.tot_result == state["tot_results"][0]  # exactly what the workflow produced
    assert item_state.recommendation.action is RedactionAction.REDACT


def test_report_contains_no_fabricated_scores(policy_chunks):
    session, _, _, state = make_session(policy_chunks, "OPRA-TEST-202")
    report = build_audit_report(session)
    branches = report["findings"][0]["current"]["tot"]["branches"]
    actual = {n.node_id: (n.score.model_dump() if n.score else None) for n in state["tot_results"][0].nodes}
    assert {b["node_id"]: b["score"] for b in branches} == actual
    standard, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    assert build_audit_report(standard)["findings"][0]["current"]["tot"] is None


# --- approve / reject / modify -------------------------------------------------------------


def test_reject_records_reason_and_removes_span_from_plan(policy_chunks):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    session.reject("TEST-201-F01", REVIEWER, "Number belongs to a public office")
    state = session.item_state("TEST-201-F01")
    assert state.status is ItemStatus.REJECTED and state.last_decision["reason"] == "Number belongs to a public office"
    assert session.redaction_plan() == []


def test_rejection_does_not_trigger_automatic_reevaluation(policy_chunks):
    session, _, executor, _ = make_session(policy_chunks, "OPRA-TEST-202")
    calls_before = list(executor.calls)
    session.reject("TEST-202-F01", REVIEWER, "disagree")
    assert executor.calls == calls_before
    assert not any(e.action.startswith("REEVALUATION") for e in session.events)


def test_modification_requires_reason_valid_spans_and_confirmation(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-202")
    text = session.record_text
    start = text.index("Juniper Court")
    span = {"start": start, "end": start + len("Juniper Court"), "expected_text": "Juniper Court", "replacement": "[STREET]"}
    with pytest.raises(ReviewError, match="reason"):
        session.propose_modification("TEST-202-F01", REVIEWER, [span], "")
    with pytest.raises(ReviewError, match="expected"):
        session.propose_modification("TEST-202-F01", REVIEWER, [span | {"expected_text": "Elm Court"}], "street only")

    session.propose_modification("TEST-202-F01", REVIEWER, [span], "Redact the street name only")
    assert session.item_state("TEST-202-F01").status is ItemStatus.MODIFICATION_UNCONFIRMED
    assert not session.finalize(REVIEWER, authorize=True).finalized  # unconfirmed spans cannot be executed

    session.confirm_modification("TEST-202-F01", REVIEWER)
    result = session.finalize(REVIEWER, authorize=True)
    assert result.finalized
    assert "home at 9 [STREET]." in open(result.derivative_path, encoding="utf-8").read()


def test_approve_is_refused_without_a_supported_redaction(policy_chunks):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-203")
    with pytest.raises(ReviewError, match="no redaction recommendation"):
        session.approve("TEST-203-F01", REVIEWER)


def test_actions_require_a_named_reviewer(policy_chunks):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    with pytest.raises(ReviewError, match="reviewer"):
        session.approve("TEST-201-F01", "  ")


# --- re-evaluation ---------------------------------------------------------------------------


def test_reviewer_requested_reevaluation_updates_and_compares(policy_chunks):
    session, orchestrator, *_ = make_session(policy_chunks, "OPRA-TEST-202")
    before = session.item_state("TEST-202-F01").recommendation
    outcome = asyncio.run(reevaluate(orchestrator, session, ReevaluationRequest(
        finding_id="TEST-202-F01", reviewer=REVIEWER, reason="Self-identification only; cannot confirm covered status.",
        factual_confirmations={"covered_person": "unconfirmed"})))
    after = session.item_state("TEST-202-F01")
    assert outcome["comparison"]["action_before"] == "REDACT" and outcome["comparison"]["action_after"] == "HUMAN_REVIEW_REQUIRED"
    assert after.recommendation.action is RedactionAction.HUMAN_REVIEW_REQUIRED and after.status is ItemStatus.ESCALATED
    assert after.reasoning_method == "TOT" and after.tot_result is not None
    requested = next(e for e in session.events if e.action == "REEVALUATION_REQUESTED")
    assert requested.payload["previous_recommendation"] == before.model_dump(mode="json")  # previous preserved
    assert session.item("TEST-202-F01").recommendation == before  # original snapshot untouched


def test_reviewer_facts_are_factual_input_only(policy_chunks):
    """Confirming a fact cannot stand in for missing statutory text."""
    session, orchestrator, *_ = make_session(policy_chunks, "OPRA-TEST-203")
    outcome = asyncio.run(reevaluate(orchestrator, session, ReevaluationRequest(
        finding_id="TEST-203-F01", reviewer=REVIEWER, reason="I believe IP addresses are confidential.",
        factual_confirmations={"covered_person": "confirmed"})))
    assert outcome["recommendation"]["action"] == "HUMAN_REVIEW_REQUIRED"
    assert outcome["recommendation"]["policy_references"] == []


def test_feedback_accumulates_across_attempts(policy_chunks):
    session, orchestrator, *_ = make_session(policy_chunks, "OPRA-TEST-202")
    asyncio.run(reevaluate(orchestrator, session, ReevaluationRequest(
        finding_id="TEST-202-F01", reviewer=REVIEWER, reason="cannot confirm", factual_confirmations={"covered_person": "unconfirmed"})))
    second = asyncio.run(reevaluate(orchestrator, session, ReevaluationRequest(finding_id="TEST-202-F01", reviewer=REVIEWER, reason="check again")))
    assert second["factual_confirmations_applied"] == {"covered_person": "unconfirmed"}
    assert second["recommendation"]["action"] == "HUMAN_REVIEW_REQUIRED"


def test_missing_evidence_remains_escalated_after_reevaluation(policy_chunks):
    session, orchestrator, executor, _ = make_session(policy_chunks, "OPRA-TEST-203")
    outcome = asyncio.run(reevaluate(orchestrator, session, ReevaluationRequest(
        finding_id="TEST-203-F01", reviewer=REVIEWER, reason="Look for any IP address rule.", additional_query="IP address logs privacy")))
    assert outcome["retrieval_performed"] and outcome["retrieval_query"] == "IP address logs privacy"
    assert outcome["evidence_status"] == "MISSING"
    assert session.item_state("TEST-203-F01").status is ItemStatus.ESCALATED


def test_reevaluation_limit_requires_manual_resolution(policy_chunks):
    session, orchestrator, *_ = make_session(policy_chunks, "OPRA-TEST-204")
    request = ReevaluationRequest(finding_id="TEST-204-F01", reviewer=REVIEWER, reason="again")
    asyncio.run(reevaluate(orchestrator, session, request))
    asyncio.run(reevaluate(orchestrator, session, request))
    third = asyncio.run(reevaluate(orchestrator, session, request))
    assert third["status"] == "MANUAL_RESOLUTION_REQUIRED"
    state = session.item_state("TEST-204-F01")
    assert state.status is ItemStatus.MANUAL_RESOLUTION_REQUIRED and state.reevaluation_attempts == 2
    assert sum(e.action == "REEVALUATION_COMPLETED" for e in session.events) == 2
    session.reject("TEST-204-F01", REVIEWER, "resolved manually")  # a human can still resolve it
    assert session.item_state("TEST-204-F01").status is ItemStatus.REJECTED


# --- finalization controls ----------------------------------------------------------------


def test_unauthorized_finalization_is_blocked_and_recorded(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    session.approve("TEST-201-F01", REVIEWER)
    no_auth = session.finalize(REVIEWER, authorize=False)
    no_name = session.finalize("", authorize=True)
    assert not no_auth.finalized and any("not authorized" in r for r in no_auth.reasons)
    assert not no_name.finalized and any("named reviewer" in r for r in no_name.reasons)
    assert [e.action for e in session.events].count("FINALIZATION_BLOCKED") == 2
    assert not (isolated_outputs / "derivatives").exists()


def test_finalization_with_unresolved_findings_is_blocked(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-203")
    result = session.finalize(REVIEWER, authorize=True)
    assert not result.finalized and any("TEST-203-F01 is not resolved (status ESCALATED)" in r for r in result.reasons)


def test_unacknowledged_session_escalation_blocks_finalization(policy_chunks, isolated_outputs):
    _, _, _, state = make_session(policy_chunks, "OPRA-TEST-201")
    state = {**state, "escalations": ["QA flag: Possible undetected phone-like number at [0:12]"]}
    session = ReviewSession.from_workflow(state, RECORD_PATHS["OPRA-TEST-201"])
    session.approve("TEST-201-F01", REVIEWER)
    assert any("escalation E1" in r for r in session.finalize(REVIEWER, authorize=True).reasons)
    session.acknowledge_escalation("E1", REVIEWER, "Checked: not personal data")
    assert session.finalize(REVIEWER, authorize=True).finalized


def test_source_change_after_analysis_blocks_finalization(policy_chunks, isolated_outputs, tmp_path):
    _, _, _, state = make_session(policy_chunks, "OPRA-TEST-201")
    copy = tmp_path / "record_201.txt"
    shutil.copy(RECORD_PATHS["OPRA-TEST-201"], copy)
    session = ReviewSession.from_workflow(state, copy)
    session.approve("TEST-201-F01", REVIEWER)
    copy.write_text(copy.read_text(encoding="utf-8") + "\nEdited later.\n", encoding="utf-8")
    assert any("changed after analysis" in r for r in session.finalize(REVIEWER, authorize=True).reasons)


def test_finalized_session_cannot_be_changed(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    session.approve("TEST-201-F01", REVIEWER)
    assert session.finalize(REVIEWER, authorize=True).finalized
    with pytest.raises(ReviewError, match="finalized"):
        session.reject("TEST-201-F01", REVIEWER)
    assert not session.finalize(REVIEWER, authorize=True).finalized


# --- audit -------------------------------------------------------------------------------------


def test_audit_report_is_complete(policy_chunks, isolated_outputs):
    session, orchestrator, *_ = make_session(policy_chunks, "OPRA-TEST-202")
    asyncio.run(reevaluate(orchestrator, session, ReevaluationRequest(
        finding_id="TEST-202-F01", reviewer=REVIEWER, reason="cannot confirm", factual_confirmations={"covered_person": "unconfirmed"})))
    session.reject("TEST-202-F01", REVIEWER, "keep the street address; covered status not shown")
    assert session.finalize(REVIEWER, authorize=True).finalized
    report = json.loads(json.dumps(build_audit_report(session)))
    assert {"request_id", "record_id", "source", "workflow", "findings", "final_redaction_plan", "finalization", "events",
            "event_log_integrity"} <= set(report)
    assert report["source"]["text_sha256"] == session.source_text_sha256
    f = report["findings"][0]
    assert f["position"] == {"start": session.item("TEST-202-F01").finding.start, "end": session.item("TEST-202-F01").finding.end}
    assert f["initial"]["tot"]["selected_node_id"] and f["current"]["tot"]["branches"]
    assert f["reevaluation_attempts"] == 1 and f["reevaluations"][0]["comparison"]["action_changed"] is True
    assert [d["action"] for d in f["reviewer_decisions"]] == ["REEVALUATION_REQUESTED", "REJECTED"]
    assert report["workflow"]["tool_calls"] and report["finalization"]["derivative_sha256"]
    assert report["finalization"]["published"] is False and report["event_log_integrity"] is True


def test_event_log_is_tamper_evident(policy_chunks, tmp_path):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    session.reject("TEST-201-F01", REVIEWER, "first decision")
    session.approve("TEST-201-F01", REVIEWER)
    path = session.save(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["events"][1]["payload"]["reason"] = "rewritten"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReviewError, match="integrity"):
        ReviewSession.load(path)
    assert [e.action for e in session.events] == ["SESSION_CREATED", "REJECTED", "APPROVED"]  # both decisions kept
