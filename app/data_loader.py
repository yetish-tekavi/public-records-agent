"""Load and cross-validate the synthetic requests, records, and ground truth.

Requests and records are plain-text files with a ``Key: Value`` header block,
a blank line, and then the body. Expected results are JSON files.

    data/synthetic_requests/request_001.txt
    data/synthetic_records/record_001.txt
    data/expected_results/record_001.expected.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.schemas import ExpectedResult, PublicRecord, PublicRecordRequest, ReviewCase

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

REQUEST_HEADERS = {
    "request id": "request_id",
    "record id": "record_id",
    "date received": "received_date",
    "requester": "requester",
    "synthetic": "synthetic",
}
RECORD_HEADERS = {
    "record id": "record_id",
    "record type": "record_type",
    "title": "title",
    "record date": "record_date",
    "synthetic": "synthetic",
}
TRUE_VALUES = {"yes", "true"}


class DataLoadError(Exception):
    """Raised when a data file is missing, malformed, or inconsistent."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def read_text(path: Path) -> str:
    """Read UTF-8 text, tolerating a BOM and normalising CRLF to LF."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DataLoadError(path, f"cannot read file ({exc})") from exc


def parse_header_document(text: str) -> tuple[dict[str, str], str]:
    """Split a document into lower-cased header keys and the body text."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1

    headers: dict[str, str] = {}
    while i < len(lines) and lines[i].strip():
        key, sep, value = lines[i].partition(":")
        if not sep:
            raise ValueError(f"header line {i + 1} is not 'Key: Value': {lines[i]!r}")
        headers[key.strip().lower()] = value.strip()
        i += 1

    if not headers:
        raise ValueError("no header block found")
    body = "\n".join(lines[i + 1 :]).strip()
    return headers, body


def _map_headers(headers: dict[str, str], mapping: dict[str, str]) -> dict[str, object]:
    unknown = set(headers) - set(mapping)
    if unknown:
        raise ValueError(f"unknown header(s): {sorted(unknown)}")
    fields: dict[str, object] = {mapping[k]: v for k, v in headers.items()}
    if "synthetic" in fields:
        # Only an explicit yes/true marks a file as synthetic; anything else fails validation.
        fields["synthetic"] = str(fields["synthetic"]).lower() in TRUE_VALUES
    return fields


def load_request(path: Path) -> PublicRecordRequest:
    try:
        headers, body = parse_header_document(read_text(path))
        fields = _map_headers(headers, REQUEST_HEADERS)
        request = PublicRecordRequest(**fields, description=body)
    except (ValueError, ValidationError) as exc:
        raise DataLoadError(path, str(exc)) from exc
    logger.debug("Loaded request %s from %s", request.request_id, path.name)
    return request


def load_record(path: Path) -> PublicRecord:
    try:
        headers, body = parse_header_document(read_text(path))
        fields = _map_headers(headers, RECORD_HEADERS)
        record = PublicRecord(**fields, text=body, source_file=path.name)
    except (ValueError, ValidationError) as exc:
        raise DataLoadError(path, str(exc)) from exc
    logger.debug("Loaded record %s from %s", record.record_id, path.name)
    return record


def load_expected(path: Path) -> ExpectedResult:
    try:
        expected = ExpectedResult.model_validate(json.loads(read_text(path)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DataLoadError(path, str(exc)) from exc
    logger.debug("Loaded expected result for %s from %s", expected.record_id, path.name)
    return expected


def check_expected_against_record(expected: ExpectedResult, record: PublicRecord) -> None:
    """Confirm every ground-truth span points at exactly its stated text."""
    for finding in expected.findings:
        actual = record.text[finding.start : finding.end]
        if actual != finding.text:
            raise ValueError(
                f"finding {finding.finding_id} expects {finding.text!r} at "
                f"[{finding.start}:{finding.end}] but record has {actual!r}"
            )


def load_review_cases(data_dir: Path = DEFAULT_DATA_DIR) -> list[ReviewCase]:
    """Load every request with its matched record and ground truth."""
    request_dir = data_dir / "synthetic_requests"
    record_dir = data_dir / "synthetic_records"
    expected_dir = data_dir / "expected_results"

    request_paths = sorted(request_dir.glob("request_*.txt"))
    if not request_paths:
        raise DataLoadError(request_dir, "no request_*.txt files found")

    records = {r.record_id: r for r in map(load_record, sorted(record_dir.glob("record_*.txt")))}
    expected_by_record = {
        e.record_id: (e, p)
        for p in sorted(expected_dir.glob("*.expected.json"))
        for e in [load_expected(p)]
    }

    cases: list[ReviewCase] = []
    for path in request_paths:
        request = load_request(path)
        record = records.get(request.record_id)
        if record is None:
            raise DataLoadError(path, f"no record found with id {request.record_id}")
        if request.record_id not in expected_by_record:
            raise DataLoadError(path, f"no expected result found for {request.record_id}")
        expected, expected_path = expected_by_record[request.record_id]
        if expected.request_id != request.request_id:
            raise DataLoadError(
                expected_path,
                f"request_id {expected.request_id} does not match {request.request_id}",
            )
        try:
            check_expected_against_record(expected, record)
        except ValueError as exc:
            raise DataLoadError(expected_path, str(exc)) from exc
        cases.append(ReviewCase(request=request, record=record, expected=expected))

    logger.info("Loaded %d review case(s) from %s", len(cases), data_dir)
    return cases
