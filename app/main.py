"""Milestone 1 entry point: load and summarise the synthetic review cases.

Usage:
    python -m app.main [--data-dir PATH] [--verbose]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.data_loader import DEFAULT_DATA_DIR, DataLoadError, load_review_cases

logger = logging.getLogger("app")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Public Records Compliance Assistant (Milestone 1)")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--verbose", action="store_true", help="show debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cases = load_review_cases(args.data_dir)
    except DataLoadError as exc:
        logger.error("Could not load review cases: %s", exc)
        return 1

    print(f"Loaded {len(cases)} synthetic review case(s) from {args.data_dir}\n")
    for case in cases:
        categories = ", ".join(f.category.value for f in case.expected.findings) or "none"
        print(f"{case.request.request_id} -> {case.record.record_id} ({case.record.record_type.value})")
        print(f"  Title:              {case.record.title}")
        print(f"  Requires redaction: {'yes' if case.expected.requires_redaction else 'no'}")
        print(f"  Expected findings:  {len(case.expected.findings)} [{categories}]\n")
    print("No LLM is used in Milestone 1. All outputs require human review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
