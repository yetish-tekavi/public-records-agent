"""The three tools called independently (no LLM, no agent), including validation and failures."""

from __future__ import annotations

import json

import pytest

from app.tools.service import ToolError, ToolService
from tests.conftest import GroundTruthDetector


def error_code(fn, **kwargs) -> str:
    with pytest.raises(ToolError) as exc:
        fn(**kwargs)
    return exc.value.code


# --- search_policy ---------------------------------------------------------------


def test_search_policy_returns_cited_passages(tool_service):
    result = tool_service.search_policy(query="Is a driver license number confidential?", method="keyword")
    assert result.status.value == "SUFFICIENT"
    ref = result.passages[0].to_policy_reference()
    assert ref.source_url.startswith("https://") and ref.excerpt and ref.chunk_id


def test_search_policy_policy_not_found_is_a_result_not_an_error(tool_service):
    result = tool_service.search_policy(query="recipe for chocolate chip cookies", method="keyword")
    assert result.status.value == "INSUFFICIENT"
    assert result.passages == []


@pytest.mark.parametrize("args", [{"query": ""}, {"query": "x", "k": 0}, {"query": "x", "k": 11}, {"query": "x", "colour": "red"}, {"query": "x", "method": "magic"}])
def test_search_policy_rejects_invalid_input(tool_service, args):
    assert error_code(tool_service.search_policy, **args) == "INVALID_INPUT"


# --- detect_pii --------------------------------------------------------------------


def test_detect_pii_by_record_id(tool_service):
    result = tool_service.detect_pii(record_id="TEST-001")
    assert [f.category.value for f in result.findings] == ["PHONE_NUMBER", "DRIVERS_LICENSE_NUMBER"]


@pytest.mark.parametrize("args", [{}, {"text": "hello", "record_id": "TEST-001"}, {"record_id": "001"}, {"text": "x" * 20_001}])
def test_detect_pii_rejects_invalid_input(tool_service, args):
    assert error_code(tool_service.detect_pii, **args) == "INVALID_INPUT"


def test_detect_pii_unknown_record(tool_service):
    assert error_code(tool_service.detect_pii, record_id="TEST-999") == "NOT_FOUND"


def test_detect_pii_detector_crash_becomes_tool_failure(keyword_retriever):
    service = ToolService(retriever=keyword_retriever, detector=GroundTruthDetector(fail_times=1))
    assert error_code(service.detect_pii, record_id="TEST-001") == "TOOL_FAILURE"


# --- propose_redactions ------------------------------------------------------------


def test_propose_redactions_end_to_end(tool_service):
    findings = [f.model_dump(mode="json") for f in tool_service.detect_pii(record_id="TEST-001").findings]
    evidence = tool_service.search_policy(query="telephone number driver license number of any person", method="keyword")
    proposal = tool_service.propose_redactions(
        record_id="TEST-001", findings=findings, policy_chunk_ids=[p.chunk.chunk_id for p in evidence.passages]
    )
    assert len(proposal.recommendations) == 2
    assert all(r.requires_human_approval for r in proposal.recommendations)


def test_propose_redactions_rejects_invented_chunk_ids(tool_service):
    assert error_code(tool_service.propose_redactions, record_id="TEST-001", findings=[], policy_chunk_ids=["opra_statute:9999"]) == "NOT_FOUND"


def test_propose_redactions_rejects_tampered_findings(tool_service):
    [f, _] = [f.model_dump(mode="json") for f in tool_service.detect_pii(record_id="TEST-001").findings]
    f["text"] = "609-555-0000"
    assert error_code(tool_service.propose_redactions, record_id="TEST-001", findings=[f], policy_chunk_ids=[]) == "INVALID_INPUT"


def test_propose_redactions_unknown_record(tool_service):
    assert error_code(tool_service.propose_redactions, record_id="TEST-999", findings=[], policy_chunk_ids=[]) == "NOT_FOUND"


# --- dispatch ---------------------------------------------------------------------


def test_call_returns_json_serialisable_output(tool_service):
    result = tool_service.call("detect_pii", {"record_id": "TEST-002"})
    assert json.loads(json.dumps(result))["record_id"] == "TEST-002"


def test_call_unknown_tool(tool_service):
    with pytest.raises(ToolError) as exc:
        tool_service.call("delete_records", {})
    assert exc.value.code == "UNKNOWN_TOOL"
