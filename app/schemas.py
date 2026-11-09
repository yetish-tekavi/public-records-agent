"""Structured data models shared by every stage of the assistant.

All models reject unknown fields so that typos in data files or tool outputs
fail loudly instead of being silently ignored.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUEST_ID_PATTERN = r"^OPRA-TEST-\d{3}$"
RECORD_ID_PATTERN = r"^TEST-\d{3}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RecordType(str, Enum):
    INTERACTION_TRANSCRIPT = "interaction_transcript"
    CALL_TRANSCRIPT = "call_transcript"
    INCIDENT_REPORT = "incident_report"
    INSPECTION_LOG = "inspection_log"


class SensitiveCategory(str, Enum):
    PERSON_NAME = "PERSON_NAME"
    PHONE_NUMBER = "PHONE_NUMBER"
    STREET_ADDRESS = "STREET_ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    DRIVERS_LICENSE_NUMBER = "DRIVERS_LICENSE_NUMBER"
    SOCIAL_SECURITY_NUMBER = "SOCIAL_SECURITY_NUMBER"
    EMAIL_ADDRESS = "EMAIL_ADDRESS"
    IP_ADDRESS = "IP_ADDRESS"
    OTHER_ID_NUMBER = "OTHER_ID_NUMBER"


class RedactionAction(str, Enum):
    REDACT = "REDACT"
    RELEASE = "RELEASE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class PublicRecordRequest(StrictModel):
    """A synthetic OPRA-style request, already matched to one record by the custodian."""

    request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    received_date: date
    requester: str = Field(min_length=1)
    description: str = Field(min_length=1)
    synthetic: Literal[True]


class PublicRecord(StrictModel):
    """A synthetic record. Finding offsets index into ``text``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    record_type: RecordType
    title: str = Field(min_length=1)
    record_date: date
    text: str = Field(min_length=1)
    synthetic: Literal[True]
    source_file: str | None = None


class SensitiveDataFinding(StrictModel):
    """A span of record text that may need redaction. ``record_id`` is None for ad-hoc text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    finding_id: str = Field(min_length=1)
    record_id: str | None = Field(default=None, pattern=RECORD_ID_PATTERN)
    category: SensitiveCategory
    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    detector: str = Field(default="ground_truth", min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_span(self) -> SensitiveDataFinding:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be greater than start ({self.start})")
        if self.end - self.start != len(self.text):
            raise ValueError(
                f"span length {self.end - self.start} does not match text length {len(self.text)}"
            )
        return self


class PolicyReference(StrictModel):
    """A citation to retrieved policy text. ``excerpt`` must be quoted, not paraphrased."""

    source_document: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    excerpt: str = Field(min_length=1)
    source_url: str | None = None
    chunk_id: str | None = None


class RedactionRecommendation(StrictModel):
    """A proposed action for one finding. Always subject to human approval."""

    recommendation_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    action: RedactionAction
    rationale: str = Field(min_length=1)
    policy_references: list[PolicyReference] = Field(default_factory=list)
    detection_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    open_questions: list[str] = Field(default_factory=list)
    requires_human_approval: Literal[True] = True

    @model_validator(mode="after")
    def _redaction_needs_policy_basis(self) -> RedactionRecommendation:
        if self.action is RedactionAction.REDACT and not self.policy_references:
            raise ValueError(
                "REDACT requires at least one policy reference; "
                "use HUMAN_REVIEW_REQUIRED when no policy basis was retrieved"
            )
        return self


class ExpectedResult(StrictModel):
    """Ground-truth findings for one request/record pair, used for evaluation."""

    request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    requires_redaction: bool
    findings: list[SensitiveDataFinding] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _check_consistency(self) -> ExpectedResult:
        if self.requires_redaction != bool(self.findings):
            raise ValueError("requires_redaction must be true exactly when findings are present")
        ids = [f.finding_id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding_id values must be unique")
        for finding in self.findings:
            if finding.record_id != self.record_id:
                raise ValueError(
                    f"finding {finding.finding_id} belongs to {finding.record_id}, "
                    f"expected {self.record_id}"
                )
        return self


class ReviewCase(StrictModel):
    """A request, its matched record, and the ground truth for that pair."""

    request: PublicRecordRequest
    record: PublicRecord
    expected: ExpectedResult


# --- Policy retrieval --------------------------------------------------------

SectionStyle = Literal["opra_statute", "grc_exemptions", "grc_handbook", "ag_directive", "html_headings"]


class PolicyDocument(StrictModel):
    """Manifest entry for one locally stored official policy document."""

    doc_id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    file: str = Field(min_length=1)
    source_url: str = Field(pattern=r"^https://")
    landing_url: str | None = Field(default=None, pattern=r"^https://")
    effective_date: str | None = None  # as stated by the source, e.g. "as of September 3, 2024"
    retrieved_date: date
    section_style: SectionStyle
    strip_line_patterns: list[str] = Field(default_factory=list)


class PolicyChunk(StrictModel):
    """A verbatim passage of a policy document with its provenance."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    effective_date: str | None = None
    section: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)


class RetrievalMethod(str, Enum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class EvidenceStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"


class RetrievedPassage(StrictModel):
    """One retrieved chunk plus the scores that justified returning it."""

    chunk: PolicyChunk
    rank: int = Field(ge=1)
    keyword_score: float | None = None
    keyword_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    semantic_similarity: float | None = None
    matched_terms: list[str] = Field(default_factory=list)

    def to_policy_reference(self) -> PolicyReference:
        """Cite this passage verbatim. The citation is built only from document metadata."""
        c = self.chunk
        return PolicyReference(
            source_document=c.title,
            citation=f"{c.title}, {c.section}" if c.section else c.title,
            page=c.page_start,
            excerpt=c.text,
            source_url=c.source_url,
            chunk_id=c.chunk_id,
        )


class PolicyRetrievalResult(StrictModel):
    """Retrieved evidence only. Contains no recommendation or legal conclusion."""

    query: str = Field(min_length=1)
    method: RetrievalMethod
    status: EvidenceStatus
    passages: list[RetrievedPassage] = Field(default_factory=list)
    message: str = Field(min_length=1)

    @model_validator(mode="after")
    def _status_matches_passages(self) -> PolicyRetrievalResult:
        if (self.status is EvidenceStatus.SUFFICIENT) != bool(self.passages):
            raise ValueError("status must be SUFFICIENT exactly when passages are returned")
        return self


# --- Tool outputs -------------------------------------------------------------


class PiiDetectionResult(StrictModel):
    """Candidate sensitive spans from the PII detector. Candidates, not decisions."""

    record_id: str | None = Field(default=None, pattern=RECORD_ID_PATTERN)
    detector: str = Field(min_length=1)
    score_threshold: float = Field(ge=0.0, le=1.0)
    findings: list[SensitiveDataFinding] = Field(default_factory=list)


class RedactionProposal(StrictModel):
    """Recommendations for one record. Every item still needs human approval."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    recommendations: list[RedactionRecommendation] = Field(default_factory=list)
    needs_human_review: list[str] = Field(default_factory=list)  # finding ids
    requires_human_approval: Literal[True] = True
