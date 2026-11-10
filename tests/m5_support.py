"""Milestone 5 fixtures: review sessions built from real Milestone 4 workflow runs (stubbed retrieval)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.review import session as session_module
from app.review.session import ReviewSession
from app.tools.service import ToolService
from app.workflows.graph import Orchestrator
from app.workflows.runner import start
from tests.conftest import GroundTruthDetector
from tests.m4_support import M4_CASES, M4_DATA, ScriptedExecutor, StubRetriever

RECORD_PATHS = {rid: M4_DATA / "synthetic_records" / f"record_{rid[-3:]}.txt" for rid in M4_CASES}


def make_orchestrator(policy_chunks, data_dir: Path = M4_DATA) -> tuple[Orchestrator, ScriptedExecutor]:
    service = ToolService(data_dir=data_dir, retriever=StubRetriever(policy_chunks), detector=GroundTruthDetector(data_dir=data_dir))
    executor = ScriptedExecutor(service)
    return Orchestrator(executor), executor


def run_workflow(orchestrator: Orchestrator, request_id: str):
    case = M4_CASES[request_id]
    return asyncio.run(start(orchestrator, case.request, case.record, thread_id=f"m5-{request_id}")).state


def make_session(policy_chunks, request_id: str) -> tuple[ReviewSession, Orchestrator, ScriptedExecutor, dict]:
    orchestrator, executor = make_orchestrator(policy_chunks)
    state = run_workflow(orchestrator, request_id)
    return ReviewSession.from_workflow(state, RECORD_PATHS[request_id], actor="orchestrator"), orchestrator, executor, state


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    """Send derivatives and saved sessions to a temporary folder."""
    monkeypatch.setattr(session_module, "DERIVATIVE_DIR", tmp_path / "derivatives")
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path / "reviews")
    return tmp_path
