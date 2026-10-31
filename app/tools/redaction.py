"""propose_redactions: turn detector findings plus retrieved policy evidence into recommendations.

This is deliberately conservative rule logic, kept separate from PII detection:

* ``REDACT`` only when (a) the category is one OPRA names categorically, (b) a supplied,
  retrieved passage explicitly names that category, and (c) detection confidence is high enough.
* Everything else becomes ``HUMAN_REVIEW_REQUIRED`` with the evidence that was found attached
  and the open questions spelled out. Nothing is ever auto-released.
* Citations come only from the supplied passages, quoted verbatim.
"""

from __future__ import annotations

import re

from app.schemas import (
    PublicRecord,
    RedactionAction,
    RedactionProposal,
    RedactionRecommendation,
    RetrievedPassage,
    SensitiveCategory,
    SensitiveDataFinding,
)

MIN_DETECTION_CONFIDENCE = 0.6

# Phrases a passage must contain to count as naming the category. Matched case-insensitively
# against whitespace-normalised passage text.
CATEGORY_POLICY_PATTERNS: dict[SensitiveCategory, re.Pattern[str]] = {
    SensitiveCategory.PHONE_NUMBER: re.compile(r"telephone numbers?"),
    SensitiveCategory.DRIVERS_LICENSE_NUMBER: re.compile(r"drivers?['’]?s?\s+licen[sc]e\s+numbers?"),
    SensitiveCategory.SOCIAL_SECURITY_NUMBER: re.compile(r"social security (account )?numbers?"),
    SensitiveCategory.DATE_OF_BIRTH: re.compile(r"month and day of birth"),
    SensitiveCategory.EMAIL_ADDRESS: re.compile(r"e\s?-?\s?mail address"),
    SensitiveCategory.STREET_ADDRESS: re.compile(r"home address"),
    SensitiveCategory.PERSON_NAME: re.compile(r"reasonable expectation of privacy"),
}

# Categories the retrieved OPRA text lists without qualification. The others are
# conditional (e.g. home addresses of Daniel's Law covered persons only, personal email
# required for government applications) or have no categorical rule (names), so a human decides.
CATEGORICAL = {
    SensitiveCategory.PHONE_NUMBER,
    SensitiveCategory.DRIVERS_LICENSE_NUMBER,
    SensitiveCategory.SOCIAL_SECURITY_NUMBER,
    SensitiveCategory.DATE_OF_BIRTH,
}

CONTEXT_QUESTIONS: dict[SensitiveCategory, str] = {
    SensitiveCategory.PERSON_NAME: (
        "Is this a private individual, and does disclosure violate a reasonable expectation of privacy?"
    ),
    SensitiveCategory.STREET_ADDRESS: (
        "Is this a home address, and is the person covered by a provision that protects it?"
    ),
    SensitiveCategory.EMAIL_ADDRESS: "Is this a personal email address required for a government application?",
    SensitiveCategory.OTHER_ID_NUMBER: "Does this identifier relate to a person, and what law protects it?",
}


# A category named inside one of these narrower provisions (e.g. the list of what a firearms
# background investigation contains, or a convict's access to victim information) is not
# evidence of a general rule for that category.
NARROW_CONTEXT = re.compile(r"background investigation|firearm|permit holder|registrant|convict|victim['’]s")
NARROW_CONTEXT_WINDOW = 250


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _general_match(passage: RetrievedPassage, pattern: re.Pattern[str]) -> re.Match[str] | None:
    """First mention of the category that is not inside a narrower provision."""
    text = _normalise(passage.chunk.text)
    for m in pattern.finditer(text):
        window = text[max(0, m.start() - NARROW_CONTEXT_WINDOW) : m.end() + NARROW_CONTEXT_WINDOW]
        if not NARROW_CONTEXT.search(window):
            return m
    return None


def _quote(passage: RetrievedPassage, pattern: re.Pattern[str], width: int = 80) -> str:
    text = re.sub(r"\s+", " ", passage.chunk.text)
    m = _general_match(passage, pattern)
    start, end = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("..." if start else "") + text[start:end].strip() + ("..." if end < len(text) else "")


def validate_findings(record: PublicRecord, findings: list[SensitiveDataFinding]) -> None:
    """Reject findings that do not point at their stated text in this record."""
    for f in findings:
        if f.record_id not in (None, record.record_id):
            raise ValueError(f"finding {f.finding_id} belongs to {f.record_id}, not {record.record_id}")
        if record.text[f.start : f.end] != f.text:
            raise ValueError(f"finding {f.finding_id} text does not match record {record.record_id} at [{f.start}:{f.end}]")


def propose_redactions(
    record: PublicRecord,
    findings: list[SensitiveDataFinding],
    policy_evidence: list[RetrievedPassage],
) -> RedactionProposal:
    validate_findings(record, findings)
    recommendations: list[RedactionRecommendation] = []

    for i, f in enumerate(findings, start=1):
        pattern = CATEGORY_POLICY_PATTERNS.get(f.category)
        supporting = [p for p in policy_evidence if _general_match(p, pattern)] if pattern else []
        refs = [p.to_policy_reference() for p in supporting]
        questions: list[str] = []

        if not supporting:
            action = RedactionAction.HUMAN_REVIEW_REQUIRED
            rationale = f"No retrieved policy passage names {f.category.value}; no policy basis established."
            questions.append("Which provision, if any, applies to this information?")
        elif f.category not in CATEGORICAL:
            action = RedactionAction.HUMAN_REVIEW_REQUIRED
            rationale = (
                f"Retrieved policy mentions this kind of information ({_quote(supporting[0], pattern)!r}), "
                "but applicability depends on context."
            )
        elif f.confidence < MIN_DETECTION_CONFIDENCE:
            action = RedactionAction.HUMAN_REVIEW_REQUIRED
            rationale = (
                f"Policy names {f.category.value}, but detection confidence {f.confidence:.2f} is below "
                f"{MIN_DETECTION_CONFIDENCE}; confirm the span really is {f.category.value}."
            )
        else:
            action = RedactionAction.REDACT
            rationale = f"Retrieved policy names this category: {_quote(supporting[0], pattern)!r}."

        if f.category in CONTEXT_QUESTIONS:
            questions.append(CONTEXT_QUESTIONS[f.category])
        if f.confidence < MIN_DETECTION_CONFIDENCE:
            questions.append(f"Detector confidence is {f.confidence:.2f}; is this span correctly identified?")

        recommendations.append(
            RedactionRecommendation(
                recommendation_id=f"{record.record_id}-R{i:02d}",
                finding_id=f.finding_id,
                action=action,
                rationale=rationale,
                policy_references=refs,
                detection_confidence=f.confidence,
                open_questions=questions,
            )
        )

    return RedactionProposal(
        record_id=record.record_id,
        recommendations=recommendations,
        needs_human_review=[
            r.finding_id for r in recommendations if r.action is RedactionAction.HUMAN_REVIEW_REQUIRED
        ],
    )
