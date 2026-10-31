"""ClaudePlanner with a scripted fake Anthropic client: message protocol, parsing, and failure handling.

No network calls are made. A live run needs Anthropic credentials and is not part of the suite.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.agents.executors import LocalToolExecutor
from app.agents.models import AgentStatus
from app.agents.planners import CLAUDE_TOOLS, DEFAULT_MODEL, ClaudePlanner
from app.agents.react_agent import ReActAgent
from app.data_loader import load_review_cases
from app.schemas import RedactionAction

CASE = {c.request.request_id: c for c in load_review_cases()}["OPRA-TEST-001"]


def tool_use(id_, name, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)


def response(*blocks, stop_reason="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=None)


THINKING = SimpleNamespace(type="thinking", thinking="", signature="sig")
TEXT = SimpleNamespace(type="text", text="internal narration that must not be surfaced")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def run(client, service, max_steps=10):
    agent = ReActAgent(ClaudePlanner(client=client), LocalToolExecutor(service), max_steps=max_steps)
    return asyncio.run(agent.run(CASE.request, CASE.record))


SCRIPT = [
    response(THINKING, TEXT, tool_use("t1", "detect_pii", record_id="TEST-001", decision_summary="Scan the record first.")),
    response(tool_use("t2", "search_policy", query="telephone number driver license number of any person",
                      decision_summary="Look for policy on phone and licence numbers.")),
    response(tool_use("t3", "propose_redactions", record_id="TEST-001", finding_ids=["TEST-001-F01", "TEST-001-F02"],
                      policy_chunk_ids=["PLACEHOLDER"], decision_summary="Propose using the retrieved passages.")),
    response(tool_use("t4", "finish", escalations=[], decision_summary="Recommendations ready for review."), stop_reason="tool_use"),
]


def scripted_with_real_chunk(service):
    chunk_id = service.search_policy(query="telephone number driver license number of any person", method="keyword").passages[0].chunk.chunk_id
    script = [r for r in SCRIPT]
    script[2] = response(tool_use("t3", "propose_redactions", record_id="TEST-001", finding_ids=["TEST-001-F01", "TEST-001-F02"],
                                  policy_chunk_ids=[chunk_id], decision_summary="Propose using the retrieved passages."))
    return script


def test_full_run_follows_the_tool_protocol(tool_service):
    client = FakeClient(scripted_with_real_chunk(tool_service))
    report = run(client, tool_service)

    assert [s.tool or s.kind for s in report.steps] == ["detect_pii", "search_policy", "propose_redactions", "finish"]
    assert report.status is AgentStatus.COMPLETED
    assert report.steps[0].decision_summary == "Scan the record first."
    assert "decision_summary" not in report.steps[0].arguments
    assert all(r.action is RedactionAction.REDACT for r in report.recommendations)

    # Each follow-up request returns exactly one tool_result per tool_use, with matching ids.
    second = client.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["content"][0] is THINKING  # content passed back unchanged
    [result] = second[-1]["content"]
    assert result["tool_use_id"] == "t1" and result["is_error"] is False
    assert json.loads(result["content"])["findings"][0]["category"] == "PHONE_NUMBER"


def test_request_shape(tool_service):
    client = FakeClient(scripted_with_real_chunk(tool_service))
    run(client, tool_service)
    first = client.requests[0]
    assert first["model"] == DEFAULT_MODEL
    assert first["tools"] is CLAUDE_TOOLS and all(t["strict"] for t in first["tools"])
    assert first["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in first["betas"]
    assert "<record_text>" in first["messages"][0]["content"]
    assert "thinking" not in first  # model default; reasoning is never requested for display


def test_private_reasoning_is_not_surfaced(tool_service):
    report = run(FakeClient(scripted_with_real_chunk(tool_service)), tool_service)
    dumped = report.model_dump_json()
    assert "internal narration" not in dumped


def test_tool_errors_are_returned_to_the_model(tool_service):
    script = [
        response(tool_use("t1", "propose_redactions", record_id="TEST-001", finding_ids=[], policy_chunk_ids=["invented:1"],
                          decision_summary="Try proposing.")),
        response(tool_use("t2", "finish", escalations=["Could not complete."], decision_summary="Stopping.")),
    ]
    client = FakeClient(script)
    report = run(client, tool_service)
    [result] = client.requests[1]["messages"][-1]["content"]
    assert result["is_error"] is True and json.loads(result["content"])["error"] == "NOT_FOUND"
    assert report.status is AgentStatus.ESCALATED  # finish was blocked: record never examined


def test_parallel_tool_calls_return_results_in_one_message(tool_service):
    script = [
        response(tool_use("a", "detect_pii", record_id="TEST-003", decision_summary="Scan."),
                 tool_use("b", "search_policy", query="juvenile information exemption", decision_summary="Background.")),
        response(tool_use("c", "finish", escalations=[], decision_summary="Nothing to redact.")),
    ]
    client = FakeClient(script)
    case = {c.request.request_id: c for c in load_review_cases()}["OPRA-TEST-003"]
    agent = ReActAgent(ClaudePlanner(client=client), LocalToolExecutor(tool_service))
    asyncio.run(agent.run(case.request, case.record))
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_refusal_is_escalated(tool_service):
    report = run(FakeClient([response(stop_reason="refusal")]), tool_service)
    assert report.status is AgentStatus.ESCALATED
    assert any("declined" in e for e in report.escalations)


def test_model_that_stops_without_tools_is_nudged_then_escalated(tool_service):
    client = FakeClient([response(TEXT, stop_reason="end_turn"), response(TEXT, stop_reason="end_turn")])
    report = run(client, tool_service)
    assert client.requests[1]["messages"][-1]["content"].startswith("Continue by calling a tool")
    assert report.status is AgentStatus.ESCALATED


def test_step_limit_applies_to_llm_planner(tool_service):
    looping = [response(tool_use(f"t{i}", "search_policy", query="telephone number", decision_summary="Search again."))
               for i in range(5)]
    report = run(FakeClient(looping), tool_service, max_steps=3)
    assert report.status is AgentStatus.STEP_LIMIT_REACHED
    assert len(report.steps) == 3
