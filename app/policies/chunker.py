"""Split extracted policy text into verbatim, section-labelled chunks.

Each document style has its own heading detector. Detectors only *label* text with a
section drawn from the document itself; they never rewrite the passage text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from app.data_loader import PROJECT_ROOT
from app.policies.extract import (
    DEFAULT_POLICY_DIR,
    HTML_HEADING_PREFIX,
    Page,
    extract_pages,
    load_manifest,
)
from app.schemas import PolicyChunk, PolicyDocument

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 900
OVERLAP_LINES = 1
DEFAULT_CACHE_DIR = PROJECT_ROOT / "outputs" / "policy_index"

# A detector sees the current line and the next line and returns a new section label or None.
SectionDetector = Callable[[str, str], str | None]


def _shorten(title: str, limit: int = 70) -> str:
    title = title.strip().rstrip(":;,")
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


class StatuteDetector:
    """OPRA readable version: section headings look like ``C.47:1A-1.1 Definitions.``

    Lettered subsections (``a. The custodian ...``) become ``N.J.S.A. 47:1A-5(a)``. A subsection
    may follow a short margin note on the same line (``Fees for copies b. (1) A copy ...``).
    A letter is accepted only if it is the next one in sequence, so lettered lists inside a
    subsection (which restart at "a.") are not mistaken for subsections.
    """

    pattern = re.compile(r"(?<![(\w.])C\.(47:1A-\d+(?:\.\d+)?)\s+(\S.*)$")
    subsection = re.compile(r"^(?:[A-Z][A-Za-z ,'’-]{0,40}? )?([a-z])\.\s+[A-Z(]")

    def __init__(self) -> None:
        self.section: str | None = None
        self.title = ""
        self.next_letter = "a"

    def __call__(self, line: str, next_line: str) -> str | None:
        m = self.pattern.search(line)
        if m:
            self.section, self.title, self.next_letter = m.group(1), _shorten(m.group(2).rstrip(".")), "a"
            return f"N.J.S.A. {self.section} {self.title}"
        s = self.subsection.match(line)
        if s and self.section and s.group(1) == self.next_letter:
            self.next_letter = chr(ord(self.next_letter) + 1)
            return f"N.J.S.A. {self.section}({s.group(1)}) {self.title}"
        return None


class ExemptionsDetector:
    """GRC exemptions list: statute group headings followed by numbered items ``5) ...``."""

    group = re.compile(
        r"^(N\.J\.S\.A\. 47:1A-[\w.()\-]*[\w)](?: \([^)]*\))?"
        r"|Executive Order No\. \d+ \([^)]*\))(?: exemptions that are still active:?)?$"
    )
    item = re.compile(r"^(\d{1,2})\)\s+(.+)$")

    def __init__(self) -> None:
        self.current_group = "OPRA Exemptions"

    def __call__(self, line: str, next_line: str) -> str | None:
        g = self.group.match(line)
        if g:
            self.current_group = g.group(1)
            return self.current_group
        i = self.item.match(line)
        if i:
            title = i.group(2).split(" - ")[0]
            return f"{self.current_group}, exemption {i.group(1)}: {_shorten(title)}"
        return None


class HandbookDetector:
    """GRC handbook: ``SECTION N – ...`` headings and question-form subheadings."""

    section = re.compile(r"^SECTION \d+ [–-] .+$")
    question_start = re.compile(r"^(What|How|Who|When|Can|Do|Does|Are|Is|Why|Where|May|Should|Must)\b")

    def __init__(self) -> None:
        self.current_section: str | None = None

    def __call__(self, line: str, next_line: str) -> str | None:
        if "…" in line or "…" in next_line:  # table of contents
            return None
        if self.section.match(line):
            self.current_section = line
            return line
        if self.question_start.match(line) and len(line) < 120:
            if line.endswith("?"):
                question = line
            elif next_line.endswith("?") and len(line) + len(next_line) < 170:
                question = f"{line} {next_line}"
            else:
                return None
            return f"{self.current_section}: {question}" if self.current_section else question
        return None


class DirectiveDetector:
    """AG directive: numbered policy sections such as ``11 Public Disclosure of BWC Recordings``.

    Section numbers must increase (or restart at 1), which filters out footnote lines.
    """

    pattern = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?)\.?\s+([A-Z][^\n]{2,90})$")

    def __init__(self) -> None:
        self.last: tuple[int, ...] = ()

    def __call__(self, line: str, next_line: str) -> str | None:
        m = self.pattern.match(line)
        if not m:
            return None
        number = tuple(int(x) for x in m.group(1).split("."))
        if number != (1,) and number <= self.last:
            return None
        self.last = number
        # Subsection headings run into their body text: "3.1 Promulgation of ... Procedures. To ensure"
        title = m.group(2).split(". ")[0].rstrip(".")
        return f"§ {m.group(1)} {_shorten(title)}"


class HtmlHeadingDetector:
    def __call__(self, line: str, next_line: str) -> str | None:
        return line[len(HTML_HEADING_PREFIX) :] if line.startswith(HTML_HEADING_PREFIX) else None


DETECTORS: dict[str, Callable[[], SectionDetector]] = {
    "opra_statute": StatuteDetector,
    "grc_exemptions": ExemptionsDetector,
    "grc_handbook": HandbookDetector,
    "ag_directive": DirectiveDetector,
    "html_headings": HtmlHeadingDetector,
}


def chunk_document(
    doc: PolicyDocument,
    pages: list[Page],
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[PolicyChunk]:
    """Group consecutive lines into chunks that never cross a section boundary."""
    detector = DETECTORS[doc.section_style]()
    flat = [(page, line) for page, lines in pages for line in lines]

    chunks: list[PolicyChunk] = []
    section: str | None = None
    buf: list[tuple[int | None, str]] = []

    def flush(keep_overlap: bool) -> None:
        nonlocal buf
        text = "\n".join(line for _, line in buf)
        if text:
            page_numbers = [p for p, _ in buf if p is not None]
            chunks.append(
                PolicyChunk(
                    chunk_id=f"{doc.doc_id}:{len(chunks):04d}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    source_url=doc.source_url,
                    effective_date=doc.effective_date,
                    section=section,
                    page_start=min(page_numbers) if page_numbers else None,
                    page_end=max(page_numbers) if page_numbers else None,
                    text=text,
                )
            )
        buf = buf[-OVERLAP_LINES:] if keep_overlap and OVERLAP_LINES else []

    for i, (page, line) in enumerate(flat):
        next_line = flat[i + 1][1] if i + 1 < len(flat) else ""
        new_section = detector(line, next_line)
        if new_section is not None and new_section != section:
            flush(keep_overlap=False)
            section = new_section
        if line.startswith(HTML_HEADING_PREFIX):
            line = line[len(HTML_HEADING_PREFIX) :]
        if buf and sum(len(t) + 1 for _, t in buf) + len(line) > max_chars:
            flush(keep_overlap=True)
        buf.append((page, line))
    flush(keep_overlap=False)

    logger.info("Chunked %s into %d chunk(s)", doc.doc_id, len(chunks))
    return chunks


def _cache_key(policy_dir: Path) -> str:
    """Changes whenever the manifest, any source document, or the extraction code changes."""
    h = hashlib.sha256()
    for path in [policy_dir / "manifest.json", *sorted(p for p in policy_dir.iterdir() if p.suffix in (".pdf", ".html"))]:
        stat = path.stat()
        h.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    for module in (Path(__file__), Path(__file__).with_name("extract.py")):
        h.update(module.read_bytes())
    return h.hexdigest()[:16]


def load_policy_chunks(policy_dir: Path = DEFAULT_POLICY_DIR, cache_dir: Path | None = DEFAULT_CACHE_DIR) -> list[PolicyChunk]:
    """Extract and chunk every document listed in the manifest, using a JSON cache when valid."""
    cache_file = cache_dir / f"chunks_{_cache_key(policy_dir)}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        try:
            chunks = [PolicyChunk.model_validate(c) for c in json.loads(cache_file.read_text(encoding="utf-8"))]
            logger.info("Loaded %d policy chunks from cache %s", len(chunks), cache_file.name)
            return chunks
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable chunk cache %s: %s", cache_file, exc)

    chunks: list[PolicyChunk] = []
    for doc in load_manifest(policy_dir):
        chunks.extend(chunk_document(doc, extract_pages(doc, policy_dir)))

    if cache_file:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for stale in cache_dir.glob("chunks_*.json"):
            stale.unlink(missing_ok=True)
        cache_file.write_text(json.dumps([c.model_dump(mode="json") for c in chunks]), encoding="utf-8")
    return chunks
