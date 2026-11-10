"""Deterministic text redaction over validated character spans of the ORIGINAL record text.

No model rewrites the document: the output is built by slicing the original text and inserting
replacement tokens at validated spans. Overlapping spans are merged; adjacent spans are kept
separate. The function never touches the source file.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field


class RedactionSpan(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    expected_text: str = Field(min_length=1)
    replacement: str = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)
    source: str = "recommendation"  # or "reviewer_modification"


class RedactionOutput(BaseModel):
    original_sha256: str
    output_sha256: str
    text: str
    applied: list[RedactionSpan]
    errors: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_spans(text: str, spans: list[RedactionSpan]) -> list[str]:
    """Each span must lie inside the text and match its expected original text exactly."""
    errors = []
    for s in spans:
        if s.end <= s.start or s.end > len(text):
            errors.append(f"span [{s.start}:{s.end}] is outside the original text (length {len(text)})")
        elif text[s.start : s.end] != s.expected_text:
            errors.append(f"span [{s.start}:{s.end}] expected {s.expected_text!r} but the original has {text[s.start:s.end]!r}")
    return errors


def merge_spans(spans: list[RedactionSpan]) -> list[RedactionSpan]:
    """Merge overlapping spans (a single replacement covers the union); keep adjacent spans separate."""
    merged: list[RedactionSpan] = []
    for s in sorted(spans, key=lambda x: (x.start, x.end)):
        if merged and s.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = last.model_copy(update={
                "end": max(last.end, s.end),
                "replacement": last.replacement if last.replacement == s.replacement else "[REDACTED]",
                "finding_ids": sorted(set(last.finding_ids) | set(s.finding_ids)),
                "source": "merged",
            })
        else:
            merged.append(s.model_copy())
    return merged  # expected_text of merged spans is recomputed from the original by apply_redactions


def apply_redactions(original: str, spans: list[RedactionSpan], expected_sha256: str | None = None) -> RedactionOutput:
    original_sha = sha256_text(original)
    errors = []
    if expected_sha256 and expected_sha256 != original_sha:
        errors.append("the record text no longer matches the version that was analysed (hash mismatch)")
    errors += validate_spans(original, spans)
    if errors:
        return RedactionOutput(original_sha256=original_sha, output_sha256="", text="", applied=[], errors=errors)

    merged = merge_spans(spans)
    for m in merged:
        m.expected_text = original[m.start : m.end]

    pieces, cursor = [], 0
    for m in merged:
        pieces.append(original[cursor : m.start])
        pieces.append(m.replacement)
        cursor = m.end
    pieces.append(original[cursor:])
    output = "".join(pieces)

    # Deterministic validation: every untouched segment is byte-identical to the original,
    # and no redacted value survives anywhere else in the output.
    cursor, out_pos = 0, 0
    for m in merged:
        segment = original[cursor : m.start]
        if output[out_pos : out_pos + len(segment)] != segment:
            errors.append(f"unredacted text before [{m.start}:{m.end}] was altered")
        out_pos += len(segment) + len(m.replacement)
        cursor = m.end
    if output[out_pos:] != original[cursor:]:
        errors.append("unredacted text after the last span was altered")
    for m in merged:
        if m.expected_text in output:
            errors.append(f"redacted value {m.expected_text!r} still appears elsewhere in the output; add it to the plan")
    return RedactionOutput(original_sha256=original_sha, output_sha256=sha256_text(output) if not errors else "",
                           text=output if not errors else "", applied=merged, errors=errors)
