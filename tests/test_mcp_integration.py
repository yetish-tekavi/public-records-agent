"""Integration: a real MCP client starts the real MCP server subprocess and calls the tools."""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from app.agents.executors import McpToolExecutor
from app.agents.models import AgentStatus
from app.agents.planners import RuleBasedPlanner
from app.agents.react_agent import ReActAgent
from app.data_loader import load_review_cases
from app.tools.service import ToolError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp not installed"),
    pytest.mark.skipif(importlib.util.find_spec("en_core_web_md") is None, reason="spaCy model not installed"),
]

KEYWORD_ONLY = {"PRA_SEMANTIC": "0"}


def test_client_discovers_and_calls_tools():
    async def scenario():
        async with McpToolExecutor(env=KEYWORD_ONLY) as mcp:
            tools = {t.name: t for t in await mcp.list_tools()}
            assert set(tools) == {"search_policy", "detect_pii", "propose_redactions", "validate_citations"}
            assert {"record_id", "findings", "policy_chunk_ids"} <= set(tools["propose_redactions"].input_schema["properties"])

            policy = await mcp.call("search_policy", {"query": "telephone number driver license number of any person", "k": 3})
            assert policy["status"] == "SUFFICIENT"

            pii = await mcp.call("detect_pii", {"record_id": "TEST-001"})
            assert [f["category"] for f in pii["findings"]] == ["PHONE_NUMBER", "DRIVERS_LICENSE_NUMBER"]

            proposal = await mcp.call("propose_redactions", {
                "record_id": "TEST-001",
                "findings": pii["findings"],
                "policy_chunk_ids": [p["chunk"]["chunk_id"] for p in policy["passages"]],
            })
            assert len(proposal["recommendations"]) == 2

            first = policy["passages"][0]["chunk"]
            checks = await mcp.call("validate_citations", {"citations": [
                {"chunk_id": first["chunk_id"], "excerpt": first["text"]}, {"chunk_id": "invented:0001"}]})
            assert [c["exists"] for c in checks["checks"]] == [True, False] and checks["all_valid"] is False

            missing = await mcp.call("search_policy", {"query": "recipe for chocolate chip cookies"})
            assert missing["status"] == "INSUFFICIENT" and missing["passages"] == []

            with pytest.raises(ToolError) as exc:
                await mcp.call("propose_redactions", {"record_id": "TEST-001", "findings": [], "policy_chunk_ids": ["invented:1"]})
            assert exc.value.code == "NOT_FOUND"

            with pytest.raises(ToolError) as exc:
                await mcp.call("detect_pii", {})
            assert exc.value.code == "INVALID_INPUT"

    asyncio.run(scenario())


def test_agent_runs_over_mcp():
    cases = {c.request.request_id: c for c in load_review_cases()}

    async def scenario():
        async with McpToolExecutor(env=KEYWORD_ONLY) as mcp:
            reports = []
            for rid in ("OPRA-TEST-001", "OPRA-TEST-003"):
                agent = ReActAgent(RuleBasedPlanner(), mcp)
                reports.append(await agent.run(cases[rid].request, cases[rid].record))
            return reports

    first, third = asyncio.run(scenario())
    assert first.transport == "mcp-stdio"
    assert first.status is AgentStatus.COMPLETED and len(first.recommendations) == 2
    assert third.status is AgentStatus.COMPLETED and third.recommendations == []
