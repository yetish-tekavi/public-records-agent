"""Extraction and chunking: synthetic unit fixtures plus checks on the real policy corpus."""

from __future__ import annotations

import re

import pytest

from app.policies.chunker import (
    DirectiveDetector,
    ExemptionsDetector,
    HandbookDetector,
    StatuteDetector,
    chunk_document,
)
from app.policies.extract import (
    HTML_HEADING_PREFIX,
    PolicyLoadError,
    _MainTextParser,
    clean_lines,
    extract_pages,
    load_manifest,
)
from app.schemas import PolicyDocument


def make_doc(style: str = "opra_statute", **overrides) -> PolicyDocument:
    fields = dict(
        doc_id="fixture_doc",
        title="Fixture Policy",
        publisher="Fixture Publisher",
        file="fixture.pdf",
        source_url="https://example.invalid/fixture.pdf",
        effective_date="as of January 1, 2026",
        retrieved_date="2026-01-02",
        section_style=style,
    )
    return PolicyDocument(**{**fields, **overrides})


# --- cleaning and HTML extraction (synthetic) ---------------------------------


def test_clean_lines_collapses_whitespace_and_strips_footers():
    patterns = [re.compile(r"^Page \d+ of 9$")]
    assert clean_lines("  First   line \n\nPage 3 of 9\n second\tline ", patterns) == [
        "First line",
        "second line",
    ]


def test_html_parser_reads_only_main_text_and_marks_bold_headings():
    parser = _MainTextParser()
    parser.feed(
        '<div id="nav"><a>Navigation link</a></div>'
        '<div class="mainText"><p><strong>How to Redact</strong></p>'
        "<p>Delete the <strong>material</strong> being redacted.</p>"
        "<ul><li>First step</li></ul></div>"
    )
    assert parser.lines == [
        f"{HTML_HEADING_PREFIX}How to Redact",
        "Delete the material being redacted.",
        "First step",
    ]


# --- section detectors (synthetic) ---------------------------------------------


def test_statute_detector_finds_headings_but_not_cross_references():
    d = StatuteDetector()
    assert d("Definitions C.47:1A-1.1 Definitions.", "") == "N.J.S.A. 47:1A-1.1 Definitions"
    assert d("section 1 of P.L.1995, c.23 (C.47:1A-1.1).", "") is None
    assert d("P.L. 1963, c. 73 ( C. 47:1A-1 et seq.) as", "") is None


def test_exemptions_detector_tracks_statute_group():
    d = ExemptionsDetector()
    assert d("N.J.S.A. 47:1A-1.1", "") == "N.J.S.A. 47:1A-1.1"
    assert d("5) Criminal investigatory records - records which are not", "") == (
        "N.J.S.A. 47:1A-1.1, exemption 5: Criminal investigatory records"
    )
    assert d("N.J.S.A. 47:1A-1.1.", "") is None  # trailing reference, not a heading


def test_handbook_detector_skips_table_of_contents_and_joins_wrapped_questions():
    d = HandbookDetector()
    assert d("What if I have to redact records? ……… 22", "") is None
    assert d("SECTION 2 – HOW OPRA WORKS", "") == "SECTION 2 – HOW OPRA WORKS"
    assert d("What happens if an employee other than the custodian receives an OPRA", "request?") == (
        "SECTION 2 – HOW OPRA WORKS: What happens if an employee other than the custodian "
        "receives an OPRA request?"
    )


def test_directive_detector_requires_increasing_numbers():
    d = DirectiveDetector()
    assert d("10 Restrictions on Access to and Use and", "") == "§ 10 Restrictions on Access to and Use and"
    assert d("5 When responding to a subpoena", "") is None  # footnote after section 10
    assert d("11 Public Disclosure of BWC Recordings", "") == "§ 11 Public Disclosure of BWC Recordings"
    assert d("3.1 Promulgation of Policies. To ensure that", "") is None
    assert d("1 Governing Principles", "") == "§ 1 Governing Principles"  # numbering may restart


# --- chunking (synthetic) ------------------------------------------------------


def test_chunks_never_cross_sections_and_keep_page_ranges():
    pages = [
        (1, ["C.47:1A-1 Legislative findings.", "Alpha text on page one."]),
        (2, ["More alpha text on page two.", "C.47:1A-2 Second section.", "Beta text."]),
    ]
    chunks = chunk_document(make_doc(), pages)
    assert [c.section for c in chunks] == ["N.J.S.A. 47:1A-1 Legislative findings", "N.J.S.A. 47:1A-2 Second section"]
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 2)
    assert (chunks[1].page_start, chunks[1].page_end) == (2, 2)
    assert chunks[0].effective_date == "as of January 1, 2026"
    assert chunks[1].chunk_id == "fixture_doc:0001"


def test_long_sections_split_with_one_line_overlap():
    lines = ["C.47:1A-1 Long section."] + [f"Sentence number {i} of the long section text." for i in range(40)]
    chunks = chunk_document(make_doc(), [(1, lines)], max_chars=300)
    assert len(chunks) > 1
    assert all(len(c.text) <= 300 for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.text.split("\n")[0] == prev.text.split("\n")[-1]


def test_short_sections_are_kept():
    pages = [(1, ["N.J.S.A. 47:1A-1.1", "27) Owner and maintenance manuals.", "28) HIPAA data - data"])]
    chunks = chunk_document(make_doc("grc_exemptions"), pages)
    assert any(c.text == "27) Owner and maintenance manuals." for c in chunks)


def test_missing_document_file_raises(tmp_path):
    with pytest.raises(PolicyLoadError, match="missing"):
        extract_pages(make_doc(), tmp_path)


def test_bad_manifest_raises(tmp_path):
    (tmp_path / "manifest.json").write_text('{"documents": [{"doc_id": "Bad ID"}]}', encoding="utf-8")
    with pytest.raises(PolicyLoadError):
        load_manifest(tmp_path)


# --- real corpus ---------------------------------------------------------------


def test_manifest_lists_official_sources():
    docs = load_manifest()
    assert {d.doc_id for d in docs} == {
        "opra_statute",
        "grc_exemptions",
        "grc_redacting",
        "grc_handbook",
        "ag_bwc_directive",
    }
    assert all(d.source_url.startswith(("https://www.nj.gov/", "https://nj.gov/")) for d in docs)


def test_every_chunk_carries_provenance(policy_chunks):
    ids = [c.chunk_id for c in policy_chunks]
    assert len(ids) == len(set(ids))
    for c in policy_chunks:
        assert c.title and c.source_url.startswith("https://")
        if c.doc_id == "grc_redacting":
            assert c.page_start is None
        else:
            assert 1 <= c.page_start <= c.page_end


def test_running_headers_and_footers_are_removed(policy_chunks):
    footer = re.compile(r"^(Page \d+ of \d+|Updated 9/3/2024 \d+|BODY WORN CAMERA POLICY \| .*)$", re.M)
    assert not [c.chunk_id for c in policy_chunks if footer.search(c.text)]


def test_chunk_text_is_verbatim_from_the_source(policy_chunks):
    """Every chunk line must exist in the extracted document text (no rewriting)."""
    docs = {d.doc_id: d for d in load_manifest()}
    source_lines = {
        doc_id: {line.removeprefix(HTML_HEADING_PREFIX) for _, lines in extract_pages(doc) for line in lines}
        for doc_id, doc in docs.items()
    }
    for c in policy_chunks:
        assert set(c.text.split("\n")) <= source_lines[c.doc_id], c.chunk_id


@pytest.mark.parametrize(
    "doc_id, section_prefix, phrase, page",
    [
        ("opra_statute", "N.J.S.A. 47:1A-1.1 Definitions", "any telephone number or driver license number of any", 7),
        ("grc_exemptions", "N.J.S.A. 47:1A-1.1, exemption 5: Criminal investigatory records", "criminal investigation", 1),
        ("grc_exemptions", "N.J.S.A. 47:1A-1.1, exemption 27", "Owner and maintenance manuals", None),
        ("grc_handbook", "SECTION 2 – HOW OPRA WORKS: What if I have to redact records?", "visually obvious method", 22),
        ("ag_bwc_directive", "§ 11 Public Disclosure of BWC Recordings", "Open Public Records Act", 36),
        ("grc_redacting", "Explaining Why A Redaction is Made", "reasonable explanation", None),
    ],
)
def test_known_passages_have_expected_sections(policy_chunks, doc_id, section_prefix, phrase, page):
    matches = [
        c
        for c in policy_chunks
        if c.doc_id == doc_id and (c.section or "").startswith(section_prefix) and phrase in c.text.replace("\n", " ")
    ]
    assert matches, f"no {doc_id} chunk in {section_prefix!r} contains {phrase!r}"
    if page is not None:
        assert any(c.page_start <= page <= c.page_end for c in matches)


def test_html_navigation_is_not_indexed(policy_chunks):
    text = "\n".join(c.text for c in policy_chunks if c.doc_id == "grc_redacting")
    assert "OPRA for Records Custodians" not in text  # breadcrumb
    assert "Copyright" not in text
