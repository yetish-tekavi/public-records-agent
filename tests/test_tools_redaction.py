"""propose_redactions rule logic, on synthetic fixtures only."""

from __future__ import annotations

import pytest

from app.schemas import PolicyChunk, PublicRecord, RedactionAction, RetrievedPassage, SensitiveDataFinding
from app.tools.redaction import propose_redactions

TEXT = "Caller Pat Example lives at 12 Oak Lane and can be reached at 555-0199."
RECORD = PublicRecord(
    record_id="TEST-900", record_type="call_transcript", title="Fixture", record_date="2026-01-01",
    text=TEXT, synthetic=True,
)


def finding(fid: str, category: str, text: str, confidence: float = 0.9) -> SensitiveDataFinding:
    start = TEXT.index(text)
    return SensitiveDataFinding(finding_id=fid, record_id="TEST-900", category=category, text=text,
                                start=start, end=start + len(text), confidence=confidence)


def passage(chunk_id: str, text: str) -> RetrievedPassage:
    return RetrievedPassage(
        chunk=PolicyChunk(chunk_id=chunk_id, doc_id="fixture", title="Fixture Statute", section="§ 1 Personal information",
                          source_url="https://example.invalid/statute", page_start=3, page_end=3, text=text),
        rank=1,
    )


PHONE = finding("F1", "PHONE_NUMBER", "555-0199")
NAME = finding("F2", "PERSON_NAME", "Pat Example")
ADDRESS = finding("F3", "STREET_ADDRESS", "12 Oak Lane")
GENERAL = passage("fx:0001", "that portion of any document which discloses any telephone number or driver license number of any person")
NARROW = passage("fx:0002", "information contained in a firearms background investigation shall include telephone number of any applicant")
PRIVACY = passage("fx:0003", "would violate the citizen's reasonable expectation of privacy")
DANIELS = passage("fx:0004", "that portion of any document which discloses the home address of any active judicial officer")


def only(proposal):
    [rec] = proposal.recommendations
    return rec


def test_categorical_finding_with_general_policy_is_redact():
    rec = only(propose_redactions(RECORD, [PHONE], [GENERAL]))
    assert rec.action is RedactionAction.REDACT
    assert [r.chunk_id for r in rec.policy_references] == ["fx:0001"]
    assert rec.policy_references[0].excerpt == GENERAL.chunk.text  # verbatim
    assert "telephone number" in rec.rationale
    assert rec.requires_human_approval is True


def test_no_evidence_means_human_review_without_citations():
    proposal = propose_redactions(RECORD, [PHONE], [])
    rec = only(proposal)
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED
    assert rec.policy_references == []
    assert "no policy basis" in rec.rationale
    assert proposal.needs_human_review == ["F1"]


def test_category_mentioned_only_in_narrow_provision_is_not_a_basis():
    rec = only(propose_redactions(RECORD, [PHONE], [NARROW]))
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED
    assert rec.policy_references == []


def test_low_detection_confidence_preserves_uncertainty():
    low = finding("F1", "PHONE_NUMBER", "555-0199", confidence=0.45)
    rec = only(propose_redactions(RECORD, [low], [GENERAL]))
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED
    assert rec.detection_confidence == 0.45
    assert rec.policy_references  # evidence still attached for the reviewer
    assert any("0.45" in q for q in rec.open_questions)


@pytest.mark.parametrize("f, evidence", [(NAME, PRIVACY), (ADDRESS, DANIELS)])
def test_contextual_categories_go_to_human_review_with_evidence(f, evidence):
    rec = only(propose_redactions(RECORD, [f], [evidence]))
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED
    assert [r.chunk_id for r in rec.policy_references] == [evidence.chunk.chunk_id]
    assert "depends on context" in rec.rationale
    assert rec.open_questions


def test_unrelated_evidence_is_not_cited():
    rec = only(propose_redactions(RECORD, [PHONE], [PRIVACY, DANIELS]))
    assert rec.policy_references == []


def test_never_auto_releases():
    proposal = propose_redactions(RECORD, [PHONE, NAME, ADDRESS], [GENERAL, PRIVACY, NARROW])
    assert all(r.action is not RedactionAction.RELEASE for r in proposal.recommendations)
    assert [r.recommendation_id for r in proposal.recommendations] == ["TEST-900-R01", "TEST-900-R02", "TEST-900-R03"]


def test_findings_that_do_not_match_the_record_are_rejected():
    tampered = PHONE.model_copy(update={"text": "555-0100"})
    with pytest.raises(ValueError, match="does not match"):
        propose_redactions(RECORD, [tampered], [GENERAL])


def test_findings_from_another_record_are_rejected():
    other = PHONE.model_copy(update={"record_id": "TEST-901"})
    with pytest.raises(ValueError, match="belongs to TEST-901"):
        propose_redactions(RECORD, [other], [GENERAL])
