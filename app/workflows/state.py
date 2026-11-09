"""Typed state for the Milestone 4 multi-agent workflow.

Agents never talk to each other directly: each LangGraph node reads this state and returns a
partial update. List fields annotated with ``operator.add`` accumulate (audit log, tool calls,
transitions, escalations); every other field is overwritten by the node that owns it.
"""

from __future__ import annotations

import operator
from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import (
    EvidenceStatus,
    PolicyChunk,
    RedactionProposal,
    RedactionRecommendation,
    SensitiveCategory,
    SensitiveDataFinding,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- configuration ---------------------------------------------------------------


class ToTConfig(_Model):
    max_branching: int = Field(default=3, ge=1, le=3)
    max_depth: int = Field(default=3, ge=1, le=3)
    beam_width: int = Field(default=2, ge=1, le=3)
    min_score: int = Field(default=70, ge=0, le=100)
    max_tool_calls: int = Field(default=6, ge=0)
    time_budget_s: float = Field(default=60.0, gt=0)
    allow_targeted_retrieval: bool = True


class WorkflowConfig(_Model):
    max_steps: int = Field(default=30, ge=5)
    tool_timeout_s: float = Field(default=120.0, gt=0)
    max_record_attempts: int = Field(default=2, ge=1)
    max_queries_per_category: int = Field(default=2, ge=1)
    missing_evidence_retries: int = Field(default=1, ge=0, le=1)
    max_error_recoveries: int = Field(default=1, ge=0)
    search_k: int = Field(default=4, ge=1, le=10)
    tot: ToTConfig = Field(default_factory=ToTConfig)


# --- request and agent outputs ---------------------------------------------------------


class RequestContext(_Model):
    request_id: str
    record_id: str
    received_date: date
    description: str
    record_title: str
    record_type: str


class ToolCallRecord(_Model):
    agent: str
    tool: str
    arguments: dict[str, Any]
    status: Literal["ok", "error", "timeout", "skipped"]
    summary: str
    duration_ms: int


class RecordAnalysisResult(_Model):
    status: Literal["OK", "FAILED"]
    attempts: int
    findings: list[SensitiveDataFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CategoryEvidence(_Model):
    """Policy Research Agent output for one category of finding."""

    category: SensitiveCategory
    status: Literal["FOUND", "MISSING", "ERROR"]
    queries: list[str] = Field(default_factory=list)
    passages: list[PolicyChunk] = Field(default_factory=list)
    retrieval_status: list[EvidenceStatus] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    targeted_retry: bool = False


class EvidenceDecision(str, Enum):
    CLEAR = "CLEAR"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    MISSING = "MISSING"


class EvidenceAssessment(_Model):
    finding_id: str
    category: SensitiveCategory
    decision: EvidenceDecision
    reasons: list[str]
    supporting_chunk_ids: list[str] = Field(default_factory=list)


class CitationCheck(_Model):
    chunk_id: str
    exists: bool
    excerpt_matches: bool | None = None
    section: str | None = None
    source_url: str | None = None


class QAReport(_Model):
    proposal: RedactionProposal | None = None
    citation_checks: list[CitationCheck] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    assessments: list[EvidenceAssessment] = Field(default_factory=list)
    possible_missed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# --- Tree of Thought ---------------------------------------------------------------------


class Assumption(_Model):
    """A condition a branch depends on. FACTUAL ones are checked against the record text:
    supported when a check term is found (or, with ``supported_when_found=False``, when none is)."""

    text: str
    kind: Literal["FACTUAL", "LEGAL"]
    status: Literal["UNVERIFIED", "SUPPORTED", "UNSUPPORTED"] = "UNVERIFIED"
    check_terms: list[str] = Field(default_factory=list)
    supported_when_found: bool = True
    evidence: str | None = None


class BranchScore(_Model):
    policy_relevance: int = Field(ge=0, le=30)
    citation_validity: int = Field(ge=0, le=30)
    consistency: int = Field(ge=0, le=25)
    explanation: int = Field(ge=0, le=15)
    total: int = Field(ge=0, le=100)
    notes: list[str] = Field(default_factory=list)


class ThoughtNode(_Model):
    """One ToT node. Each node carries its own evidence copy so branches cannot contaminate each other."""

    node_id: str
    parent_id: str | None
    depth: int = Field(ge=1)
    stage: Literal["INTERPRETATION", "VERIFICATION", "CONCLUSION"]
    action: Literal["REDACT", "HUMAN_REVIEW_REQUIRED"]
    provision_type: str
    interpretation: str
    cited_chunk_ids: list[str] = Field(default_factory=list)
    evidence: list[PolicyChunk] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    status: Literal["PENDING", "EVALUATED", "REJECTED", "PRUNED", "SELECTED"] = "PENDING"
    score: BranchScore | None = None
    terminal: bool = False


class PruneRecord(_Model):
    node_id: str
    depth: int
    reason: str


class ToTResult(_Model):
    finding_id: str
    category: SensitiveCategory
    outcome: Literal["SUPPORTED_RECOMMENDATION", "ESCALATED"]
    action: Literal["REDACT", "HUMAN_REVIEW_REQUIRED"]
    selected_node_id: str | None
    selected_score: int | None
    reason: str
    nodes: list[ThoughtNode]
    pruned: list[PruneRecord]
    depth_reached: int
    tool_calls_used: int
    targeted_retrieval_used: bool
    budget_events: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


# --- control and completion ----------------------------------------------------------------


class RetryCounters(_Model):
    record_analysis: int = 0
    missing_evidence: int = 0
    error_recovery: int = 0


class HumanReviewStatus(_Model):
    status: Literal["NOT_STARTED", "PENDING", "APPROVED", "REJECTED", "RETURNED_FOR_REVISION"] = "NOT_STARTED"
    reviewer: str | None = None
    notes: str | None = None
    decided_at: str | None = None


class CompletionStatus(str, Enum):
    RUNNING = "RUNNING"
    AWAITING_HUMAN_REVIEW = "AWAITING_HUMAN_REVIEW"
    APPROVED_BY_HUMAN = "APPROVED_BY_HUMAN"
    REJECTED_BY_HUMAN = "REJECTED_BY_HUMAN"
    RETURNED_FOR_REVISION = "RETURNED_FOR_REVISION"


class AuditEvent(_Model):
    step: int
    node: str
    agent: str
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)


class HumanDecision(_Model):
    decision: Literal["approve", "reject", "return"]
    reviewer: str = Field(min_length=1)
    notes: str | None = None


class WorkflowState(TypedDict, total=False):
    request: RequestContext
    record_text: str  # read-only copy; tools never modify the source record
    config: WorkflowConfig
    record_analysis: RecordAnalysisResult
    findings: list[SensitiveDataFinding]
    evidence: list[CategoryEvidence]
    qa: QAReport
    route: str
    tot_results: list[ToTResult]
    recommendations: list[RedactionRecommendation]
    retries: RetryCounters
    step_count: int
    human_review: HumanReviewStatus
    completion: CompletionStatus
    disclosure_authorized: bool
    final_summary: str
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    audit: Annotated[list[AuditEvent], operator.add]
    transitions: Annotated[list[str], operator.add]
    escalations: Annotated[list[str], operator.add]


class ToTState(TypedDict, total=False):
    """Separate state for one ToT run, isolated from the main workflow state."""

    finding: SensitiveDataFinding
    record_text: str
    evidence: list[PolicyChunk]
    config: ToTConfig
    depth: int
    frontier: list[ThoughtNode]
    nodes: dict[str, ThoughtNode]
    pruned: list[PruneRecord]
    tool_calls_used: int
    targeted_retrieval_used: bool
    started_at: float
    budget_events: list[str]
    citation_cache: dict[str, bool]
    result: ToTResult
