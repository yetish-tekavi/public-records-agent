"""Ingest an official policy document from its public source and record its provenance.

    python -m app.policies.ingest                       # OPRA statute, section N.J.S.A. 47:1A-5(a)
    python -m app.policies.ingest --offline             # use the local copy; record its origin

Steps: fetch the official landing page, confirm it lists the manifest's PDF, download the PDF,
compare it with the local copy (never silently overwritten), read the source version from the
document text, and extract the requested section verbatim from the downloaded file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin

from pydantic import BaseModel, Field

from app.data_loader import PROJECT_ROOT
from app.policies.chunker import chunk_document
from app.policies.extract import DEFAULT_POLICY_DIR, PolicyLoadError, extract_pages, load_manifest

logger = logging.getLogger(__name__)

INGESTION_DIR = DEFAULT_POLICY_DIR / "ingestion"
DOWNLOAD_DIR = PROJECT_ROOT / "outputs" / "ingest"
USER_AGENT = "Mozilla/5.0 (CMU capstone research prototype; public-records-agent)"


class IngestionRecord(BaseModel):
    doc_id: str
    title: str
    source_url: str
    landing_url: str | None
    section: str
    source_version: str | None = Field(description="as stated in the document text")
    retrieved_at: str
    mode: str  # "network" or "offline"
    local_file: str
    sha256: str
    size_bytes: int
    pages: int
    identical_to_local_copy: bool | None
    landing_page_lists_source: bool | None
    passage_pages: list[int]
    passage: str
    log: list[str]


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def fetch(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url.replace(" ", "%20"), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def pdf_links(html: str, base_url: str) -> list[str]:
    parser = _Links()
    parser.feed(html)
    return [urljoin(base_url, h) for h in parser.hrefs if h.lower().split("?")[0].endswith(".pdf")]


def section_passage(pdf_path: Path, doc, section: str, start_phrase: str | None) -> tuple[str, list[int], str | None, int]:
    """Return the section's verbatim extracted text (from start_phrase on), its pages, the version, page count."""
    import pypdf

    pages = extract_pages(doc.model_copy(update={"file": pdf_path.name}), pdf_path.parent)
    # Read the version from the raw first page: cleaning strips the running header that states it.
    first_page = " ".join((pypdf.PdfReader(pdf_path).pages[0].extract_text() or "").split())
    version = re.search(r"as of ([A-Z][a-z]+ \d{1,2}, \d{4})", first_page)
    # One chunk per section (no size limit) so the passage is contiguous and overlap-free.
    chunks = [c for c in chunk_document(doc, pages, max_chars=10**7) if (c.section or "").startswith(f"N.J.S.A. {section} ")]
    if not chunks:
        raise PolicyLoadError(f"section {section} not found in {pdf_path.name}")
    text = chunks[0].text
    if start_phrase:
        idx = text.find(start_phrase)
        if idx < 0:
            raise PolicyLoadError(f"phrase {start_phrase!r} not found in section {section}")
        text = text[idx:]
    page_list = list(range(chunks[0].page_start, chunks[0].page_end + 1))
    return text, page_list, version.group(1) if version else None, len(pages)


def ingest(doc_id: str = "opra_statute", section: str = "47:1A-5(a)", start_phrase: str | None = "Prior to allowing access",
           offline: bool = False, policy_dir: Path = DEFAULT_POLICY_DIR, out_dir: Path = INGESTION_DIR) -> IngestionRecord:
    log: list[str] = []

    def note(msg: str) -> None:
        log.append(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}")
        logger.info(msg)

    doc = next((d for d in load_manifest(policy_dir) if d.doc_id == doc_id), None)
    if doc is None:
        raise PolicyLoadError(f"{doc_id} is not in the manifest")
    local = policy_dir / doc.file
    note(f"Document: {doc.title}")
    note(f"Official source URL: {doc.source_url}")

    listed: bool | None = None
    identical: bool | None = None
    if offline:
        path = local
        note(f"Offline mode: using local copy {local.relative_to(PROJECT_ROOT)} (originally retrieved {doc.retrieved_date} from the source URL above)")
    else:
        if doc.landing_url:
            html = fetch(doc.landing_url).decode("utf-8", errors="replace")
            links = pdf_links(html, doc.landing_url)
            listed = unquote(doc.source_url) in {unquote(u) for u in links}
            note(f"Fetched landing page {doc.landing_url}: {len(links)} PDF link(s); manifest source listed: {listed}")
            for u in links:
                note(f"  landing page PDF: {unquote(u)}")
        data = fetch(doc.source_url)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = DOWNLOAD_DIR / doc.file
        path.write_bytes(data)
        note(f"Downloaded {len(data):,} bytes to {path.relative_to(PROJECT_ROOT)}")
        if local.exists():
            identical = hashlib.sha256(local.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
            note(f"Compared with local copy {local.relative_to(PROJECT_ROOT)}: {'identical' if identical else 'DIFFERENT'}")
            if not identical:
                note("The official file has changed. The local copy and manifest were NOT updated; review before re-indexing.")

    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    passage, passage_pages, version, page_count = section_passage(path, doc, section, start_phrase)
    note(f"SHA-256 {sha}; {page_count} pages; source version: {version or 'not stated'}")
    note(f"Extracted N.J.S.A. {section} passage ({len(passage)} chars, page(s) {passage_pages}) from the {'downloaded' if not offline else 'local'} file")

    record = IngestionRecord(
        doc_id=doc.doc_id, title=doc.title, source_url=doc.source_url, landing_url=doc.landing_url,
        section=f"N.J.S.A. {section}", source_version=f"as of {version}" if version else doc.effective_date,
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), mode="offline" if offline else "network",
        local_file=str(local.relative_to(PROJECT_ROOT)).replace("\\", "/"), sha256=sha, size_bytes=len(data), pages=page_count,
        identical_to_local_copy=identical, landing_page_lists_source=listed, passage_pages=passage_pages, passage=passage, log=log,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{doc_id}_{section.replace(':', '_').replace('(', '_').replace(')', '')}.json"
    out.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    note(f"Wrote ingestion record {out.relative_to(PROJECT_ROOT)}")
    record.log = log
    out.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest an official policy document and record provenance")
    parser.add_argument("--doc", default="opra_statute")
    parser.add_argument("--section", default="47:1A-5(a)")
    parser.add_argument("--offline", action="store_true", help="use the local copy instead of downloading")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        record = ingest(args.doc, args.section, offline=args.offline)
    except (OSError, PolicyLoadError) as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1
    print(json.dumps({k: v for k, v in record.model_dump().items() if k not in ("log", "passage")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
