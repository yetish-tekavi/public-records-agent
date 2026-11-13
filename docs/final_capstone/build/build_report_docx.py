"""Convert docs/final_capstone/final_report.md to a formatted .docx (and .pdf through Microsoft Word, if available).

    python docs/final_capstone/build/build_report_docx.py

Handles the Markdown used in the report: headings, paragraphs with **bold**, *italic*, `code` and links,
bullet and numbered lists, pipe tables, fenced code blocks, images with an italic caption line,
block quotes and horizontal rules.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "final_report.md"
DOCX = HERE.parent / "final_report.docx"
PDF = HERE.parent / "final_report.pdf"

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
MUTED = RGBColor(0x5F, 0x6B, 0x73)
BODY, HEAD, MONO = "Calibri", "Cambria", "Consolas"
INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def shade(cell, hex_fill: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def add_hyperlink(paragraph, text: str, url: str, size: float | None) -> None:
    rid = paragraph.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1F5FA8")
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.extend([color, u])
    if size:
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size * 2)))
        rpr.append(sz)
    r.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    r.append(t)
    link.append(r)
    paragraph._p.append(link)


def inline(paragraph, text: str, size: float | None = None, bold: bool = False, italic: bool = False, color=None) -> None:
    for part in INLINE.split(text):
        if not part:
            continue
        b, i, mono, link = bold, italic, False, None
        if part.startswith("**") and part.endswith("**"):
            part, b = part[2:-2], True
        elif part.startswith("`") and part.endswith("`"):
            part, mono = part[1:-1], True
        elif part.startswith("[") and "](" in part:
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part).groups()
            if url.startswith("http"):
                add_hyperlink(paragraph, label, url, size)
                continue
            part = label  # relative repository link: keep the visible text
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            part, i = part[1:-1], True
        run = paragraph.add_run(part)
        run.bold, run.italic = b, i
        if mono:
            run.font.name = MONO
            run.font.size = Pt((size or 10.5) - 1)
        elif size:
            run.font.size = Pt(size)
        if color is not None:
            run.font.color.rgb = color


def table(doc, rows: list[list[str]]) -> None:
    header, body = rows[0], rows[1:]
    ncols = len(header)
    size = 9.5 if ncols <= 3 else (8.5 if ncols <= 5 else 7.5)
    t = doc.add_table(rows=1 + len(body), cols=ncols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    # column widths proportional to content length (square root damps very long cells), within 6.8 in
    lengths = [max(len(re.sub(r"[*`]", "", r[c])) if c < len(r) else 0 for r in rows) for c in range(ncols)]
    weights = [max(4.0, min(n, 220)) ** 0.5 for n in lengths]
    widths = [Inches(6.8 * w / sum(weights)) for w in weights]
    for r, row in enumerate([header] + body):
        for c in range(ncols):
            cell = t.cell(r, c)
            cell.width = widths[c]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            inline(p, row[c] if c < len(row) else "", size=size, bold=(r == 0), color=RGBColor(0xFF, 0xFF, 0xFF) if r == 0 else None)
            if r == 0:
                shade(cell, "1F3A5F")
            elif r % 2 == 0:
                shade(cell, "F2F5F8")
    # repeat header row on page breaks
    trPr = t.rows[0]._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    trPr.append(th)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def build() -> None:
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin"):
        setattr(sec, side, Inches(0.85))
    sec.top_margin = sec.bottom_margin = Inches(0.8)
    st = doc.styles
    st["Normal"].font.name = BODY
    st["Normal"].font.size = Pt(10.5)
    st["Normal"].element.rPr.rFonts.set(qn("w:eastAsia"), BODY)
    st["Normal"].paragraph_format.space_after = Pt(5)
    for name, size, before in (("Title", 24, 0), ("Heading 1", 16, 16), ("Heading 2", 13, 12), ("Heading 3", 11.5, 10)):
        s = st[name]
        s.font.name, s.font.size, s.font.bold = HEAD, Pt(size), True
        s.font.color.rgb = NAVY
        s.element.rPr.rFonts.set(qn("w:eastAsia"), HEAD)
        s.paragraph_format.space_before, s.paragraph_format.space_after = Pt(before), Pt(6)
        s.paragraph_format.keep_with_next = True
    # footer page number
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for kind, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        run = fp.add_run()
        if kind:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), kind)
            run._r.append(fc)
        else:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = txt
            run._r.append(it)
        run.font.size = Pt(9)

    lines = SRC.read_text(encoding="utf-8").splitlines()
    i, first_h1 = 0, True
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.15)
            for j, b in enumerate(block):
                r = p.add_run(b)
                r.font.name, r.font.size = MONO, Pt(8.5)
                if j < len(block) - 1:
                    r.add_break()
            pPr = p._p.get_or_add_pPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), "F2F5F8")
            pPr.append(shd)
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|?\s*:?-{3,}", lines[i].strip().strip("|").strip()):
                    rows.append(split_row(lines[i]))
                i += 1
            table(doc, rows)
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            level, txt = len(m.group(1)), m.group(2)
            if level == 1 and first_h1:
                p = doc.add_paragraph(style="Title")
                inline(p, txt)
                first_h1 = False
            else:
                if level == 2 and (txt.startswith("Appendix A") or txt.startswith("1. ")):
                    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
                p = doc.add_heading(level=min(level - 1, 3) if level > 1 else 1)
                inline(p, txt)
            i += 1
            continue
        if s == "---":
            i += 1
            continue
        img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", s)
        if img:
            path = (SRC.parent / img.group(2)).resolve()
            doc.add_picture(str(path), width=Inches(6.6 if "architecture" in img.group(2) else 6.3))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            i += 1
            continue
        if s.startswith("*Figure"):
            caption = [s]
            i += 1
            while not caption[-1].endswith("*") and i < len(lines) and lines[i].strip():
                caption.append(lines[i].strip())
                i += 1
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            inline(p, " ".join(caption)[1:-1], size=9, italic=True, color=MUTED)
            continue
        if s.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.3)
            inline(p, " ".join(q for q in quote if q), italic=True, color=MUTED)
            continue
        bm = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        nm = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if bm or nm:
            mm = bm or nm
            indent = len(mm.group(1)) // 2
            if bm:
                p = doc.add_paragraph(style="List Bullet 2" if indent else "List Bullet")
                text = mm.group(2)
            else:  # keep the Markdown numbering (Word's list numbering would continue across sections)
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.3 + 0.25 * indent)
                p.paragraph_format.first_line_indent = Inches(-0.22)
                p.add_run(f"{mm.group(2)}. ")
                text = mm.group(3)
            p.paragraph_format.space_after = Pt(2)
            i += 1
            while i < len(lines) and lines[i].startswith("  " * (indent + 1)) and lines[i].strip() and not re.match(r"^\s*([-*]|\d+\.)\s", lines[i]):
                text += " " + lines[i].strip()
                i += 1
            inline(p, text)
            continue
        para = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^\s*([-*]\s|\d+\.\s|#|\||```|!\[|>)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        p = doc.add_paragraph()
        inline(p, " ".join(para))
    doc.core_properties.title = "Agentic Public Records Compliance Assistant: Final Capstone Report"
    doc.core_properties.subject = "CMU Building Agentic AI Systems capstone"
    doc.save(DOCX)
    print(f"wrote {DOCX}")


def export_pdf() -> None:
    script = (f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
              f"$d = $w.Documents.Open('{DOCX}', $false, $true); $d.ExportAsFixedFormat('{PDF}', 17); $d.Close($false); $w.Quit()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=300)
        print(f"wrote {PDF}")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"PDF export skipped: {exc}")


if __name__ == "__main__":
    build()
    if "--no-pdf" not in sys.argv:
        export_pdf()
