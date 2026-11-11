"""Deterministic evidence analysis shared by the Compliance & QA Agent and the ToT controller.

Everything here reads retrieved passage text and the synthetic record; nothing is inferred
from outside the corpus. The classification rules are deliberately conservative:

* MISSING     - no retrieved passage names the category (outside a narrower provision).
* CLEAR       - the category is one OPRA lists without qualification, a passage names it with
                operative language, detection confidence is adequate, and no retrieved exclusion
                clause matches the record context.
* CONFLICTING - a retrieved passage requires redaction while another retrieved clause excludes
                information of this kind, and the record context suggests the exclusion may apply.
* AMBIGUOUS   - evidence exists but its application depends on conditions (who the person is,
                balancing tests, definitions without an operative rule) or detection is uncertain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import PolicyChunk, RetrievedPassage, SensitiveCategory, SensitiveDataFinding
from app.tools.redaction import (
    CATEGORICAL,
    CATEGORY_POLICY_PATTERNS,
    MIN_DETECTION_CONFIDENCE,
    SOURCE_AUTHORITY,
    _general_match,
)
from app.workflows.state import EvidenceAssessment, EvidenceDecision

# Language that directs withholding. "shall not include" is deliberately absent: in the statute it
# carves items out of definitions ("... shall not include any street address ... of a public agency").
OPERATIVE = re.compile(
    r"shall redact|redact from|exempt from (?:public )?access|shall not (?:be )?(?:disclosed|deemed|considered)|"
    r"deemed to be confidential|not be considered a government record"
)
RESTRICTED_CLASS = re.compile(r"judicial officer|prosecutor|law enforcement officer|child protective investigator")
DEFINITION = re.compile(r"[“\"]personal identifying information[”\"]\s+(?:means|shall (?:not )?include)|\bmeans any\b|shall mean")
BALANCING = re.compile(r"reasonable expectation of privacy")
WINDOW = 250

# Retrieved exclusion clauses and the record context that suggests they may apply.
EXCLUSIONS: dict[SensitiveCategory, tuple[re.Pattern[str], re.Pattern[str]]] = {
    SensitiveCategory.PHONE_NUMBER: (
        re.compile(r"shall not include[^.]{0,120}telephone number of a public agency"),
        re.compile(r"\b(main line|department|agency|township|office line|switchboard|dispatch center)\b"),
    ),
}
EXCLUSION_ASSUMPTION = {
    SensitiveCategory.PHONE_NUMBER: "The number belongs to a private person, not a public agency (a retrieved clause excludes agency numbers).",
}

# Record terms accepted as indicating a person covered by the restricted-class provision.
COVERED_PERSON_TERMS = [
    "judge", "justice", "judicial officer", "prosecutor", "assistant prosecutor", "police officer",
    "law enforcement officer", "detective", "sheriff", "state trooper", "child protective investigator",
]


def norm(text: str) -> str:
    return " ".join(text.split()).lower()


def authority(chunk: PolicyChunk) -> int:
    return SOURCE_AUTHORITY.get(chunk.doc_id, 9)


@dataclass(frozen=True)
class ProvisionAnalysis:
    chunk: PolicyChunk
    names_category: bool
    operative: bool
    restricted_class: bool
    definition: bool
    balancing: bool
    quote: str


def analyse_passage(chunk: PolicyChunk, category: SensitiveCategory) -> ProvisionAnalysis:
    pattern = CATEGORY_POLICY_PATTERNS.get(category)
    m = _general_match(RetrievedPassage(chunk=chunk, rank=1), pattern) if pattern else None
    if m is None:
        return ProvisionAnalysis(chunk, False, False, False, False, False, "")
    text = norm(chunk.text)
    around = text[max(0, m.start() - WINDOW) : m.end() + WINDOW]
    after = text[m.start() : m.end() + WINDOW]
    raw = " ".join(chunk.text.split())
    quote = raw[max(0, m.start() - 80) : m.end() + 120]
    # Restricted only if a covered-class term qualifies the mention before any general "of any person"
    # (in 47:1A-5(a) the phone clause ends "of any person" before the home-address clause begins).
    restricted = RESTRICTED_CLASS.search(after)
    general = re.search(r"of any person", after)
    return ProvisionAnalysis(
        chunk=chunk,
        names_category=True,
        operative=bool(OPERATIVE.search(around)),
        restricted_class=bool(restricted) and not (general and general.start() < restricted.start()),
        definition=bool(DEFINITION.search(around)),
        balancing=bool(BALANCING.search(around)),
        quote=quote,
    )


def supporting(passages: list[PolicyChunk], category: SensitiveCategory) -> list[ProvisionAnalysis]:
    analyses = [analyse_passage(c, category) for c in passages]
    return sorted((a for a in analyses if a.names_category), key=lambda a: authority(a.chunk))


def record_context(record_text: str, finding: SensitiveDataFinding, width: int = 200) -> str:
    return record_text[max(0, finding.start - width) : finding.end + width]


def find_terms(record_text: str, finding: SensitiveDataFinding, terms: list[str]) -> str | None:
    """A verbatim record snippet containing one of ``terms`` near the finding, or None."""
    context = record_context(record_text, finding)
    for term in terms:
        m = re.search(rf"\b{re.escape(term)}\b", context, re.IGNORECASE)
        if m:
            start, end = max(0, m.start() - 40), min(len(context), m.end() + 40)
            while start > 0 and not context[start - 1].isspace():  # snap the quote to word boundaries
                start -= 1
            while end < len(context) and not context[end].isspace():
                end += 1
            return context[start:end].strip()
    return None


def covered_person_evidence(record_text: str, finding: SensitiveDataFinding) -> str | None:
    return find_terms(record_text, finding, COVERED_PERSON_TERMS)


AGENCY_CONTEXT_TERMS = ["main line", "department", "agency", "township", "office line", "switchboard", "dispatch center"]


def applicable_exclusion(finding: SensitiveDataFinding, passages: list[PolicyChunk], record_text: str) -> str | None:
    """chunk_id of a retrieved exclusion clause whose condition the record context suggests, or None."""
    exclusion = EXCLUSIONS.get(finding.category)
    if not exclusion:
        return None
    clause, context = exclusion
    excluded = [c.chunk_id for c in passages if clause.search(norm(c.text))]
    if excluded and context.search(record_context(record_text, finding).lower()):
        return excluded[0]
    return None


def assess_finding(finding: SensitiveDataFinding, passages: list[PolicyChunk], record_text: str) -> EvidenceAssessment:
    support = supporting(passages, finding.category)
    ids = [a.chunk.chunk_id for a in support]
    if not support:
        return EvidenceAssessment(
            finding_id=finding.finding_id, category=finding.category, decision=EvidenceDecision.MISSING,
            reasons=[f"No retrieved passage names {finding.category.value}."],
        )

    excluded = applicable_exclusion(finding, passages, record_text)
    if excluded:
        return EvidenceAssessment(
            finding_id=finding.finding_id, category=finding.category, decision=EvidenceDecision.CONFLICTING,
            reasons=[f"Retrieved exclusion clause ({excluded}) may apply: the record context suggests an agency number."],
            supporting_chunk_ids=ids + ([excluded] if excluded not in ids else []),
        )

    operative_general = [a for a in support if a.operative and not a.restricted_class]
    if finding.category in CATEGORICAL and operative_general and finding.confidence >= MIN_DETECTION_CONFIDENCE:
        return EvidenceAssessment(
            finding_id=finding.finding_id, category=finding.category, decision=EvidenceDecision.CLEAR,
            reasons=[f"{operative_general[0].chunk.section or operative_general[0].chunk.title} names this category with operative language."],
            supporting_chunk_ids=ids,
        )

    reasons = []
    if finding.category not in CATEGORICAL:
        reasons.append(f"{finding.category.value} is not listed without qualification; application depends on conditions.")
    if any(a.restricted_class for a in support):
        reasons.append("A retrieved provision applies only to specific classes of person.")
    if any(a.definition or a.balancing for a in support):
        reasons.append("Retrieved evidence includes a definition or balancing test rather than a direct rule.")
    if finding.confidence < MIN_DETECTION_CONFIDENCE:
        reasons.append(f"Detection confidence {finding.confidence:.2f} is below {MIN_DETECTION_CONFIDENCE}.")
    if not operative_general and finding.category in CATEGORICAL:
        reasons.append("No retrieved passage applies operative language to this category generally.")
    return EvidenceAssessment(
        finding_id=finding.finding_id, category=finding.category, decision=EvidenceDecision.AMBIGUOUS,
        reasons=reasons or ["Evidence present but conditions apply."], supporting_chunk_ids=ids,
    )


DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
MISSED_PATTERNS = {
    "phone-like number": re.compile(r"\(?\b\d{3}\)?[ .-]\d{3}-\d{4}\b"),
    # 7-digit local number; not part of a longer hyphenated code such as PO-2026-555-0123.
    "7-digit phone-like number": re.compile(r"(?<![\d-])\b\d{3}-?\d{4}\b(?![\d-])"),
    "number spelled out in words": re.compile(rf"\b{DIGIT_WORD}(?:[\s,.-]+{DIGIT_WORD}){{6,}}\b", re.IGNORECASE),
    "SSN-like number": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email-like text": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "IPv4-like address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


def possibly_missed(record_text: str, findings: list[SensitiveDataFinding]) -> list[str]:
    """Pattern sweep for values the detector did not report. Flags for a human, not findings."""
    flags = []
    for label, pattern in MISSED_PATTERNS.items():
        for m in pattern.finditer(record_text):
            if not any(f.start <= m.start() < f.end or m.start() <= f.start < m.end() for f in findings):
                flags.append(f"Possible undetected {label} at [{m.start()}:{m.end()}]: {m.group(0)!r}")
    return flags
