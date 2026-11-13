"""Build the final architecture diagram files.

    python docs/final_capstone/build/build_architecture.py

Writes to docs/final_capstone/architecture/:
- final_architecture.drawio  editable diagrams.net file
- final_architecture.pptx    one native-shape slide (editable in PowerPoint)
- final_architecture.png     presentation export of that slide (needs Microsoft PowerPoint; skipped otherwise)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

sys.path.insert(0, str(Path(__file__).parent))
import diagram  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "architecture"


def add_title(slide, title: str, subtitle: str) -> None:
    for text, y, size, bold, color in ((title, 0.3, 24, True, diagram.INK), (subtitle, 0.75, 12, False, "5F6B73")):
        tb = slide.shapes.add_textbox(Inches(0.25), Inches(y), Inches(12.8), Inches(0.4))
        r = tb.text_frame.paragraphs[0].add_run()
        r.text = text
        r.font.name, r.font.size, r.font.bold = "Calibri", Pt(size), bold
        r.font.color.rgb = RGBColor.from_string(color)


def export_png(pptx: Path, png: Path) -> bool:
    script = (f"$pp = New-Object -ComObject PowerPoint.Application; "
              f"$p = $pp.Presentations.Open('{pptx}', $true, $false, $false); "
              f"$p.Slides(1).Export('{png}', 'PNG', 2667, 1500); $p.Close(); $pp.Quit()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"PNG export skipped: {exc}")
        return False
    return png.exists()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    diagram.to_drawio(OUT / "final_architecture.drawio")
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_title(slide, diagram.TITLE, diagram.SUBTITLE)
    diagram.draw(slide)
    pptx = OUT / "final_architecture.pptx"
    prs.save(pptx)
    ok = export_png(pptx.resolve(), (OUT / "final_architecture.png").resolve())
    for name in ("final_architecture.drawio", "final_architecture.pptx") + (("final_architecture.png",) if ok else ()):
        print(f"wrote {OUT / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
