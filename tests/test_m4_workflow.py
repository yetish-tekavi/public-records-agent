"""LangGraph workflow: routing, structured state, retries, failures, limits, and human approval."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from pydantic import ValidationError

from app.schemas import RedactionAction
from app.tools.service import ToolService
from app.workflows.graph import Orchestrator
from app.workflows.runner import resume, start
from app.workflows.state import (
    AuditEvent,
    CompletionStatus,
    QAReport,
    RecordAnalysisResult,
    ToolCallRecord,
    WorkflowConfig,
)
from tests.conftest import GroundTruthDetector
from tests.m4_support import M4_CASES, M4_DATA, ScriptedExecutor, StubRetriever, write_case

STANDARD = ["orchestrator_start", "record_analysis", "policy_research", "evidence_merge", "compliance_qa", "evidence_decision"]


def service(policy_chunks, data_dir=M4_DATA, detector=None):
    return ToolService(data_dir=data_dir, retriever=StubRetriever(policy_chunks), detector=detector or GroundTruthDetector(data_dir=data_dir))


def run_case(executor, request_id=None, case=None, config=None):
    case = case or M4_CASES[request_id]
    orch = Orchestrator(executor, config or WorkflowConfig())
    return asyncio.run(start(orch, case.request, case.record, thread_id=case.request.request_id))


# --- routing for the four scenario types ------------------------------------------------


def test_clear_evidence_takes_standard_path_without_tot(policy_chunks):
    run = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-201")
    s = run.state
    assert s["transitions"] == STANDARD + ["finalize_recommendations"]
    assert s["route"] == "standard" and s["tot_results"] == []
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.REDACT and rec.policy_references[0].chunk_id == "opra_statute:0052"
    assert s["completion"] is CompletionStatus.AWAITING_HUMAN_REVIEW and s["disclosure_authorized"] is False


def test_ambiguous_evidence_activates_tot(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-202").state
    assert s["transitions"] == STANDARD + ["tot", "finalize_recommendations"]
    [tot] = s["tot_results"]
    assert tot.outcome == "SUPPORTED_RECOMMENDATION" and tot.selected_score >= 70
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.REDACT and rec.requires_human_approval
    assert any(q.startswith("Confirm the condition 'The person is a covered person") and "retired Superior Court judge" in q
               for q in rec.open_questions)


def test_missing_evidence_retries_once_then_escalates_without_citations(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-203").state
    assert s["transitions"] == STANDARD + ["targeted_retrieval", "evidence_merge", "compliance_qa", "evidence_decision", "finalize_recommendations"]
    assert s["retries"].missing_evidence == 1
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED and rec.policy_references == []
    assert "Insufficient policy evidence" in rec.rationale
    assert not any(token in rec.rationale for token in ("N.J.S.A", "47:1A", "§"))
    assert any("insufficient policy evidence" in e for e in s["escalations"])


def test_tot_failure_protection_rejects_unsupported_branch(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-204").state
    [tot] = s["tot_results"]
    assert tot.outcome == "ESCALATED"
    assert any("unsupported factual assumption" in p.reason for p in tot.pruned)
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED


def test_conflicting_evidence_activates_tot_and_blocks_redaction(policy_chunks, tmp_path):
    text = "For permits, call the Larkspur Township main line at (201) 555-0100 during business hours."
    data = write_case(tmp_path, "950", text, [("PHONE_NUMBER", "(201) 555-0100")])
    retriever = StubRetriever(policy_chunks, routes=[(("telephone",), ["opra_statute:0052", "opra_statute:0034"])])
    svc = ToolService(data_dir=data, retriever=retriever, detector=GroundTruthDetector(data_dir=data))
    from app.data_loader import load_review_cases
    [case] = load_review_cases(data)
    s = run_case(ScriptedExecutor(svc), case=case).state
    assert [a.decision.value for a in s["qa"].assessments] == ["CONFLICTING"]
    assert "tot" in s["transitions"]
    assert s["recommendations"][0].action is RedactionAction.HUMAN_REVIEW_REQUIRED


# --- structured state -------------------------------------------------------------------


def test_state_is_typed_and_records_every_tool_call(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-202").state
    assert isinstance(s["record_analysis"], RecordAnalysisResult) and isinstance(s["qa"], QAReport)
    assert all(isinstance(t, ToolCallRecord) for t in s["tool_calls"])
    assert {t.agent for t in s["tool_calls"]} == {"record_analysis", "policy_research", "compliance_qa"}
    assert all(isinstance(e, AuditEvent) for e in s["audit"])
    assert [e.step for e in s["audit"]] == list(range(1, len(s["audit"]) + 1))
    assert s["step_count"] == len(s["transitions"])


def test_original_record_is_not_modified(policy_chunks):
    path = M4_DATA / "synthetic_records" / "record_202.txt"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-202")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# --- failures, retries, limits --------------------------------------------------------------


def test_transient_record_analysis_failure_is_retried(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks), fail={"detect_pii": 1}), "OPRA-TEST-201").state
    assert s["record_analysis"].status == "OK" and s["record_analysis"].attempts == 2
    assert [t.status for t in s["tool_calls"] if t.tool == "detect_pii"] == ["error", "ok"]


def test_persistent_record_failure_is_bounded_and_escalated(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks), fail={"detect_pii": 99}), "OPRA-TEST-201").state
    assert s["transitions"] == ["orchestrator_start", "record_analysis", "error_recovery", "record_analysis", "error_recovery",
                                "finalize_recommendations"]
    assert s["findings"] == [] and s["recommendations"] == []  # nothing fabricated
    assert any("reviewed manually" in e for e in s["escalations"])
    assert s["completion"] is CompletionStatus.AWAITING_HUMAN_REVIEW


def test_tool_timeout_is_handled(policy_chunks):
    cfg = WorkflowConfig(tool_timeout_s=0.2, max_record_attempts=1, max_error_recoveries=0)
    s = run_case(ScriptedExecutor(service(policy_chunks), delay={"detect_pii": 1.0}), "OPRA-TEST-201", config=cfg).state
    assert [t.status for t in s["tool_calls"]] == ["timeout"]
    assert s["record_analysis"].status == "FAILED"
    assert s["completion"] is CompletionStatus.AWAITING_HUMAN_REVIEW


def test_policy_tool_failure_becomes_missing_evidence_not_invented_policy(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks), fail={"search_policy": 99}), "OPRA-TEST-201").state
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED and rec.policy_references == []
    assert s["retries"].missing_evidence == 1


def test_step_limit_forces_human_review(policy_chunks):
    s = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-202", config=WorkflowConfig(max_steps=5)).state
    assert s["route"] == "human_review" and "tot" not in s["transitions"]
    assert any("Step limit 5" in e for e in s["escalations"])


def test_invalid_citation_is_vetoed_by_qa(policy_chunks):
    def all_invalid(arguments):
        return {"checks": [{"chunk_id": c["chunk_id"], "exists": False, "excerpt_matches": None, "section": None, "source_url": None}
                           for c in arguments["citations"]], "all_valid": False}

    s = run_case(ScriptedExecutor(service(policy_chunks), override={"validate_citations": all_invalid}), "OPRA-TEST-201").state
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED and "could not validate" in rec.rationale


# --- human approval -------------------------------------------------------------------------


@pytest.mark.parametrize("decision, completion, authorized", [
    ("approve", CompletionStatus.APPROVED_BY_HUMAN, True),
    ("reject", CompletionStatus.REJECTED_BY_HUMAN, False),
    ("return", CompletionStatus.RETURNED_FOR_REVISION, False),
])
def test_only_human_approval_authorizes_disclosure(policy_chunks, decision, completion, authorized):
    run = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-201")
    assert run.review_package["recommendations"] and run.state["disclosure_authorized"] is False
    done = asyncio.run(resume(run, {"decision": decision, "reviewer": "test-reviewer"}))
    assert done.state["completion"] is completion
    assert done.state["disclosure_authorized"] is authorized
    assert done.state["human_review"].reviewer == "test-reviewer"


def test_invalid_human_decision_is_refused(policy_chunks):
    run = run_case(ScriptedExecutor(service(policy_chunks)), "OPRA-TEST-201")
    with pytest.raises(ValidationError):
        asyncio.run(resume(run, {"decision": "release_now", "reviewer": "x"}))
    with pytest.raises(ValidationError):
        asyncio.run(resume(run, {"decision": "approve", "reviewer": ""}))
