"""Reviewer-added redactions for text the detector missed, and the QA flags that prompt them."""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from app.data_loader import PROJECT_ROOT, load_review_cases
from app.review.report import build_audit_report
from app.review.session import ReviewError, ReviewSession
from app.schemas import PiiDetectionResult
from app.tools.service import ToolService
from app.workflows.evidence import possibly_missed
from app.workflows.graph import Orchestrator
from app.workflows.runner import start
from tests.m4_support import ScriptedExecutor, StubRetriever
from tests.m5_support import isolated_outputs, make_session  # noqa: F401

REVIEWER = "Test Reviewer"
M6 = PROJECT_ROOT / "data" / "milestone_6"


# --- QA sweep -------------------------------------------------------------------------------


@pytest.mark.parametrize("text, flagged", [
    ("You can reach me at 5550142 after six.", "5550142"),
    ("Call 555-0142 tonight.", "555-0142"),
    ("the caller said: six zero nine, five five five, zero one three one.", "six zero nine, five five five, zero one three one"),
])
def test_sweep_flags_numbers_the_detector_misses(text, flagged):
    assert any(flagged in f for f in possibly_missed(text, []))


@pytest.mark.parametrize("text", [
    "Purchase order PO-2026-555-0123 covers road salt deliveries.",
    "Work order WO-2026-0193 remains open.",
    "The vote was five to two.",
])
def test_sweep_does_not_flag_codes_or_ordinary_words(text):
    assert possibly_missed(text, []) == []


# --- session actions ------------------------------------------------------------------------


def _session(policy_chunks, request_id="OPRA-TEST-201"):
    session, *_ = make_session(policy_chunks, request_id)
    return session


def test_added_redaction_must_be_confirmed_then_is_applied(policy_chunks, isolated_outputs):
    session = _session(policy_chunks)
    record_path = session.record_path
    before = hashlib.sha256(open(record_path, "rb").read()).hexdigest()
    session.approve("TEST-201-F01", REVIEWER)
    start = session.record_text.index("resident")
    event = session.add_manual_redaction(REVIEWER, start, start + len("resident"), "OTHER", "Role reveals identity in context")
    manual_id = event.payload["manual_id"]
    blocked = session.finalize(REVIEWER, authorize=True)
    assert not blocked.finalized and any(f"{manual_id}" in r and "not confirmed" in r for r in blocked.reasons)

    session.confirm_manual_redaction(manual_id, REVIEWER)
    result = session.finalize(REVIEWER, authorize=True)
    assert result.finalized
    text = open(result.derivative_path, encoding="utf-8").read()
    assert "the [REDACTED: OTHER] stated" in text and "[REDACTED: PHONE_NUMBER]" in text
    assert hashlib.sha256(open(record_path, "rb").read()).hexdigest() == before


def test_withdrawn_redaction_is_not_applied_but_stays_in_history(policy_chunks):
    session = _session(policy_chunks)
    start = session.record_text.index("interaction")
    manual_id = session.add_manual_redaction(REVIEWER, start, start + 11, "OTHER", "mistake").payload["manual_id"]
    session.withdraw_manual_redaction(manual_id, REVIEWER, "added by mistake")
    assert [m.status for m in session.manual_redactions()] == ["WITHDRAWN"]
    assert not any(s.source == "reviewer_addition" for s in session.redaction_plan())
    assert [e.action for e in session.events][-2:] == ["MANUAL_REDACTION_PROPOSED", "MANUAL_REDACTION_WITHDRAWN"]


@pytest.mark.parametrize("kwargs, message", [
    ({"start": 0, "end": 5, "category": "OTHER", "reason": ""}, "reason"),
    ({"start": 0, "end": 5, "category": "", "reason": "r"}, "category"),
    ({"start": 50, "end": 5000, "category": "OTHER", "reason": "r"}, "outside"),
])
def test_added_redaction_is_validated(policy_chunks, kwargs, message):
    session = _session(policy_chunks)
    with pytest.raises(ReviewError, match=message):
        session.add_manual_redaction(REVIEWER, **kwargs)
    with pytest.raises(ReviewError, match="reviewer"):
        session.add_manual_redaction("", 0, 5, "OTHER", "r")


def test_no_additions_after_finalization(policy_chunks, isolated_outputs):
    session = _session(policy_chunks)
    session.approve("TEST-201-F01", REVIEWER)
    assert session.finalize(REVIEWER, authorize=True).finalized
    with pytest.raises(ReviewError, match="finalized"):
        session.add_manual_redaction(REVIEWER, 0, 5, "OTHER", "late")


def test_added_span_overlapping_an_approved_finding_is_merged(policy_chunks, isolated_outputs):
    session = _session(policy_chunks)
    session.approve("TEST-201-F01", REVIEWER)
    start = session.record_text.index("number is 609")
    manual_id = session.add_manual_redaction(REVIEWER, start, start + len("number is 609"), "PHONE_NUMBER", "include the label").payload["manual_id"]
    session.confirm_manual_redaction(manual_id, REVIEWER)
    result = session.finalize(REVIEWER, authorize=True)
    # Same replacement on both spans, so the merged span keeps it (only differing replacements become "[REDACTED]").
    assert result.finalized and result.redaction.text.endswith("My personal phone [REDACTED: PHONE_NUMBER].")
    assert len(result.redaction.applied) == 1


def test_find_occurrences_and_repeated_value_guard(policy_chunks, isolated_outputs, tmp_path):
    session = _session(policy_chunks)
    assert session.find_occurrences("the") == [(i, i + 3) for i in range(len(session.record_text)) if session.record_text.startswith("the", i)]
    assert session.find_occurrences("not in this record") == []


def test_audit_report_records_reviewer_additions_without_policy_claim(policy_chunks):
    session = _session(policy_chunks)
    start = session.record_text.index("resident")
    session.add_manual_redaction(REVIEWER, start, start + 8, "OTHER", "context")
    [entry] = build_audit_report(session)["reviewer_added_redactions"]
    assert entry["category"] == "OTHER" and entry["proposed_by"] == REVIEWER and entry["status"] == "PROPOSED"
    added = next(e for e in session.events if e.action == "MANUAL_REDACTION_PROPOSED")
    assert "no policy basis is claimed" in added.payload["note"]
    assert session.verify_chain()


# --- end to end: undetected number -> QA flag -> reviewer adds redaction ---------------------------


class NoFindings:
    def detect(self, text, record_id=None):
        return PiiDetectionResult(record_id=record_id, detector="stub-misses-everything", score_threshold=0.4, findings=[])


def test_missed_number_is_flagged_and_reviewer_can_redact_it(policy_chunks, isolated_outputs):
    case = next(c for c in load_review_cases(M6) if c.record.record_id == "TEST-605")
    service = ToolService(data_dir=M6, retriever=StubRetriever(policy_chunks), detector=NoFindings())
    state = asyncio.run(start(Orchestrator(ScriptedExecutor(service)), case.request, case.record, thread_id="m6-manual")).state
    assert state["findings"] == [] and any("5550142" in e for e in state["escalations"])
    session = ReviewSession.from_workflow(state, M6 / "synthetic_records" / "record_605.txt")
    assert not session.finalize(REVIEWER, authorize=True).finalized  # unacknowledged QA flag blocks finalization

    [esc] = session.session_escalations
    session.acknowledge_escalation(esc.escalation_id, REVIEWER, "Confirmed: a personal number the detector missed.")
    [(s, e)] = session.find_occurrences("5550142")
    manual_id = session.add_manual_redaction(REVIEWER, s, e, "PHONE_NUMBER", "Personal callback number").payload["manual_id"]
    session.confirm_manual_redaction(manual_id, REVIEWER)
    result = session.finalize(REVIEWER, authorize=True)
    assert result.finalized and "5550142" not in result.redaction.text


# --- UI ------------------------------------------------------------------------------------------


def test_add_redaction_through_the_ui(policy_chunks, isolated_outputs):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    session = _session(policy_chunks)
    at = AppTest.from_file(str(PROJECT_ROOT / "app" / "review_app.py"), default_timeout=60)
    at.session_state["session"] = session
    at.session_state["reviewer"] = "UI Reviewer"
    at.run()
    at.text_input(key="manual-text").input("resident").run()
    at.selectbox(key="manual-category").select("OTHER").run()
    at.text_input(key="manual-reason").input("Role identifies the caller").run()
    at.button(key="manual-add").click().run()
    s = at.session_state["session"]
    [m] = s.manual_redactions()
    assert m.status == "PROPOSED" and m.span.expected_text == "resident"
    at.button(key=f"manual-confirm-{m.manual_id}").click().run()
    assert at.session_state["session"].manual_redactions()[0].status == "CONFIRMED"
