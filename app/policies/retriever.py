"""Policy retrieval with explicit evidence thresholds.

The retriever returns verbatim passages and the scores that justified them. When no passage
clears the thresholds it returns ``INSUFFICIENT`` with no passages, so downstream steps must
fall back to "human review required" rather than cite weak or unrelated text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.policies.chunker import load_policy_chunks
from app.policies.extract import DEFAULT_POLICY_DIR
from app.policies.keyword import KeywordHit, KeywordIndex, tokenize
from app.policies.semantic import (
    DEFAULT_INDEX_DIR,
    Embedder,
    SemanticHit,
    SemanticIndex,
    SemanticUnavailableError,
)
from app.schemas import (
    EvidenceStatus,
    PolicyChunk,
    PolicyRetrievalResult,
    RetrievalMethod,
    RetrievedPassage,
)

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 500
MAX_K = 20
RRF_K = 60

SUFFICIENT_MESSAGE = (
    "{n} passage(s) met the evidence thresholds. Passages are verbatim source text, "
    "not legal conclusions; a human reviewer must evaluate them."
)
INSUFFICIENT_MESSAGE = (
    "Insufficient policy evidence: no passage met the retrieval thresholds. Human review required."
)
OUT_OF_SCOPE_MESSAGE = (
    "Insufficient policy evidence: the query refers to {place}, but the policy corpus covers "
    "New Jersey only. Human review required."
)

# The corpus is New Jersey law and guidance. Semantic similarity cannot tell one state's
# exemption list from another's, so queries naming another jurisdiction are refused outright.
OTHER_JURISDICTIONS = re.compile(
    r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|"
    r"georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|"
    r"massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
    r"new hampshire|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
    r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|"
    r"virginia|washington|west virginia|wisconsin|wyoming|gdpr|european union|canada|"
    r"united kingdom)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvidenceThresholds:
    """Minimum scores for a passage to count as evidence. Calibrated on the bundled corpus."""

    keyword_min_score: float = 6.0
    keyword_min_coverage: float = 0.6
    keyword_min_terms: int = 2
    keyword_allow_unknown_terms: bool = False
    semantic_min_similarity: float = 0.55


class PolicyRetriever:
    def __init__(
        self,
        chunks: list[PolicyChunk],
        semantic: SemanticIndex | None = None,
        thresholds: EvidenceThresholds | None = None,
        candidate_pool: int = 25,
    ) -> None:
        self.chunks = chunks
        self.keyword = KeywordIndex(chunks)
        self.semantic = semantic
        self.thresholds = thresholds or EvidenceThresholds()
        self.candidate_pool = candidate_pool

    @classmethod
    def from_directory(
        cls,
        policy_dir: Path = DEFAULT_POLICY_DIR,
        enable_semantic: bool = True,
        embedder: Embedder | None = None,
        index_dir: Path | None = DEFAULT_INDEX_DIR,
        thresholds: EvidenceThresholds | None = None,
    ) -> PolicyRetriever:
        chunks = load_policy_chunks(policy_dir)
        semantic = None
        if enable_semantic:
            try:
                semantic = SemanticIndex(chunks, embedder=embedder, persist_dir=index_dir)
            except SemanticUnavailableError as exc:
                logger.warning("Semantic retrieval unavailable, keyword only: %s", exc)
        return cls(chunks, semantic=semantic, thresholds=thresholds)

    def _keyword_ok(self, hit: KeywordHit | None, n_terms: int, unknown: list[str]) -> bool:
        t = self.thresholds
        return (
            hit is not None
            and (t.keyword_allow_unknown_terms or not unknown)
            and hit.score >= t.keyword_min_score
            and hit.coverage >= t.keyword_min_coverage
            and len(hit.matched_terms) >= min(t.keyword_min_terms, n_terms)
        )

    def _semantic_ok(self, hit: SemanticHit | None) -> bool:
        return hit is not None and hit.similarity >= self.thresholds.semantic_min_similarity

    def search(
        self,
        query: str,
        method: RetrievalMethod | str = RetrievalMethod.HYBRID,
        k: int = 5,
    ) -> PolicyRetrievalResult:
        query = " ".join(query.split())
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > MAX_QUERY_CHARS:
            raise ValueError(f"query exceeds {MAX_QUERY_CHARS} characters")
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be between 1 and {MAX_K}")
        method = RetrievalMethod(method)

        if method is not RetrievalMethod.KEYWORD and self.semantic is None:
            if method is RetrievalMethod.SEMANTIC:
                raise SemanticUnavailableError("semantic index not loaded")
            logger.info("Hybrid requested without a semantic index; using keyword only")
            method = RetrievalMethod.KEYWORD

        place = OTHER_JURISDICTIONS.search(query)
        if place:
            logger.info("Policy search refused: query names %r, outside the corpus", place.group(0))
            return PolicyRetrievalResult(
                query=query,
                method=method,
                status=EvidenceStatus.INSUFFICIENT,
                message=OUT_OF_SCOPE_MESSAGE.format(place=place.group(0)),
            )

        n_terms = len(set(tokenize(query)))
        unknown = self.keyword.unknown_terms(query)
        if unknown:
            logger.info("Query terms absent from the policy corpus: %s", unknown)
        kw_hits: dict[str, KeywordHit] = {}
        sem_hits: dict[str, SemanticHit] = {}
        if method is not RetrievalMethod.SEMANTIC:
            kw_hits = {h.chunk.chunk_id: h for h in self.keyword.search(query, self.candidate_pool)}
        if method is not RetrievalMethod.KEYWORD:
            sem_hits = {h.chunk.chunk_id: h for h in self.semantic.search(query, self.candidate_pool)}

        # Rank candidates: by the single score, or by reciprocal-rank fusion for hybrid.
        fused: dict[str, float] = {}
        for hits in (kw_hits, sem_hits):
            for rank, chunk_id in enumerate(hits, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        ordered = sorted(fused, key=lambda cid: (-fused[cid], cid))

        passages: list[RetrievedPassage] = []
        for chunk_id in ordered:
            kw, sem = kw_hits.get(chunk_id), sem_hits.get(chunk_id)
            supported = (method is not RetrievalMethod.SEMANTIC and self._keyword_ok(kw, n_terms, unknown)) or (
                method is not RetrievalMethod.KEYWORD and self._semantic_ok(sem)
            )
            if not supported:
                continue
            chunk = (kw or sem).chunk
            passages.append(
                RetrievedPassage(
                    chunk=chunk,
                    rank=len(passages) + 1,
                    keyword_score=round(kw.score, 3) if kw else None,
                    keyword_coverage=round(kw.coverage, 3) if kw else None,
                    semantic_similarity=round(sem.similarity, 3) if sem else None,
                    matched_terms=kw.matched_terms if kw else [],
                )
            )
            if len(passages) == k:
                break

        status = EvidenceStatus.SUFFICIENT if passages else EvidenceStatus.INSUFFICIENT
        logger.info(
            "Policy search method=%s status=%s query=%r results=%s",
            method.value,
            status.value,
            query,
            [p.chunk.chunk_id for p in passages],
        )
        return PolicyRetrievalResult(
            query=query,
            method=method,
            status=status,
            passages=passages,
            message=SUFFICIENT_MESSAGE.format(n=len(passages)) if passages else INSUFFICIENT_MESSAGE,
        )
