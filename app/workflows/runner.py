"""Run a review case through the Milestone 4 graph, pausing at human review, and resume with a decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from app.schemas import PublicRecord, PublicRecordRequest
from app.workflows.graph import Orchestrator, initial_state, recursion_limit
from app.workflows.state import CompletionStatus, HumanDecision, WorkflowConfig


@dataclass
class WorkflowRun:
    app: Any
    config: dict[str, Any]
    state: dict[str, Any]
    review_package: dict[str, Any] | None


async def start(orchestrator: Orchestrator, request: PublicRecordRequest, record: PublicRecord,
                thread_id: str | None = None, config: WorkflowConfig | None = None) -> WorkflowRun:
    """Run until the workflow pauses for the human reviewer (or ends)."""
    wf_config = config or orchestrator.config
    app = orchestrator.build()
    run_config = {"configurable": {"thread_id": thread_id or request.request_id}, "recursion_limit": recursion_limit(wf_config)}
    result = await app.ainvoke(initial_state(request, record, wf_config), run_config)
    package = result["__interrupt__"][0].value if result.get("__interrupt__") else None
    state = (await app.aget_state(run_config)).values
    if package is not None:
        state = {**state, "completion": CompletionStatus.AWAITING_HUMAN_REVIEW}
        state["human_review"] = state["human_review"].model_copy(update={"status": "PENDING"})
    return WorkflowRun(app=app, config=run_config, state=state, review_package=package)


async def resume(run: WorkflowRun, decision: HumanDecision | dict[str, Any]) -> WorkflowRun:
    """Resume a paused workflow with a human decision (validated before it is applied)."""
    decision = HumanDecision.model_validate(decision)
    await run.app.ainvoke(Command(resume=decision.model_dump()), run.config)
    state = (await run.app.aget_state(run.config)).values
    return WorkflowRun(app=run.app, config=run.config, state=state, review_package=None)
