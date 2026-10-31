"""Semantic retrieval over policy chunks using a local ChromaDB vector store.

Embeddings are computed here and passed to Chroma explicitly, so the embedding model can be
swapped (tests use a deterministic offline embedder) without Chroma persisting model config.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.data_loader import PROJECT_ROOT
from app.schemas import PolicyChunk

logger = logging.getLogger(__name__)

DEFAULT_INDEX_DIR = PROJECT_ROOT / "outputs" / "policy_index"
COLLECTION_NAME = "nj_policy_chunks"


class SemanticUnavailableError(RuntimeError):
    """Raised when ChromaDB or the embedding model cannot be loaded."""


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MiniLMEmbedder:
    """all-MiniLM-L6-v2 via Chroma's bundled ONNX runtime (~80 MB, downloaded on first use)."""

    name = "chroma-default-all-MiniLM-L6-v2"

    def __init__(self) -> None:
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        except ImportError as exc:
            raise SemanticUnavailableError("chromadb is not installed") from exc
        self._fn = DefaultEmbeddingFunction()

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return [list(map(float, v)) for v in self._fn(texts)]
        except Exception as exc:  # model download or ONNX runtime failure
            raise SemanticUnavailableError(f"embedding model failed: {exc}") from exc


class HashingEmbedder:
    """Deterministic bag-of-words embedder for offline tests. Not semantically meaningful."""

    name = "hashing-test-embedder"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            v = [0.0] * self.dim
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                v[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return vectors


@dataclass(frozen=True)
class SemanticHit:
    chunk: PolicyChunk
    similarity: float  # cosine similarity, 1.0 = identical direction


def _fingerprint(embedder_name: str, chunks: list[PolicyChunk]) -> str:
    h = hashlib.sha256(embedder_name.encode())
    for c in chunks:
        h.update(c.chunk_id.encode())
        h.update(c.text.encode())
    return h.hexdigest()[:32]


class SemanticIndex:
    """Chroma collection of chunk embeddings; rebuilt automatically when chunks change."""

    def __init__(
        self,
        chunks: list[PolicyChunk],
        embedder: Embedder | None = None,
        persist_dir: Path | None = DEFAULT_INDEX_DIR,
    ) -> None:
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise SemanticUnavailableError("chromadb is not installed") from exc
        if not chunks:
            raise ValueError("SemanticIndex needs at least one chunk")

        self.embedder = embedder or MiniLMEmbedder()
        self._chunks = {c.chunk_id: c for c in chunks}
        settings = Settings(anonymized_telemetry=False)
        if persist_dir is None:
            client = chromadb.EphemeralClient(settings=settings)
        else:
            persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(persist_dir), settings=settings)

        fingerprint = _fingerprint(self.embedder.name, chunks)
        name = f"{COLLECTION_NAME}_{fingerprint[:8]}"
        existing = {c.name: c for c in client.list_collections()}
        self._collection = client.get_or_create_collection(
            name=name,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"fingerprint": fingerprint, "embedder": self.embedder.name},
        )
        self.reused = name in existing and self._collection.count() == len(chunks)
        if self.reused:
            logger.info("Reusing semantic index %s (%d chunks)", name, len(chunks))
        else:
            self._build(chunks)
        if persist_dir is not None:
            # Drop indexes built by the same embedder from an older version of the documents.
            for stale_name, stale in existing.items():
                if (
                    stale_name != name
                    and stale_name.startswith(COLLECTION_NAME)
                    and (stale.metadata or {}).get("embedder") == self.embedder.name
                ):
                    client.delete_collection(stale_name)
                    logger.info("Deleted stale semantic index %s", stale_name)

    def _build(self, chunks: list[PolicyChunk], batch: int = 64) -> None:
        logger.info("Building semantic index with %s for %d chunks", self.embedder.name, len(chunks))
        for i in range(0, len(chunks), batch):
            part = chunks[i : i + batch]
            self._collection.upsert(
                ids=[c.chunk_id for c in part],
                embeddings=self.embedder.embed([f"{c.section or ''}\n{c.text}" for c in part]),
                metadatas=[{"doc_id": c.doc_id} for c in part],
            )

    def search(self, query: str, k: int = 5) -> list[SemanticHit]:
        result = self._collection.query(
            query_embeddings=self.embedder.embed([query]),
            n_results=min(k, len(self._chunks)),
            include=["distances"],
        )
        return [
            SemanticHit(self._chunks[chunk_id], 1.0 - float(distance))
            for chunk_id, distance in zip(result["ids"][0], result["distances"][0])
        ]
