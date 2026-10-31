"""Semantic index plumbing (offline hashing embedder) and real-model integration checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.policies.retriever import EvidenceThresholds, PolicyRetriever
from app.policies.semantic import HashingEmbedder, SemanticIndex
from app.schemas import EvidenceStatus, PolicyChunk

chromadb = pytest.importorskip("chromadb")


def chunk(chunk_id: str, text: str) -> PolicyChunk:
    return PolicyChunk(
        chunk_id=chunk_id,
        doc_id="fixture",
        title="Fixture Policy",
        source_url="https://example.invalid/fixture",
        page_start=1,
        page_end=1,
        text=text,
    )


FIXTURE_CHUNKS = [
    chunk("c1", "Custodians must respond to requests within seven business days."),
    chunk("c2", "Telephone numbers and driver license numbers are personal identifying information."),
    chunk("c3", "The council publishes an annual report of complaints received."),
]


def test_semantic_index_returns_closest_chunk(tmp_path):
    index = SemanticIndex(FIXTURE_CHUNKS, embedder=HashingEmbedder(), persist_dir=tmp_path)
    hits = index.search("telephone numbers and driver license numbers", k=2)
    assert hits[0].chunk.chunk_id == "c2"
    assert hits[0].similarity > hits[1].similarity
    assert -1.0 <= hits[1].similarity <= 1.0


def test_semantic_index_is_reused_then_rebuilt_when_chunks_change(tmp_path):
    first = SemanticIndex(FIXTURE_CHUNKS, embedder=HashingEmbedder(), persist_dir=tmp_path)
    assert first.reused is False
    second = SemanticIndex(FIXTURE_CHUNKS, embedder=HashingEmbedder(), persist_dir=tmp_path)
    assert second.reused is True

    changed = FIXTURE_CHUNKS[:2] + [chunk("c3", "Revised text about annual reports.")]
    third = SemanticIndex(changed, embedder=HashingEmbedder(), persist_dir=tmp_path)
    assert third.reused is False
    from chromadb.config import Settings

    client = chromadb.PersistentClient(path=str(tmp_path), settings=Settings(anonymized_telemetry=False))
    assert len(client.list_collections()) == 1  # stale index removed


def test_retriever_semantic_mode_applies_similarity_threshold():
    index = SemanticIndex(FIXTURE_CHUNKS, embedder=HashingEmbedder(), persist_dir=None)
    retriever = PolicyRetriever(FIXTURE_CHUNKS, semantic=index, thresholds=EvidenceThresholds(semantic_min_similarity=0.5))

    hit = retriever.search("telephone numbers and driver license numbers", method="semantic")
    assert hit.status is EvidenceStatus.SUFFICIENT
    assert hit.passages[0].chunk.chunk_id == "c2"
    assert hit.passages[0].keyword_score is None

    miss = retriever.search("chocolate chip cookie recipe", method="semantic")
    assert miss.status is EvidenceStatus.INSUFFICIENT


def test_hybrid_mode_reports_both_scores():
    index = SemanticIndex(FIXTURE_CHUNKS, embedder=HashingEmbedder(), persist_dir=None)
    retriever = PolicyRetriever(FIXTURE_CHUNKS, semantic=index, thresholds=EvidenceThresholds(keyword_min_score=0.1))
    result = retriever.search("telephone numbers driver license", method="hybrid")
    top = result.passages[0]
    assert top.chunk.chunk_id == "c2"
    assert top.keyword_score is not None and top.semantic_similarity is not None


# --- real embedding model (skipped until the model has been downloaded once) ---------

MODEL_CACHE = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
needs_model = pytest.mark.skipif(
    not MODEL_CACHE.exists(), reason="all-MiniLM-L6-v2 not downloaded; run `python -m app.policy_search --demo` once"
)


@pytest.fixture(scope="module")
def hybrid_retriever():
    return PolicyRetriever.from_directory()


@needs_model
@pytest.mark.semantic_model
@pytest.mark.parametrize(
    "query, doc_id, section_part",
    [
        ("Is a person's telephone number or driver license number confidential?", "grc_exemptions", "exemption 20"),
        ("How should a custodian redact an electronic document?", "grc_redacting", "How to Redact"),
        ("When can body worn camera recordings be released to the public?", "ag_bwc_directive", "§ 11"),
        ("Can a person who wants to remain anonymous ask the officer to stop recording?", "ag_bwc_directive", "§ 4.6"),
    ],
)
def test_hybrid_known_questions(hybrid_retriever, query, doc_id, section_part):
    result = hybrid_retriever.search(query, method="hybrid", k=5)
    assert result.status is EvidenceStatus.SUFFICIENT
    found = [(p.chunk.doc_id, p.chunk.section) for p in result.passages]
    assert any(d == doc_id and section_part in (s or "") for d, s in found), found


@needs_model
@pytest.mark.semantic_model
@pytest.mark.parametrize(
    "query",
    [
        "What is the speed limit on residential streets?",
        "recipe for chocolate chip cookies",
        "What is the maximum jail sentence for shoplifting?",
        "How long are hospital medical records retained in California?",
    ],
)
def test_hybrid_missing_policy(hybrid_retriever, query):
    result = hybrid_retriever.search(query, method="hybrid")
    assert result.status is EvidenceStatus.INSUFFICIENT
