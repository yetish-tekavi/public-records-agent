"""Shared fixtures. Every fixture here is synthetic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data_loader import DEFAULT_DATA_DIR

VALID_RECORD = """Record ID: TEST-900
Record Type: call_transcript
Title: Fixture call
Record Date: 2026-01-02
Synthetic: yes

Caller: Reach me at 555-0100 please.
"""

VALID_REQUEST = """Request ID: OPRA-TEST-900
Record ID: TEST-900
Date Received: 2026-01-05
Requester: Fixture Requester
Synthetic: yes

Please send the fixture call record.
"""

VALID_EXPECTED = {
    "request_id": "OPRA-TEST-900",
    "record_id": "TEST-900",
    "requires_redaction": True,
    "findings": [
        {
            "finding_id": "TEST-900-F01",
            "record_id": "TEST-900",
            "category": "PHONE_NUMBER",
            "text": "555-0100",
            "start": 20,
            "end": 28,
        }
    ],
}


@pytest.fixture
def real_data_dir() -> Path:
    return DEFAULT_DATA_DIR


@pytest.fixture(scope="session")
def policy_chunks():
    """Chunks of the real (public, official) policy documents. Extracted once per session."""
    from app.policies.chunker import load_policy_chunks

    return load_policy_chunks()


@pytest.fixture(scope="session")
def keyword_retriever(policy_chunks):
    """Keyword-only retriever over the real corpus; needs no embedding model."""
    from app.policies.retriever import PolicyRetriever

    return PolicyRetriever(policy_chunks)


@pytest.fixture
def make_data_dir(tmp_path: Path):
    """Build a one-case data directory, letting a test override any file."""

    def _make(
        record: str = VALID_RECORD,
        request: str = VALID_REQUEST,
        expected: dict | None = None,
    ) -> Path:
        for sub in ("synthetic_requests", "synthetic_records", "expected_results"):
            (tmp_path / sub).mkdir(exist_ok=True)
        (tmp_path / "synthetic_records" / "record_900.txt").write_text(record, encoding="utf-8")
        (tmp_path / "synthetic_requests" / "request_900.txt").write_text(request, encoding="utf-8")
        (tmp_path / "expected_results" / "record_900.expected.json").write_text(
            json.dumps(VALID_EXPECTED if expected is None else expected), encoding="utf-8"
        )
        return tmp_path

    return _make
