"""Presidio-based detect_pii against the synthetic ground truth (requires the spaCy model)."""

from __future__ import annotations

import importlib.util

import pytest

from app.data_loader import load_review_cases
from app.schemas import SensitiveCategory

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("presidio_analyzer") is None or importlib.util.find_spec("en_core_web_md") is None,
    reason="presidio-analyzer or en_core_web_md not installed",
)


@pytest.fixture(scope="module")
def detector():
    from app.tools.pii import PiiDetector

    return PiiDetector()


@pytest.mark.parametrize("case", load_review_cases(), ids=lambda c: c.record.record_id)
def test_detector_matches_ground_truth_exactly(detector, case):
    result = detector.detect(case.record.text, record_id=case.record.record_id)
    got = {(f.category, f.start, f.end) for f in result.findings}
    expected = {(f.category, f.start, f.end) for f in case.expected.findings}
    assert got == expected


def test_findings_carry_offsets_confidence_and_detector(detector):
    text = load_review_cases()[0].record.text
    for f in detector.detect(text, record_id="TEST-001").findings:
        assert text[f.start : f.end] == f.text
        assert 0.4 <= f.confidence <= 1.0
        assert f.detector.startswith("presidio:")


def test_ad_hoc_text_has_no_record_id(detector):
    result = detector.detect("Call me at (609) 555-0123 tomorrow.")
    assert result.record_id is None
    assert [f.category for f in result.findings] == [SensitiveCategory.PHONE_NUMBER]
    assert result.findings[0].finding_id == "TEXT-P01"


def test_date_of_birth_needs_context(detector):
    with_context = detector.detect("Driver DOB 03/17/1988 was not injured.")
    without_context = detector.detect("The meeting was held on 03/17/2026.")
    assert [f.category for f in with_context.findings] == [SensitiveCategory.DATE_OF_BIRTH]
    assert without_context.findings == []


def test_spaced_nj_license_and_email(detector):
    cats = {f.category for f in detector.detect("License K4821 07733 91026, email pat@example.com").findings}
    assert {SensitiveCategory.DRIVERS_LICENSE_NUMBER, SensitiveCategory.EMAIL_ADDRESS} <= cats


def test_internal_identifiers_are_not_flagged(detector):
    assert detector.detect("Asset tag PW-ASSET-0457; work order WO-2026-0193 opened on September 28.").findings == []
