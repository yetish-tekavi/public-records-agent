"""Milestone 2 demo: search the NJ policy corpus and print verbatim evidence.

Usage:
    python -m app.policy_search "Is a driver license number confidential?"
    python -m app.policy_search "..." --method keyword -k 3
    python -m app.policy_search "..." --json
    python -m app.policy_search --demo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import textwrap

from app.policies.extract import PolicyLoadError
from app.policies.retriever import MAX_K, PolicyRetriever
from app.policies.semantic import SemanticUnavailableError
from app.schemas import PolicyRetrievalResult, RetrievalMethod

logger = logging.getLogger("app")

DISPLAY_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "▪": "*", "•": "*", "§": "Sec."})

DEMO_QUERIES = [
    "Is a person's telephone number or driver license number confidential?",
    "How should a custodian redact an electronic document?",
    "When can body worn camera recordings be released to the public?",
    "What is the speed limit on residential streets?",
    "California Public Records Act exemptions",
]


def format_result(result: PolicyRetrievalResult, excerpt_chars: int = 500) -> str:
    out = [f"QUERY:  {result.query}", f"METHOD: {result.method.value}", f"STATUS: {result.status.value}"]
    out.append(f"        {result.message}")
    for p in result.passages:
        c = p.chunk
        pages = (
            f"p. {c.page_start}" if c.page_start == c.page_end else f"pp. {c.page_start}-{c.page_end}"
        ) if c.page_start else "web page"
        scores = ", ".join(
            s
            for s in (
                f"keyword={p.keyword_score} (coverage {p.keyword_coverage})" if p.keyword_score is not None else "",
                f"semantic={p.semantic_similarity}" if p.semantic_similarity is not None else "",
            )
            if s
        )
        excerpt = c.text if len(c.text) <= excerpt_chars else c.text[:excerpt_chars].rstrip() + " […]"
        out += [
            "",
            f"  [{p.rank}] {c.title}",
            f"      Section:   {c.section or '(no section heading)'}",
            f"      Location:  {pages}   Effective: {c.effective_date or 'not stated'}",
            f"      Source:    {c.source_url}",
            f"      Chunk:     {c.chunk_id}   Scores: {scores}",
            "      Excerpt (verbatim):",
            textwrap.indent(excerpt, "        | "),
        ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search official NJ public-records policy documents")
    parser.add_argument("query", nargs="?", help="question or keywords to search for")
    parser.add_argument("--method", choices=[m.value for m in RetrievalMethod], default="hybrid")
    parser.add_argument("-k", type=int, default=3, help=f"maximum passages to return (1-{MAX_K})")
    parser.add_argument("--no-semantic", action="store_true", help="skip loading the embedding model")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--demo", action="store_true", help="run a fixed set of example queries")
    parser.add_argument("--verbose", action="store_true", help="show debug logging")
    args = parser.parse_args(argv)
    if not args.query and not args.demo:
        parser.error("provide a query or --demo")
    utf8_console = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") == "utf8"
    if hasattr(sys.stdout, "reconfigure"):
        # Windows consoles often use a legacy code page that cannot print some PDF glyphs.
        sys.stdout.reconfigure(errors="replace")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    method = "keyword" if args.no_semantic else args.method
    try:
        retriever = PolicyRetriever.from_directory(enable_semantic=method != "keyword")
        results = [retriever.search(q, method=method, k=args.k) for q in (DEMO_QUERIES if args.demo else [args.query])]
    except (PolicyLoadError, SemanticUnavailableError, ValueError) as exc:
        logger.error("Policy search failed: %s", exc)
        return 1

    if args.json:
        print(json.dumps([r.model_dump(mode="json") for r in results], indent=2, ensure_ascii=not utf8_console))
    else:
        text = f"\n{'=' * 78}\n".join(format_result(r) for r in results)
        # Display only: typographic characters are folded to ASCII on non-UTF-8 consoles.
        print(text if utf8_console else text.translate(DISPLAY_FOLD))
        print("\nRetrieved passages are evidence only. They are not legal advice or a disclosure decision.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
