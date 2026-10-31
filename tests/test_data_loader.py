from __future__ import annotations

import copy
import re

import pytest

from app.data_loader import (
    DataLoadError,
    load_record,
    load_request,
    load_review_cases,
    parse_header_document,
)
from app.schemas import SensitiveCategory
from tests.conftest import VALID_EXPECTED, VALID_RECORD, VALID_REQUEST

# --- parse_header_document -------------------------------------------------


def test_parse_header_document_splits_headers_and_body():
    headers, body = parse_header_document("\nKey One: a\nKey Two: b: c\n\nBody line 1\nBody line 2\n")
    assert headers == {"key one": "a", "key two": "b: c"}
    assert body == "Body line 1\nBody line 2"


def test_parse_header_document_rejects_malformed_header():
    with pytest.raises(ValueError, match="not 'Key: Value'"):
        parse_header_document("Record ID: TEST-900\nthis line has no colon\n\nbody")


def test_parse_header_document_rejects_missing_headers():
    with pytest.raises(ValueError, match="no header block"):
        parse_header_document("\n\n")


# --- the real synthetic dataset ---------------------------------------------


def test_real_dataset_loads_three_cases(real_data_dir):
    cases = load_review_cases(real_data_dir)
    assert [c.request.request_id for c in cases] == ["OPRA-TEST-001", "OPRA-TEST-002", "OPRA-TEST-003"]
    for case in cases:
        assert case.request.record_id == case.record.record_id == case.expected.record_id
        assert case.request.synthetic and case.record.synthetic


def test_real_dataset_covers_required_scenarios(real_data_dir):
    cases = load_review_cases(real_data_dir)
    categories = {f.category for c in cases for f in c.expected.findings}
    assert SensitiveCategory.PHONE_NUMBER in categories
    assert SensitiveCategory.DRIVERS_LICENSE_NUMBER in categories
    no_pii = [c for c in cases if not c.expected.requires_redaction]
    assert len(no_pii) == 1 and no_pii[0].expected.findings == []


def test_real_dataset_offsets_match_record_text(real_data_dir):
    for case in load_review_cases(real_data_dir):
        for finding in case.expected.findings:
            assert case.record.text[finding.start : finding.end] == finding.text


def test_real_dataset_phone_numbers_are_fictional(real_data_dir):
    """Data-safety guard: phone numbers must use the reserved 555-0100..0199 range."""
    for case in load_review_cases(real_data_dir):
        for finding in case.expected.findings:
            if finding.category is SensitiveCategory.PHONE_NUMBER:
                assert re.search(r"555-01\d\d$", finding.text), finding.text


# --- loader behaviour on synthetic fixtures ---------------------------------


def test_fixture_case_loads(make_data_dir):
    cases = load_review_cases(make_data_dir())
    assert len(cases) == 1
    assert cases[0].expected.findings[0].text == "555-0100"


def test_crlf_and_bom_do_not_shift_offsets(make_data_dir, tmp_path):
    data_dir = make_data_dir()
    path = data_dir / "synthetic_records" / "record_900.txt"
    path.write_bytes(b"\xef\xbb\xbf" + VALID_RECORD.replace("\n", "\r\n").encode("utf-8"))
    record = load_record(path)
    assert "\r" not in record.text
    assert load_review_cases(data_dir)[0].record.text == record.text


def test_record_not_marked_synthetic_is_rejected(make_data_dir):
    data_dir = make_data_dir(record=VALID_RECORD.replace("Synthetic: yes", "Synthetic: no"))
    with pytest.raises(DataLoadError, match="record_900.txt"):
        load_review_cases(data_dir)


def test_record_missing_synthetic_header_is_rejected(make_data_dir):
    data_dir = make_data_dir(record=VALID_RECORD.replace("Synthetic: yes\n", ""))
    with pytest.raises(DataLoadError, match="synthetic"):
        load_review_cases(data_dir)


def test_unknown_header_is_rejected(make_data_dir):
    data_dir = make_data_dir(request=VALID_REQUEST.replace("Synthetic: yes", "Synthetic: yes\nPriority: high"))
    with pytest.raises(DataLoadError, match="unknown header"):
        load_request(data_dir / "synthetic_requests" / "request_900.txt")


def test_request_for_missing_record_is_rejected(make_data_dir):
    data_dir = make_data_dir(request=VALID_REQUEST.replace("Record ID: TEST-900", "Record ID: TEST-901"))
    with pytest.raises(DataLoadError, match="no record found with id TEST-901"):
        load_review_cases(data_dir)


def test_expected_offset_mismatch_is_rejected(make_data_dir):
    expected = copy.deepcopy(VALID_EXPECTED)
    expected["findings"][0].update(start=19, end=27)
    with pytest.raises(DataLoadError, match="expects '555-0100'"):
        load_review_cases(make_data_dir(expected=expected))


def test_expected_request_id_mismatch_is_rejected(make_data_dir):
    expected = copy.deepcopy(VALID_EXPECTED)
    expected["request_id"] = "OPRA-TEST-901"
    with pytest.raises(DataLoadError, match="does not match"):
        load_review_cases(make_data_dir(expected=expected))


def test_malformed_expected_json_is_rejected(make_data_dir):
    data_dir = make_data_dir()
    (data_dir / "expected_results" / "record_900.expected.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DataLoadError, match="record_900.expected.json"):
        load_review_cases(data_dir)


def test_empty_data_dir_is_rejected(tmp_path):
    with pytest.raises(DataLoadError, match="no request_"):
        load_review_cases(tmp_path)
