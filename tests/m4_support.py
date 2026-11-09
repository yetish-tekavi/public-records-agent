"""Shared fixtures for Milestone 4 tests: real corpus chunks, a stub retriever, and fake executors."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.data_loader import PROJECT_ROOT, load_review_cases
from app.schemas import EvidenceStatus, PolicyRetrievalResult, RetrievalMethod, RetrievedPassage
from app.tools.service import ToolError, ToolService

M4_DATA = PROJECT_ROOT / "data" / "milestone_4"
M4_CASES = {c.request.request_id: c for c in load_review_cases(M4_DATA)}


class StubRetriever:
    """Returns fixed REAL corpus chunks per query topic, so routing tests do not depend on ranking."""

    def __init__(self, chunks, routes: list[tuple[tuple[str, ...], list[str]]] | None = None) -> None:
        self.chunks = chunks
        self.by_id = {c.chunk_id: c for c in chunks}
        self.routes = routes if routes is not None else [
            (("judicial officer",), ["opra_statute:0052"]),               # ToT targeted retrieval (covered persons)
            (("telephone",), ["opra_statute:0052", "grc_exemptions:0026"]),
            (("home address",), ["opra_statute:0020", "grc_exemptions:0025"]),
        ]
        self.queries: list[str] = []

    def search(self, query, method=RetrievalMethod.HYBRID, k=5):
        self.queries.append(query)
        q = query.lower()
        for terms, ids in self.routes:
            if all(t in q for t in terms):
                passages = [RetrievedPassage(chunk=self.by_id[i], rank=n) for n, i in enumerate(ids[:k], 1)]
                return PolicyRetrievalResult(query=query, method="hybrid", status=EvidenceStatus.SUFFICIENT, passages=passages,
                                             message=f"{len(passages)} passage(s) met the evidence thresholds.")
        return PolicyRetrievalResult(query=query, method="hybrid", status=EvidenceStatus.INSUFFICIENT,
                                     message="Insufficient policy evidence. Human review required.")


class ScriptedExecutor:
    """Wraps ToolService in-process; can inject failures, delays, or altered tool results per tool."""

    transport = "in-process-test"

    def __init__(self, service: ToolService, fail: dict[str, int] | None = None, delay: dict[str, float] | None = None,
                 override: dict[str, Any] | None = None) -> None:
        self.service = service
        self.fail = dict(fail or {})
        self.delay = delay or {}
        self.override = override or {}
        self.calls: list[str] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool)
        if self.delay.get(tool):
            await asyncio.sleep(self.delay[tool])
        if self.fail.get(tool, 0) > 0:
            self.fail[tool] -= 1
            raise ToolError("TOOL_FAILURE", f"simulated {tool} failure")
        if tool in self.override:
            return self.override[tool](arguments)
        return await asyncio.to_thread(self.service.call, tool, arguments)


def write_case(root: Path, num: str, text: str, findings: list[tuple[str, str]]) -> Path:
    """Create a one-record synthetic dataset (request, record, ground truth) under ``root``."""
    for sub in ("synthetic_requests", "synthetic_records", "expected_results"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "synthetic_records" / f"record_{num}.txt").write_text(
        f"Record ID: TEST-{num}\nRecord Type: call_transcript\nTitle: Fixture\nRecord Date: 2026-11-01\nSynthetic: yes\n\n{text}\n", encoding="utf-8")
    (root / "synthetic_requests" / f"request_{num}.txt").write_text(
        f"Request ID: OPRA-TEST-{num}\nRecord ID: TEST-{num}\nDate Received: 2026-11-02\nRequester: Fixture\nSynthetic: yes\n\nPlease send it.\n",
        encoding="utf-8")
    fs = [{"finding_id": f"TEST-{num}-F{i:02d}", "record_id": f"TEST-{num}", "category": cat, "text": val,
           "start": text.index(val), "end": text.index(val) + len(val), "confidence": 0.9} for i, (cat, val) in enumerate(findings, 1)]
    (root / "expected_results" / f"record_{num}.expected.json").write_text(json.dumps(
        {"request_id": f"OPRA-TEST-{num}", "record_id": f"TEST-{num}", "requires_redaction": bool(fs), "findings": fs}), encoding="utf-8")
    return root
