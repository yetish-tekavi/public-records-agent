"""Agent actions, trace, and report models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import RedactionRecommendation, SensitiveDataFinding, StrictModel

ToolName = Literal["search_policy", "detect_pii", "propose_redactions"]


class ToolCallRequest(BaseModel):
    """A planner's request to call one tool. ``decision_summary`` is a short public rationale."""

    model_config = ConfigDict(extra="forbid")

    tool: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    decision_summary: str = Field(min_length=1, max_length=400)
    call_id: str | None = None  # LLM tool_use id, when there is one


class FinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_summary: str = Field(min_length=1, max_length=600)
    escalations: list[str] = Field(default_factory=list)
    call_id: str | None = None


AgentAction = ToolCallRequest | FinishRequest


class AgentStatus(str, Enum):
    COMPLETED = "COMPLETED"  # finished normally, nothing unresolved
    ESCALATED = "ESCALATED"  # finished or stopped with issues a human must resolve
    STEP_LIMIT_REACHED = "STEP_LIMIT_REACHED"


class TraceStep(StrictModel):
    """One visible step: what was called, with which arguments, what came back, and why."""

    step: int = Field(ge=1)
    kind: Literal["tool_call", "finish", "guardrail"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "error", "blocked"]
    observation: str
    decision_summary: str


class AgentReport(StrictModel):
    request_id: str
    record_id: str
    planner: str
    transport: str
    status: AgentStatus
    steps: list[TraceStep]
    findings: list[SensitiveDataFinding] = Field(default_factory=list)
    policy_chunk_ids: list[str] = Field(default_factory=list)
    recommendations: list[RedactionRecommendation] = Field(default_factory=list)
    escalations: list[str] = Field(default_factory=list)
    final_summary: str
    requires_human_approval: Literal[True] = True
