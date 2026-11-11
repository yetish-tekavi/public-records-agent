"""The three specialist agents. Each has a bounded tool-use loop over the existing MCP tools.

These agents are deterministic: their tool choices follow fixed rules, not an LLM. They are
called by the Orchestrator's LangGraph nodes and return structured results only.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.agents.executors import ToolExecutor
from app.agents.planners import POLICY_QUERIES, evidence_names_category
from app.schemas import (
    EvidenceStatus,
    PolicyChunk,
    PolicyRetrievalResult,
    RedactionProposal,
    SensitiveCategory,
    SensitiveDataFinding,
)
from app.tools.redaction import CATEGORICAL
from app.tools.service import ToolError, is_official
from app.workflows import evidence as ev
from app.workflows.state import (
    CategoryEvidence,
    CitationCheck,
    QAReport,
    RecordAnalysisResult,
    ToolCallRecord,
    WorkflowConfig,
)


class ToolCaller:
    """Calls a tool through the executor with a timeout, recording every call."""

    def __init__(self, executor: ToolExecutor, agent: str, timeout_s: float) -> None:
        self.executor = executor
        self.agent = agent
        self.timeout_s = timeout_s
        self.records: list[ToolCallRecord] = []

    def _summary_args(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "propose_redactions":
            return {"record_id": arguments.get("record_id"), "finding_ids": [f["finding_id"] for f in arguments.get("findings", [])],
                    "policy_chunk_ids": arguments.get("policy_chunk_ids", [])}
        if tool == "validate_citations":
            return {"chunk_ids": [c["chunk_id"] for c in arguments.get("citations", [])]}
        return dict(arguments)

    async def call(self, tool: str, arguments: dict[str, Any], summarise=lambda r: "ok") -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(self.executor.call(tool, arguments), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            self._record(tool, arguments, "timeout", f"no response within {self.timeout_s}s", started)
            raise ToolError("TIMEOUT", f"{tool} did not respond within {self.timeout_s}s") from None
        except ToolError as exc:
            self._record(tool, arguments, "error", f"{exc.code}: {exc.message}", started)
            raise
        except Exception as exc:  # unexpected executor failure: report as a tool failure, never crash the graph
            self._record(tool, arguments, "error", f"TOOL_FAILURE: {exc}", started)
            raise ToolError("TOOL_FAILURE", str(exc)) from exc
        self._record(tool, arguments, "ok", summarise(result), started)
        return result

    def _record(self, tool: str, arguments: dict[str, Any], status: str, summary: str, started: float) -> None:
        self.records.append(ToolCallRecord(agent=self.agent, tool=tool, arguments=self._summary_args(tool, arguments),
                                           status=status, summary=summary[:500],
                                           duration_ms=int((time.monotonic() - started) * 1000)))

    def take(self) -> list[ToolCallRecord]:
        records, self.records = self.records, []
        return records


def _search_summary(result: dict[str, Any]) -> str:
    return f"{result['status']}: " + (", ".join(p["chunk"]["chunk_id"] for p in result["passages"]) or result["message"])


class RecordAnalysisAgent:
    """Detects candidate sensitive information. Retries tool failures; never fabricates findings."""

    name = "record_analysis"

    def __init__(self, executor: ToolExecutor, config: WorkflowConfig) -> None:
        self.tools = ToolCaller(executor, self.name, config.tool_timeout_s)
        self.max_attempts = config.max_record_attempts

    async def run(self, record_id: str) -> RecordAnalysisResult:
        errors: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = await self.tools.call(
                    "detect_pii", {"record_id": record_id},
                    lambda r: f"{len(r['findings'])} finding(s): " + ", ".join(f"{f['category']} ({f['confidence']})" for f in r["findings"]),
                )
            except ToolError as exc:
                errors.append(f"attempt {attempt}: {exc.code}: {exc.message}")
                continue
            findings = [SensitiveDataFinding.model_validate(f) for f in result["findings"]]
            return RecordAnalysisResult(status="OK", attempts=attempt, findings=findings, errors=errors)
        return RecordAnalysisResult(status="FAILED", attempts=self.max_attempts, errors=errors)


class PolicyResearchAgent:
    """Retrieves policy evidence per category (in parallel), reformulating once when needed."""

    name = "policy_research"

    def __init__(self, executor: ToolExecutor, config: WorkflowConfig) -> None:
        self.tools = ToolCaller(executor, self.name, config.tool_timeout_s)
        self.max_queries = config.max_queries_per_category
        self.k = config.search_k

    async def _search(self, query: str) -> PolicyRetrievalResult:
        result = await self.tools.call("search_policy", {"query": query, "k": self.k}, _search_summary)
        return PolicyRetrievalResult.model_validate(result)

    @staticmethod
    def _accept(out: CategoryEvidence, result: PolicyRetrievalResult) -> PolicyRetrievalResult:
        """Provenance guard: only official sources become evidence; others are recorded and dropped."""
        kept = []
        for p in result.passages:
            if not is_official(p.chunk.source_url):
                out.excluded_sources.append(f"{p.chunk.chunk_id} ({p.chunk.source_url})")
            elif p.chunk.chunk_id not in {c.chunk_id for c in out.passages}:
                out.passages.append(p.chunk)
                kept.append(p)
            else:
                kept.append(p)
        return result.model_copy(update={"passages": kept}) if kept else result.model_copy(
            update={"passages": [], "status": EvidenceStatus.INSUFFICIENT})

    async def research_category(self, category: SensitiveCategory) -> CategoryEvidence:
        queries = POLICY_QUERIES.get(category, [f"What New Jersey law applies to {category.value.lower().replace('_', ' ')}?"])
        out = CategoryEvidence(category=category, status="MISSING")
        for query in queries[: self.max_queries]:
            out.queries.append(query)
            try:
                result = self._accept(out, await self._search(query))
            except ToolError as exc:
                out.errors.append(f"{exc.code}: {exc.message}")
                continue
            out.retrieval_status.append(result.status)
            if evidence_names_category(result.model_dump(mode="json"), category):
                out.status = "FOUND"
            # For categories OPRA lists outright, keep looking until an operative, general passage is found:
            # an exemption list naming the category is not the same as the provision that directs redaction.
            needs_operative = category in CATEGORICAL and not any(
                (a := ev.analyse_passage(c, category)).operative and not a.restricted_class for c in out.passages)
            if out.status == "FOUND" and not needs_operative:
                break
        if out.status != "FOUND" and out.errors and not out.passages:
            out.status = "ERROR"
        return out

    async def research(self, categories: list[SensitiveCategory]) -> list[CategoryEvidence]:
        return list(await asyncio.gather(*(self.research_category(c) for c in categories)))

    async def targeted_search(self, category: SensitiveCategory, query: str, previous: CategoryEvidence | None) -> CategoryEvidence:
        """One extra retrieval, requested by the Orchestrator (missing evidence) or the ToT controller."""
        out = previous.model_copy(deep=True) if previous else CategoryEvidence(category=category, status="MISSING")
        out.queries.append(query)
        out.targeted_retry = True
        try:
            result = self._accept(out, await self._search(query))
        except ToolError as exc:
            out.errors.append(f"{exc.code}: {exc.message}")
            return out
        out.retrieval_status.append(result.status)
        if evidence_names_category(result.model_dump(mode="json"), category):
            out.status = "FOUND"
        return out


class ComplianceQAAgent:
    """Proposes via the rule tool, validates citations, assesses evidence, and critiques ToT branches."""

    name = "compliance_qa"

    def __init__(self, executor: ToolExecutor, config: WorkflowConfig) -> None:
        self.tools = ToolCaller(executor, self.name, config.tool_timeout_s)

    async def validate(self, citations: list[dict[str, Any]], tools: ToolCaller | None = None) -> list[CitationCheck]:
        if not citations:
            return []
        caller = tools or self.tools
        result = await caller.call(
            "validate_citations", {"citations": citations},
            lambda r: f"{sum(c['exists'] and c.get('official', False) and c['excerpt_matches'] is not False for c in r['checks'])}/{len(r['checks'])} valid",
        )
        return [CitationCheck.model_validate(c) for c in result["checks"]]

    async def review(self, record_id: str, record_text: str, findings: list[SensitiveDataFinding],
                     evidence: list[CategoryEvidence]) -> QAReport:
        report = QAReport(possible_missed=ev.possibly_missed(record_text, findings))
        by_category: dict[SensitiveCategory, list[PolicyChunk]] = {e.category: e.passages for e in evidence}
        chunk_ids = list(dict.fromkeys(c.chunk_id for e in evidence for c in e.passages))[:20]

        if findings:
            try:
                result = await self.tools.call(
                    "propose_redactions",
                    {"record_id": record_id, "findings": [f.model_dump(mode="json") for f in findings], "policy_chunk_ids": chunk_ids},
                    lambda r: ", ".join(f"{x['finding_id']}={x['action']}" for x in r["recommendations"]),
                )
                report.proposal = RedactionProposal.model_validate(result)
            except ToolError as exc:
                report.errors.append(f"propose_redactions: {exc.code}: {exc.message}")

        if report.proposal:
            refs = {ref.chunk_id: ref.excerpt for r in report.proposal.recommendations for ref in r.policy_references if ref.chunk_id}
            try:
                report.citation_checks = await self.validate([{"chunk_id": cid, "excerpt": ex} for cid, ex in refs.items()])
                report.invalid_citations = [c.chunk_id for c in report.citation_checks if not c.valid]
            except ToolError as exc:
                report.errors.append(f"validate_citations: {exc.code}: {exc.message}")
                report.invalid_citations = list(refs)  # unverifiable citations are treated as invalid

        report.assessments = [ev.assess_finding(f, by_category.get(f.category, []), record_text) for f in findings]
        return report
