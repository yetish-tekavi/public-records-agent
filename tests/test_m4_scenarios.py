"""Full-stack Milestone 4 scenarios: real Presidio, real hybrid retrieval, real LangGraph workflow."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from app.agents.executors import LocalToolExecutor, McpToolExecutor
from app.schemas import RedactionAction
from app.tools.service import ToolService
from app.workflows.graph import Orchestrator
from app.workflows.runner import start
from tests.m4_support import M4_CASES, M4_DATA

MODEL_CACHE = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
pytestmark = [
    pytest.mark.semantic_model,
    pytest.mark.skipif(not MODEL_CACHE.exists(), reason="embedding model not downloaded"),
    pytest.mark.skipif(importlib.util.find_spec("en_core_web_md") is None, reason="spaCy model not installed"),
]


@pytest.fixture(scope="module")
def orchestrator():
    return Orchestrator(LocalToolExecutor(ToolService(data_dir=M4_DATA)))


def run(orchestrator, request_id):
    case = M4_CASES[request_id]
    return asyncio.run(start(orchestrator, case.request, case.record, thread_id=f"full-{request_id}")).state


def test_scenario_a_straightforward(orchestrator):
    s = run(orchestrator, "OPRA-TEST-201")
    assert "tot" not in s["transitions"] and s["route"] == "standard"
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.REDACT
    assert rec.policy_references[0].citation.endswith("N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined")


def test_scenario_b_ambiguous_uses_tot(orchestrator):
    s = run(orchestrator, "OPRA-TEST-202")
    [tot] = s["tot_results"]
    assert len([n for n in tot.nodes if n.depth == 1]) >= 2
    assert tot.outcome == "SUPPORTED_RECOMMENDATION" and tot.selected_score >= 70
    assert s["recommendations"][0].action is RedactionAction.REDACT


def test_scenario_c_missing_evidence(orchestrator):
    s = run(orchestrator, "OPRA-TEST-203")
    assert "targeted_retrieval" in s["transitions"] and s["retries"].missing_evidence == 1
    [rec] = s["recommendations"]
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED and rec.policy_references == []


def test_scenario_d_tot_failure_protection(orchestrator):
    s = run(orchestrator, "OPRA-TEST-204")
    [tot] = s["tot_results"]
    assert tot.outcome == "ESCALATED" and any(p.reason.startswith("rejected") for p in tot.pruned)
    assert s["recommendations"][0].action is RedactionAction.HUMAN_REVIEW_REQUIRED


@pytest.mark.integration
@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp not installed")
def test_workflow_over_mcp():
    case = M4_CASES["OPRA-TEST-201"]

    async def scenario():
        async with McpToolExecutor(env={"PRA_DATA_DIR": str(M4_DATA), "PRA_SEMANTIC": "1"}) as mcp:
            return (await start(Orchestrator(mcp), case.request, case.record, thread_id="mcp-201")).state

    s = asyncio.run(scenario())
    assert s["recommendations"][0].action is RedactionAction.REDACT
    assert {t.tool for t in s["tool_calls"]} == {"detect_pii", "search_policy", "propose_redactions", "validate_citations"}
