"""Deterministic redaction: exact spans, validation, overlaps, and no collateral changes."""

from __future__ import annotations

from app.review.redact import RedactionSpan, apply_redactions, merge_spans, sha256_text

TEXT = "Call 609-555-0142 or write to 12 Oak Lane. Ask for Pat."


def span(value, replacement="[X]", start=None):
    s = TEXT.index(value) if start is None else start
    return RedactionSpan(start=s, end=s + len(value), expected_text=value, replacement=replacement, finding_ids=[value])


def test_exact_span_replacement_leaves_other_text_identical():
    out = apply_redactions(TEXT, [span("609-555-0142", "[PHONE]")])
    assert out.valid
    assert out.text == "Call [PHONE] or write to 12 Oak Lane. Ask for Pat."
    assert out.original_sha256 == sha256_text(TEXT) and out.output_sha256 == sha256_text(out.text)


def test_spans_are_applied_regardless_of_order():
    out = apply_redactions(TEXT, [span("Pat", "[N]"), span("609-555-0142", "[P]"), span("12 Oak Lane", "[A]")])
    assert out.text == "Call [P] or write to [A]. Ask for [N]."


def test_offsets_must_match_the_original_text():
    bad = RedactionSpan(start=5, end=17, expected_text="609-555-0000", replacement="[P]")
    out = apply_redactions(TEXT, [bad])
    assert not out.valid and out.text == "" and "expected '609-555-0000'" in out.errors[0]


def test_out_of_range_span_is_rejected():
    out = apply_redactions(TEXT, [RedactionSpan(start=50, end=500, expected_text="x", replacement="[X]")])
    assert not out.valid and "outside the original text" in out.errors[0]


def test_hash_mismatch_blocks_redaction():
    out = apply_redactions(TEXT, [span("Pat")], expected_sha256="0" * 64)
    assert not out.valid and "hash mismatch" in out.errors[0]


def test_overlapping_spans_are_merged_into_one_replacement():
    a = span("609-555-0142", "[PHONE]")
    b = RedactionSpan(start=a.start + 4, end=a.end + 3, expected_text=TEXT[a.start + 4 : a.end + 3], replacement="[OTHER]", finding_ids=["b"])
    out = apply_redactions(TEXT, [a, b])
    assert out.valid and len(out.applied) == 1
    assert out.text == "Call [REDACTED] write to 12 Oak Lane. Ask for Pat."  # union covers "609-555-0142 or"
    assert out.applied[0].finding_ids == sorted(["609-555-0142", "b"])


def test_adjacent_spans_stay_separate():
    a = RedactionSpan(start=5, end=8, expected_text="609", replacement="[A]")
    b = RedactionSpan(start=8, end=17, expected_text="-555-0142", replacement="[B]")
    assert len(merge_spans([a, b])) == 2
    assert apply_redactions(TEXT, [a, b]).text == "Call [A][B] or write to 12 Oak Lane. Ask for Pat."


def test_value_repeated_elsewhere_blocks_partial_redaction():
    text = "Primary 609-555-0142; backup 609-555-0142."
    out = apply_redactions(text, [RedactionSpan(start=8, end=20, expected_text="609-555-0142", replacement="[P]")])
    assert not out.valid and "still appears elsewhere" in out.errors[0]


def test_no_spans_returns_original_text():
    out = apply_redactions(TEXT, [])
    assert out.valid and out.text == TEXT
