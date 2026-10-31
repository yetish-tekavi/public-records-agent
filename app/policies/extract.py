"""Load the policy manifest and extract cleaned text lines from PDFs and HTML pages."""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

from pydantic import ValidationError

from app.data_loader import PROJECT_ROOT
from app.schemas import PolicyDocument

logger = logging.getLogger(__name__)

DEFAULT_POLICY_DIR = PROJECT_ROOT / "data" / "policy_documents"
HTML_HEADING_PREFIX = "## "

# A page of extracted text: (1-based page number or None for HTML, cleaned lines).
Page = tuple[int | None, list[str]]


class PolicyLoadError(Exception):
    """Raised when the manifest or a policy document cannot be loaded."""


def load_manifest(policy_dir: Path = DEFAULT_POLICY_DIR) -> list[PolicyDocument]:
    path = policy_dir / "manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        docs = [PolicyDocument.model_validate(d) for d in raw["documents"]]
    except (OSError, KeyError, json.JSONDecodeError, ValidationError) as exc:
        raise PolicyLoadError(f"{path}: {exc}") from exc
    ids = [d.doc_id for d in docs]
    if len(ids) != len(set(ids)):
        raise PolicyLoadError(f"{path}: duplicate doc_id values")
    return docs


def clean_lines(text: str, strip_patterns: list[re.Pattern[str]]) -> list[str]:
    """Collapse whitespace, drop blank lines, and drop running headers/footers."""
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and not any(p.search(line) for p in strip_patterns):
            lines.append(line)
    return lines


class _MainTextParser(HTMLParser):
    """Collect text from ``div.mainText``; bold-only paragraphs become headings."""

    BLOCK_TAGS = {"p", "li", "br", "ul", "ol", "div", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._depth = 0  # nesting depth inside div.mainText; 0 means outside
        self._buf: list[str] = []
        self._strong: list[str] = []
        self._in_strong = False

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if self._depth == 0:
            if tag == "div" and "mainText" in classes:
                self._depth = 1
            return
        if tag == "div":
            self._depth += 1
        if tag in self.BLOCK_TAGS:
            self._flush()
        if tag in ("strong", "b"):
            self._in_strong = True

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        if tag in ("strong", "b"):
            self._in_strong = False
        if tag in self.BLOCK_TAGS:
            self._flush()
        if tag == "div":
            self._depth -= 1

    def handle_data(self, data):
        if self._depth:
            self._buf.append(data)
            if self._in_strong:
                self._strong.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        strong = re.sub(r"\s+", " ", "".join(self._strong)).strip()
        if text:
            self.lines.append(HTML_HEADING_PREFIX + text if text == strong else text)
        self._buf, self._strong = [], []


def extract_pages(doc: PolicyDocument, policy_dir: Path = DEFAULT_POLICY_DIR) -> list[Page]:
    """Return cleaned text lines per page for one manifest document."""
    path = policy_dir / doc.file
    strip = [re.compile(p) for p in doc.strip_line_patterns]
    if not path.exists():
        raise PolicyLoadError(f"{path}: file listed in manifest is missing")

    if path.suffix.lower() == ".pdf":
        import pypdf

        try:
            reader = pypdf.PdfReader(path)
            pages = [(i, clean_lines(p.extract_text() or "", strip)) for i, p in enumerate(reader.pages, 1)]
        except Exception as exc:  # pypdf raises several unrelated exception types
            raise PolicyLoadError(f"{path}: cannot extract PDF text ({exc})") from exc
    elif path.suffix.lower() in (".html", ".htm"):
        parser = _MainTextParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        parser.close()
        if not parser.lines:
            raise PolicyLoadError(f"{path}: no div.mainText content found")
        pages = [(None, clean_lines("\n".join(parser.lines), strip))]
    else:
        raise PolicyLoadError(f"{path}: unsupported file type")

    empty = [n for n, lines in pages if not lines]
    if empty:
        logger.warning("%s: no extractable text on page(s) %s", doc.file, empty)
    logger.debug("Extracted %d page(s) from %s", len(pages), doc.file)
    return pages
