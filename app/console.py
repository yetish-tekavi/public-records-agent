"""Console output helpers shared by the command-line demos."""

from __future__ import annotations

import sys

DISPLAY_FOLD = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "▪": "*", "•": "*", "§": "Sec."}
)


def prepare_stdout() -> bool:
    """Avoid crashes on legacy Windows code pages. Returns True if stdout is UTF-8."""
    utf8 = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") == "utf8"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    return utf8


def for_console(text: str, utf8: bool) -> str:
    """Display only: fold typographic characters to ASCII when the console is not UTF-8."""
    return text if utf8 else text.translate(DISPLAY_FOLD)
