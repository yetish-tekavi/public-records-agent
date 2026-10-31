"""Keyword retrieval, evidence thresholds, and missing-policy behaviour.

Runs on the real public policy corpus in keyword mode, so no embedding model is needed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.policies.keyword import KeywordIndex, stem, tokenize
from app.policies.retriever import PolicyRetriever
from app.policies.semantic import SemanticUnavailableError
from app.schemas import EvidenceStatus, PolicyChunk, PolicyRetrievalResult, RetrievalMethod

# (query, expected doc_id, text the expected passage's section label must contain)
KNOWN_POLICY_QUESTIONS = [
    ("Is a person's telephone number or driver license number confidential?", "opra_statute", "47:1A-1.1"),
    ("phone number privacy", "opra_statute", "47:1A-1.1"),
    ("social security number redaction", "grc_redacting", "How to Redact"),
    ("How must a custodian explain why a redaction was made?", "grc_redacting", "Explaining Why"),
    ("When can body worn camera recordings be released to the public?", "ag_bwc_directive", "§ 11"),
    ("Are criminal investigatory records exempt from disclosure?", "grc_exemptions", "exemption 5"),
    ("How many business days does a custodian have to respond to an OPRA request?", "grc_handbook", "time frame"),
    ("juvenile information exemption", "grc_exemptions", "exemption 23"),
]

MISSING_POLICY_QUESTIONS = [
    "What is the speed limit on residential streets?",
    "recipe for chocolate chip cookies",
    "Federal tax deduction for home office expenses",
    "zoning variance for a backyard swimming pool",
    "What is the maximum jail sentence for shoplifting?",
    "cryptocurrency wallet seed phrase",
    "GDPR right to erasure",
]

OTHER_JURISDICTION_QUESTIONS = [
    "How do I apply for a fishing license in Pennsylvania?",
    "California Public Records Act exemptions",
    "New York FOIL response deadline",
]


def chunk(chunk_id: str, text: str, section: str | None = None) -> PolicyChunk:
    return PolicyChunk(
        chunk_id=chunk_id,
        doc_id="fixture",
        title="Fixture Policy",
        source_url="https://example.invalid/fixture",
        section=section,
        page_start=1,
        page_end=1,
        text=text,
    )


# --- tokenizer and BM25 (synthetic) ----------------------------------------------


@pytest.mark.parametrize(
    "word, expected",
    [("records", "record"), ("redacted", "redact"), ("redacting", "redact"), ("address", "address"),
     ("addresses", "address"), ("anonymous", "anonymous"), ("speed", "speed"), ("agencies", "agency")],
)
def test_stem(word, expected):
    assert stem(word) == expected


def test_tokenize_normalises_domain_abbreviations_and_keeps_citations():
    assert tokenize("Body-worn cameras and the Open Public Records Act") == ["bwc", "opra"]
    assert tokenize("My phone and SSN") == ["telephone", "social", "security", "number"]
    assert "47:1a-1.1" in tokenize("See N.J.S.A. 47:1A-1.1 for definitions")


def test_bm25_ranks_the_relevant_chunk_first():
    index = KeywordIndex([
        chunk("c1", "Custodians must respond within seven business days."),
        chunk("c2", "Telephone numbers are personal identifying information.", section="Personal information"),
        chunk("c3", "The council meets monthly to review complaints."),
    ])
    hits = index.search("Are telephone numbers personal information?")
    assert hits[0].chunk.chunk_id == "c2"
    assert hits[0].coverage == 1.0
    assert index.unknown_terms("telephone numbers in Atlantis") == ["atlantis"]


def test_bm25_returns_nothing_for_unrelated_query():
    index = KeywordIndex([chunk("c1", "Custodians must respond within seven business days.")])
    assert index.search("chocolate cookies") == []


# --- known questions and missing policy (real corpus, keyword mode) -----------------


@pytest.mark.parametrize("query, doc_id, section_part", KNOWN_POLICY_QUESTIONS)
def test_known_policy_questions_retrieve_expected_passage(keyword_retriever, query, doc_id, section_part):
    result = keyword_retriever.search(query, method="keyword", k=5)
    assert result.status is EvidenceStatus.SUFFICIENT
    found = [(p.chunk.doc_id, p.chunk.section) for p in result.passages]
    assert any(d == doc_id and section_part in (s or "") for d, s in found), found


@pytest.mark.parametrize("query", MISSING_POLICY_QUESTIONS)
def test_missing_policy_returns_insufficient(keyword_retriever, query):
    result = keyword_retriever.search(query, method="keyword")
    assert result.status is EvidenceStatus.INSUFFICIENT
    assert result.passages == []
    assert "Human review required" in result.message


@pytest.mark.parametrize("query", OTHER_JURISDICTION_QUESTIONS)
def test_other_jurisdictions_are_refused(keyword_retriever, query):
    result = keyword_retriever.search(query, method="keyword")
    assert result.status is EvidenceStatus.INSUFFICIENT
    assert "New Jersey only" in result.message


def test_passages_are_verbatim_and_convert_to_policy_references(keyword_retriever, policy_chunks):
    by_id = {c.chunk_id: c for c in policy_chunks}
    result = keyword_retriever.search("driver license number telephone number", method="keyword", k=3)
    for passage in result.passages:
        assert passage.chunk.text == by_id[passage.chunk.chunk_id].text
        ref = passage.to_policy_reference()
        assert ref.excerpt == passage.chunk.text
        assert ref.page == passage.chunk.page_start
        assert ref.source_url == passage.chunk.source_url
        assert ref.citation.startswith(passage.chunk.title)


def test_passages_report_their_evidence_scores(keyword_retriever):
    t = keyword_retriever.thresholds
    for passage in keyword_retriever.search("criminal investigatory records exempt", method="keyword").passages:
        assert passage.keyword_score >= t.keyword_min_score
        assert passage.keyword_coverage >= t.keyword_min_coverage
        assert passage.semantic_similarity is None


# --- input validation and failure handling -----------------------------------------


@pytest.mark.parametrize("query, k", [("", 3), ("   ", 3), ("x" * 501, 3), ("valid query", 0), ("valid query", 21)])
def test_invalid_search_input_is_rejected(keyword_retriever, query, k):
    with pytest.raises(ValueError):
        keyword_retriever.search(query, method="keyword", k=k)


def test_unknown_method_is_rejected(keyword_retriever):
    with pytest.raises(ValueError):
        keyword_retriever.search("telephone number", method="psychic")


def test_hybrid_without_semantic_index_falls_back_to_keyword(keyword_retriever):
    result = keyword_retriever.search("telephone number", method="hybrid")
    assert result.method is RetrievalMethod.KEYWORD


def test_semantic_without_index_raises(keyword_retriever):
    with pytest.raises(SemanticUnavailableError):
        keyword_retriever.search("telephone number", method="semantic")


def test_retrieval_result_status_must_match_passages():
    with pytest.raises(ValidationError):
        PolicyRetrievalResult(query="q", method="keyword", status="SUFFICIENT", passages=[], message="m")


def test_empty_corpus_is_rejected():
    with pytest.raises(ValueError):
        PolicyRetriever([])
