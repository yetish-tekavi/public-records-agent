"""Render Office files with Microsoft Office (COM) for visual QA and PDF export.

    python docs/final_capstone/build/render_office.py slides <deck.pptx> <out_dir>   # one PNG per slide
    python docs/final_capstone/build/render_office.py pdf <doc.docx> <out.pdf>        # Word → PDF
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def ps(script: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=600)


def slides(deck: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ps(f"$pp = New-Object -ComObject PowerPoint.Application; "
       f"$p = $pp.Presentations.Open('{deck}', $true, $false, $false); "
       f"$i = 1; foreach ($s in $p.Slides) {{ $s.Export((Join-Path '{out}' ('slide{{0:D2}}.png' -f $i)), 'PNG', 1600, 900); $i++ }}; "
       f"$p.Close(); $pp.Quit()")


def pdf(doc: Path, out: Path) -> None:
    ps(f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
       f"$d = $w.Documents.Open('{doc}', $false, $true); $d.ExportAsFixedFormat('{out}', 17); $d.Close($false); $w.Quit()")


if __name__ == "__main__":
    kind, src, dst = sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    {"slides": slides, "pdf": pdf}[kind](src, dst)
    print(f"rendered {src.name} -> {dst}")
