"""Deterministic BM25 keyword retrieval over policy chunks (no external dependencies)."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.schemas import PolicyChunk

TOKEN_RE = re.compile(r"\d+:\d+[a-z]?-\d+(?:\.\d+)?|[a-z0-9]+")

# Domain abbreviations and synonyms, applied identically to documents and queries so that
# "body worn camera" matches "BWC" and "phone" matches "telephone". Keep this list small
# and documented: every entry changes what counts as a keyword match.
PHRASE_NORMALISATION = [
    (re.compile(r"body[\s-]+worn[\s-]+cameras?"), " bwc "),
    (re.compile(r"open public records act"), " opra "),
    (re.compile(r"\bssns?\b"), " social security number "),
    (re.compile(r"\bdob\b"), " date of birth "),
]
TOKEN_SYNONYMS = {"phone": "telephone", "phones": "telephone", "licence": "license"}
STOPWORDS = frozenset(
    """a an and are as at be been being but by can could did do does for from had has have how
    i if in into is it its may me might must my no not of on or our shall should so such than that
    the their them then there these they this those to under upon was we were what when where which
    who whom why will with would you your about any all also each other some""".split()
)


def stem(token: str) -> str:
    """A deliberately small suffix stripper; enough to match 'records'/'record', 'redacted'/'redact'."""
    if len(token) <= 4 or token[0].isdigit():
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith("sses"):
        return token[:-2]
    if token.endswith(("ss", "us", "is", "eed")):
        return token
    for suffix in ("ing", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    text = text.lower()
    for pattern, replacement in PHRASE_NORMALISATION:
        text = pattern.sub(replacement, text)
    return [
        stem(TOKEN_SYNONYMS.get(t, t))
        for t in TOKEN_RE.findall(text)
        if len(t) > 1 and t not in STOPWORDS
    ]


@dataclass(frozen=True)
class KeywordHit:
    chunk: PolicyChunk
    score: float
    coverage: float  # fraction of distinct query terms present in the chunk
    matched_terms: list[str]


class KeywordIndex:
    """Okapi BM25 over each chunk's text, with its section label counted twice as a heading boost."""

    def __init__(self, chunks: list[PolicyChunk], k1: float = 1.5, b: float = 0.75) -> None:
        if not chunks:
            raise ValueError("KeywordIndex needs at least one chunk")
        self.chunks = chunks
        self.k1, self.b = k1, b
        self._tf = [Counter(tokenize(f"{c.section or ''}\n{c.section or ''}\n{c.text}")) for c in chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = sum(self._len) / len(self._len)
        df = Counter(term for tf in self._tf for term in tf)
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def unknown_terms(self, query: str) -> list[str]:
        """Query terms that appear nowhere in the corpus, e.g. another state's name."""
        return [t for t in dict.fromkeys(tokenize(query)) if t not in self._idf]

    def search(self, query: str, k: int = 5) -> list[KeywordHit]:
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms:
            return []
        hits = []
        for i, tf in enumerate(self._tf):
            matched = [t for t in terms if t in tf]
            if not matched:
                continue
            norm = self.k1 * (1 - self.b + self.b * self._len[i] / self._avg_len)
            score = sum(self._idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in matched)
            hits.append(KeywordHit(self.chunks[i], score, len(matched) / len(terms), matched))
        hits.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return hits[:k]
