"""ToolService: the single, validated implementation of every agent tool.

Both the MCP server and the in-process executor call these methods, so input validation,
record/chunk resolution, and error mapping are identical regardless of transport.

Tools take identifiers wherever possible (record ids, policy chunk ids) and resolve them
here, so a caller (or an LLM) cannot alter record text or fabricate a policy excerpt.
"""

from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.data_loader import DEFAULT_DATA_DIR, DataLoadError, load_record
from app.policies.extract import DEFAULT_POLICY_DIR, PolicyLoadError
from app.policies.retriever import PolicyRetriever
from app.policies.semantic import SemanticUnavailableError
from app.schemas import (
    RECORD_ID_PATTERN,
    PiiDetectionResult,
    PolicyRetrievalResult,
    PublicRecord,
    RedactionProposal,
    RetrievalMethod,
    RetrievedPassage,
    SensitiveDataFinding,
)
from app.tools.redaction import propose_redactions

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 20_000
# Only these sources may serve as cited legal authority (provenance guard, Milestone 6).
OFFICIAL_SOURCE_PREFIXES = ("https://www.nj.gov/", "https://nj.gov/")


def is_official(source_url: str | None) -> bool:
    return bool(source_url) and source_url.startswith(OFFICIAL_SOURCE_PREFIXES)


class ToolError(Exception):
    """A tool failure reported back to the caller as data, never as a crash."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchPolicyInput(_Input):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=3, ge=1, le=10)
    method: RetrievalMethod = RetrievalMethod.HYBRID


class DetectPiiInput(_Input):
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_CHARS)
    record_id: str | None = Field(default=None, pattern=RECORD_ID_PATTERN)

    @model_validator(mode="after")
    def _exactly_one(self) -> DetectPiiInput:
        if (self.text is None) == (self.record_id is None):
            raise ValueError("provide exactly one of text or record_id")
        return self


class ProposeRedactionsInput(_Input):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    findings: list[SensitiveDataFinding] = Field(default_factory=list, max_length=200)
    policy_chunk_ids: list[str] = Field(default_factory=list, max_length=20)


class CitationToCheck(_Input):
    chunk_id: str = Field(min_length=1, max_length=100)
    excerpt: str | None = Field(default=None, max_length=20_000)


class ValidateCitationsInput(_Input):
    citations: list[CitationToCheck] = Field(min_length=1, max_length=50)


class CitationCheckResult(BaseModel):
    chunk_id: str
    exists: bool
    excerpt_matches: bool | None
    section: str | None
    source_url: str | None
    official: bool = False


class CitationValidationResult(BaseModel):
    checks: list[CitationCheckResult]
    all_valid: bool


def _validate(model: type[_Input], arguments: dict[str, Any]) -> Any:
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
        raise ToolError("INVALID_INPUT", details) from exc


class ToolService:
    def __init__(
        self,
        data_dir: Path = DEFAULT_DATA_DIR,
        policy_dir: Path = DEFAULT_POLICY_DIR,
        retriever: PolicyRetriever | None = None,
        detector: Any | None = None,
        enable_semantic: bool = True,
        policy_doc_ids: list[str] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.policy_dir = policy_dir
        self.enable_semantic = enable_semantic
        self.policy_doc_ids = policy_doc_ids
        if retriever is not None:
            self.__dict__["retriever"] = retriever
        if detector is not None:
            self.__dict__["detector"] = detector

    # Heavy resources load on first use so the MCP server starts quickly.
    @cached_property
    def retriever(self) -> PolicyRetriever:
        logger.info("Loading policy retriever (semantic=%s, documents=%s)", self.enable_semantic, self.policy_doc_ids or "all")
        return PolicyRetriever.from_directory(
            self.policy_dir, enable_semantic=self.enable_semantic, doc_ids=self.policy_doc_ids
        )

    @cached_property
    def detector(self):
        from app.tools.pii import PiiDetector

        logger.info("Loading PII detector")
        return PiiDetector()

    @cached_property
    def _records(self) -> dict[str, PublicRecord]:
        records = {}
        for path in sorted((self.data_dir / "synthetic_records").glob("record_*.txt")):
            record = load_record(path)
            records[record.record_id] = record
        return records

    def get_record(self, record_id: str) -> PublicRecord:
        try:
            record = self._records.get(record_id)
        except DataLoadError as exc:
            raise ToolError("DATA_ERROR", str(exc)) from exc
        if record is None:
            raise ToolError("NOT_FOUND", f"no synthetic record with id {record_id}")
        return record

    # --- tools -----------------------------------------------------------------

    def search_policy(self, **arguments: Any) -> PolicyRetrievalResult:
        args = _validate(SearchPolicyInput, arguments)
        try:
            return self.retriever.search(args.query, method=args.method, k=args.k)
        except (PolicyLoadError, SemanticUnavailableError) as exc:
            raise ToolError("TOOL_UNAVAILABLE", str(exc)) from exc
        except ValueError as exc:
            raise ToolError("INVALID_INPUT", str(exc)) from exc

    def detect_pii(self, **arguments: Any) -> PiiDetectionResult:
        args = _validate(DetectPiiInput, arguments)
        text = args.text if args.record_id is None else self.get_record(args.record_id).text
        try:
            return self.detector.detect(text, record_id=args.record_id)
        except ToolError:
            raise
        except Exception as exc:  # the NLP stack can fail in many ways; report, don't crash
            raise ToolError("TOOL_FAILURE", f"PII detection failed: {exc}") from exc

    def propose_redactions(self, **arguments: Any) -> RedactionProposal:
        args = _validate(ProposeRedactionsInput, arguments)
        record = self.get_record(args.record_id)
        chunks = self.retriever.chunks
        position = {c.chunk_id: i for i, c in enumerate(chunks)}
        unknown = [cid for cid in args.policy_chunk_ids if cid not in position]
        if unknown:
            raise ToolError("NOT_FOUND", f"unknown policy chunk id(s): {unknown}; cite only retrieved passages")
        cited_ids = list(dict.fromkeys(args.policy_chunk_ids))
        evidence = [RetrievedPassage(chunk=chunks[position[cid]], rank=i) for i, cid in enumerate(cited_ids, start=1)]
        try:
            return propose_redactions(record, args.findings, evidence, self._section_context(cited_ids))
        except ValueError as exc:
            raise ToolError("INVALID_INPUT", str(exc)) from exc

    def _section_context(self, chunk_ids: list[str], reach: int = 2) -> list[RetrievedPassage]:
        """The chunks that follow each cited chunk within the same section (e.g. its exception list)."""
        chunks = self.retriever.chunks
        position = {c.chunk_id: i for i, c in enumerate(chunks)}
        context: dict[str, RetrievedPassage] = {}
        for cid in chunk_ids:
            i, base = position[cid], chunks[position[cid]]
            for j in range(i + 1, min(len(chunks), i + reach + 1)):
                c = chunks[j]
                if c.doc_id == base.doc_id and c.section == base.section and c.chunk_id not in chunk_ids:
                    context.setdefault(c.chunk_id, RetrievedPassage(chunk=c, rank=len(context) + 1))
        return list(context.values())

    def validate_citations(self, **arguments: Any) -> CitationValidationResult:
        """Check that each cited chunk exists in the indexed corpus and, if given, that the excerpt is verbatim."""
        args = _validate(ValidateCitationsInput, arguments)
        by_id = {c.chunk_id: c for c in self.retriever.chunks}
        checks = []
        for citation in args.citations:
            chunk = by_id.get(citation.chunk_id)
            matches = None
            if chunk is not None and citation.excerpt is not None:
                matches = " ".join(citation.excerpt.split()) in " ".join(chunk.text.split())
            checks.append(CitationCheckResult(
                chunk_id=citation.chunk_id,
                exists=chunk is not None,
                excerpt_matches=matches,
                section=chunk.section if chunk else None,
                source_url=chunk.source_url if chunk else None,
                official=chunk is not None and is_official(chunk.source_url),
            ))
        return CitationValidationResult(checks=checks, all_valid=all(c.exists and c.official and c.excerpt_matches is not False for c in checks))

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch by tool name and return a JSON-serialisable result."""
        tools = {
            "search_policy": self.search_policy,
            "detect_pii": self.detect_pii,
            "propose_redactions": self.propose_redactions,
            "validate_citations": self.validate_citations,
        }
        if name not in tools:
            raise ToolError("UNKNOWN_TOOL", f"no tool named {name!r}")
        logger.info("Tool call %s(%s)", name, ", ".join(sorted(arguments)))
        result = tools[name](**arguments)
        return result.model_dump(mode="json")
