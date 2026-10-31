from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    ExpectedResult,
    PolicyReference,
    PublicRecord,
    PublicRecordRequest,
    RedactionAction,
    RedactionRecommendation,
    SensitiveCategory,
    SensitiveDataFinding,
)


def make_request(**overrides) -> PublicRecordRequest:
    fields = dict(
        request_id="OPRA-TEST-900",
        record_id="TEST-900",
        received_date="2026-01-05",
        requester="Fixture Requester",
        description="Please send the record.",
        synthetic=True,
    )
    return PublicRecordRequest(**{**fields, **overrides})


def make_record(**overrides) -> PublicRecord:
    fields = dict(
        record_id="TEST-900",
        record_type="call_transcript",
        title="Fixture call",
        record_date="2026-01-02",
        text="Caller: Reach me at 555-0100 please.",
        synthetic=True,
    )
    return PublicRecord(**{**fields, **overrides})


def make_finding(**overrides) -> SensitiveDataFinding:
    fields = dict(
        finding_id="TEST-900-F01",
        record_id="TEST-900",
        category="PHONE_NUMBER",
        text="555-0100",
        start=20,
        end=28,
    )
    return SensitiveDataFinding(**{**fields, **overrides})


def make_policy_ref(**overrides) -> PolicyReference:
    fields = dict(
        source_document="fixture_policy.pdf",
        citation="Fixture Act 1:1",
        page=1,
        excerpt="Fixture excerpt text.",
    )
    return PolicyReference(**{**fields, **overrides})


# --- PublicRecordRequest ---------------------------------------------------


def test_valid_request_parses_date():
    request = make_request()
    assert request.received_date.isoformat() == "2026-01-05"


@pytest.mark.parametrize("bad_id", ["OPRA-001", "opra-test-001", "OPRA-TEST-1", "REQ-TEST-001"])
def test_request_rejects_bad_request_id(bad_id):
    with pytest.raises(ValidationError):
        make_request(request_id=bad_id)


def test_request_must_be_synthetic():
    with pytest.raises(ValidationError):
        make_request(synthetic=False)


def test_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        make_request(priority="high")


# --- PublicRecord ----------------------------------------------------------


def test_valid_record():
    record = make_record()
    assert record.record_type.value == "call_transcript"


def test_record_must_be_synthetic():
    with pytest.raises(ValidationError):
        make_record(synthetic=False)


def test_record_rejects_unknown_record_type():
    with pytest.raises(ValidationError):
        make_record(record_type="evidence_locker_export")


def test_record_rejects_empty_text():
    with pytest.raises(ValidationError):
        make_record(text="")


def test_record_preserves_text_whitespace():
    record = make_record(text="  indented line\n")
    assert record.text == "  indented line\n"


# --- SensitiveDataFinding --------------------------------------------------


def test_valid_finding_defaults():
    finding = make_finding()
    assert finding.category is SensitiveCategory.PHONE_NUMBER
    assert finding.detector == "ground_truth"
    assert finding.confidence == 1.0


def test_finding_rejects_end_before_start():
    with pytest.raises(ValidationError, match="greater than start"):
        make_finding(start=28, end=20)


def test_finding_rejects_span_text_length_mismatch():
    with pytest.raises(ValidationError, match="does not match text length"):
        make_finding(end=30)


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_finding_rejects_out_of_range_confidence(confidence):
    with pytest.raises(ValidationError):
        make_finding(confidence=confidence)


def test_finding_rejects_unknown_category():
    with pytest.raises(ValidationError):
        make_finding(category="FAVORITE_COLOR")


# --- PolicyReference -------------------------------------------------------


def test_policy_reference_requires_excerpt():
    with pytest.raises(ValidationError):
        make_policy_ref(excerpt="")


def test_policy_reference_rejects_page_zero():
    with pytest.raises(ValidationError):
        make_policy_ref(page=0)


def test_policy_reference_page_is_optional():
    assert make_policy_ref(page=None).page is None


# --- RedactionRecommendation -----------------------------------------------


def test_redact_without_policy_reference_is_rejected():
    with pytest.raises(ValidationError, match="HUMAN_REVIEW_REQUIRED"):
        RedactionRecommendation(
            recommendation_id="R1", finding_id="TEST-900-F01", action="REDACT", rationale="PII"
        )


def test_redact_with_policy_reference_is_accepted():
    rec = RedactionRecommendation(
        recommendation_id="R1",
        finding_id="TEST-900-F01",
        action="REDACT",
        rationale="Phone number",
        policy_references=[make_policy_ref()],
    )
    assert rec.action is RedactionAction.REDACT
    assert rec.requires_human_approval is True


def test_human_review_required_needs_no_policy_reference():
    rec = RedactionRecommendation(
        recommendation_id="R1",
        finding_id="TEST-900-F01",
        action="HUMAN_REVIEW_REQUIRED",
        rationale="No policy basis retrieved",
    )
    assert rec.policy_references == []


def test_human_approval_cannot_be_disabled():
    with pytest.raises(ValidationError):
        RedactionRecommendation(
            recommendation_id="R1",
            finding_id="TEST-900-F01",
            action="HUMAN_REVIEW_REQUIRED",
            rationale="x",
            requires_human_approval=False,
        )


# --- ExpectedResult --------------------------------------------------------


def test_expected_result_with_findings():
    expected = ExpectedResult(
        request_id="OPRA-TEST-900",
        record_id="TEST-900",
        requires_redaction=True,
        findings=[make_finding()],
    )
    assert len(expected.findings) == 1


def test_expected_result_with_no_findings():
    expected = ExpectedResult(
        request_id="OPRA-TEST-900", record_id="TEST-900", requires_redaction=False
    )
    assert expected.findings == []


@pytest.mark.parametrize("requires_redaction, findings", [(True, []), (False, "one")])
def test_expected_result_requires_redaction_must_match_findings(requires_redaction, findings):
    with pytest.raises(ValidationError, match="requires_redaction"):
        ExpectedResult(
            request_id="OPRA-TEST-900",
            record_id="TEST-900",
            requires_redaction=requires_redaction,
            findings=[make_finding()] if findings == "one" else [],
        )


def test_expected_result_rejects_duplicate_finding_ids():
    with pytest.raises(ValidationError, match="unique"):
        ExpectedResult(
            request_id="OPRA-TEST-900",
            record_id="TEST-900",
            requires_redaction=True,
            findings=[make_finding(), make_finding()],
        )


def test_expected_result_rejects_finding_from_other_record():
    with pytest.raises(ValidationError, match="belongs to"):
        ExpectedResult(
            request_id="OPRA-TEST-900",
            record_id="TEST-900",
            requires_redaction=True,
            findings=[make_finding(record_id="TEST-901")],
        )
