"""ReAct loop behaviour with the rule-based planner and stub tools (no LLM, no Presidio)."""

from __future__ import annotations

import asyncio

import pytest

from app.agents.executors import LocalToolExecutor
from app.agents.models import AgentStatus, FinishRequest, ToolCallRequest
from app.agents.planners import RuleBasedPlanner
from app.agents.react_agent import ReActAgent
from app.data_loader import load_review_cases
from app.schemas import EvidenceStatus, PolicyRetrievalResult, RedactionAction
from app.tools.service import ToolService
from tests.conftest import GroundTruthDetector

CASES = {c.request.request_id: c for c in load_review_cases()}


def run_agent(service: ToolService, request_id: str, planner=None, **kwargs):
    case = CASES[request_id]
    agent = ReActAgent(planner or RuleBasedPlanner(), LocalToolExecutor(service), **kwargs)
    return asyncio.run(agent.run(case.request, case.record))


class NoPolicyRetriever:
    """Wraps the real retriever but never finds evidence: the policy-not-found scenario."""

    def __init__(self, real):
        self.chunks = real.chunks

    def search(self, query, method="hybrid", k=5):
        return PolicyRetrievalResult(query=query, method="keyword", status=EvidenceStatus.INSUFFICIENT,
                                     message="Insufficient policy evidence. Human review required.")


def test_happy_path_tool_sequence_and_recommendations(tool_service):
    report = run_agent(tool_service, "OPRA-TEST-001")
    assert [s.tool or s.kind for s in report.steps] == ["detect_pii", "search_policy", "search_policy", "propose_redactions", "finish"]
    assert report.status is AgentStatus.COMPLETED
    assert [r.action for r in report.recommendations] == [RedactionAction.REDACT, RedactionAction.REDACT]
    assert all(r.policy_references for r in report.recommendations)
    assert all(s.decision_summary for s in report.steps)
    assert report.requires_human_approval is True


def test_trace_shows_finding_ids_not_full_finding_objects(tool_service):
    report = run_agent(tool_service, "OPRA-TEST-001")
    propose = next(s for s in report.steps if s.tool == "propose_redactions")
    assert propose.arguments["finding_ids"] == ["TEST-001-F01", "TEST-001-F02"]
    assert "findings" not in propose.arguments


def test_record_without_pii_finishes_without_searching(tool_service):
    report = run_agent(tool_service, "OPRA-TEST-003")
    assert [s.tool or s.kind for s in report.steps] == ["detect_pii", "finish"]
    assert report.status is AgentStatus.COMPLETED
    assert report.recommendations == []


def test_policy_not_found_escalates_instead_of_inventing(keyword_retriever, ground_truth_detector):
    service = ToolService(retriever=NoPolicyRetriever(keyword_retriever), detector=ground_truth_detector)
    report = run_agent(service, "OPRA-TEST-001")
    searches = [s for s in report.steps if s.tool == "search_policy"]
    assert len(searches) == 4  # each category tried with a reformulated query
    assert all(r.action is RedactionAction.HUMAN_REVIEW_REQUIRED for r in report.recommendations)
    assert all(r.policy_references == [] for r in report.recommendations)
    assert report.status is AgentStatus.ESCALATED
    assert any("no policy basis" in e for e in report.escalations)


def test_transient_tool_failure_is_retried(keyword_retriever):
    service = ToolService(retriever=keyword_retriever, detector=GroundTruthDetector(fail_times=1))
    report = run_agent(service, "OPRA-TEST-001")
    assert [s.status for s in report.steps[:2]] == ["error", "ok"]
    assert "TOOL_FAILURE" in report.steps[0].observation
    assert report.recommendations


def test_repeated_tool_failure_stops_and_escalates(keyword_retriever):
    service = ToolService(retriever=keyword_retriever, detector=GroundTruthDetector(fail_times=99))
    report = run_agent(service, "OPRA-TEST-001")
    assert [s.status for s in report.steps] == ["error", "error"]
    assert report.status is AgentStatus.ESCALATED
    assert any("detect_pii failed 2 time(s)" in e for e in report.escalations)
    assert report.recommendations == []


def test_step_limit_is_enforced_and_findings_still_go_to_review(tool_service):
    report = run_agent(tool_service, "OPRA-TEST-002", max_steps=2)
    assert len(report.steps) == 2
    assert report.status is AgentStatus.STEP_LIMIT_REACHED
    assert len(report.recommendations) == 3
    assert all(r.action is RedactionAction.HUMAN_REVIEW_REQUIRED for r in report.recommendations)
    assert any("Step limit" in e for e in report.escalations)


class ScriptedPlanner:
    """Plays back a fixed list of actions, for guardrail tests."""

    name = "scripted"

    def __init__(self, actions):
        self.actions = list(actions)
        self.observed = []

    async def next_action(self, state):
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def observe(self, call, result, error):
        self.observed.append((call.tool, error.code if error else None))


def test_finish_before_examining_record_is_blocked(tool_service):
    report = run_agent(tool_service, "OPRA-TEST-001", planner=ScriptedPlanner([FinishRequest(decision_summary="Looks fine.")]))
    assert report.steps[0].kind == "guardrail" and report.steps[0].status == "blocked"
    assert report.status is AgentStatus.ESCALATED


def test_finish_before_recommendations_is_blocked_and_findings_fall_back_to_review(tool_service):
    planner = ScriptedPlanner([
        ToolCallRequest(tool="detect_pii", arguments={"record_id": "TEST-001"}, decision_summary="Scan."),
        FinishRequest(decision_summary="Done."),
    ])
    report = run_agent(tool_service, "OPRA-TEST-001", planner=planner)
    assert report.steps[-1].status == "blocked"
    assert all(r.action is RedactionAction.HUMAN_REVIEW_REQUIRED for r in report.recommendations)


def test_unknown_finding_id_is_a_tool_error_not_a_crash(tool_service):
    planner = ScriptedPlanner([
        ToolCallRequest(tool="detect_pii", arguments={"record_id": "TEST-001"}, decision_summary="Scan."),
        ToolCallRequest(tool="propose_redactions", arguments={"finding_ids": ["TEST-001-F99"], "policy_chunk_ids": []}, decision_summary="Propose."),
        ToolCallRequest(tool="propose_redactions", arguments={"policy_chunk_ids": []}, decision_summary="Propose all."),
        FinishRequest(decision_summary="Done."),
    ])
    report = run_agent(tool_service, "OPRA-TEST-001", planner=planner)
    assert report.steps[1].status == "error" and "TEST-001-F99" in report.steps[1].observation
    assert ("propose_redactions", "INVALID_INPUT") in planner.observed
    assert report.steps[2].status == "ok"


def test_invented_chunk_id_is_rejected(tool_service):
    planner = ScriptedPlanner([
        ToolCallRequest(tool="detect_pii", arguments={"record_id": "TEST-001"}, decision_summary="Scan."),
        ToolCallRequest(tool="propose_redactions", arguments={"policy_chunk_ids": ["opra_statute:7777"]}, decision_summary="Cite."),
        FinishRequest(decision_summary="Done."),
    ])
    report = run_agent(tool_service, "OPRA-TEST-001", planner=planner)
    assert "NOT_FOUND" in report.steps[1].observation
    assert all(not r.policy_references for r in report.recommendations)


def test_planner_crash_is_contained(tool_service):
    report = run_agent(tool_service, "OPRA-TEST-001", planner=ScriptedPlanner([RuntimeError("LLM unavailable")]))
    assert report.status is AgentStatus.ESCALATED
    assert any("Planner failed" in e for e in report.escalations)


def test_max_steps_must_be_positive(tool_service):
    with pytest.raises(ValueError):
        ReActAgent(RuleBasedPlanner(), LocalToolExecutor(tool_service), max_steps=0)
