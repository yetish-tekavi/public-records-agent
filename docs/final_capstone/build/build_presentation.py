"""Build the final presentation (10 slides) with python-pptx.

    python docs/final_capstone/build/build_presentation.py

Writes docs/final_capstone/final_presentation.pptx. Every number on the slides comes from committed
results (docs/milestone_6/evaluation_results.json at commit 7a1a875, the Milestone 4 execution traces,
and the Checkpoint 3.1 demo). The screenshots are crops of real captures of the running app.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

sys.path.insert(0, str(Path(__file__).parent))
import diagram  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE.parent / "final_presentation.pptx"
CROPS = HERE.parent / "screenshots" / "crops"

HEAD, BODY = "Cambria", "Calibri"
NAVY, NAVY_DARK, TEAL, AMBER = "1F3A5F", "14273E", "2A9D8F", "F2B134"
INK, MUTED, LIGHT_BG, CARD, ICE, RED = "1B2631", "5F6B73", "F7F9FB", "FFFFFF", "CADCFC", "B23A48"
FOOTER = "Agentic Public Records Compliance Assistant · CMU Building Agentic AI Systems capstone"


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


# ------------------------------------------------------------------ helpers
def set_bg(slide, color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = rgb(color)


def bullet(p, level: int = 0) -> None:
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(Inches(0.22 + 0.22 * level))))
    pPr.set("indent", str(int(-Inches(0.2))))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    bu = pPr.makeelement(qn("a:buChar"), {"char": "•"})
    pPr.append(bu)


def text(slide, x, y, w, h, items, size=14, color=INK, bold=False, font=BODY, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, bullets=False, space_after=4, name=None, italic=False, margin=0.0):
    """items: list of paragraphs; each paragraph is a str or a list of (text, {bold, color, size, italic}) runs."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Inches(margin)
    for i, para in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        if bullets:
            bullet(p)
        runs = [(para, {})] if isinstance(para, str) else para
        for t, opt in runs:
            r = p.add_run()
            r.text = t
            r.font.name = opt.get("font", font)
            r.font.size = Pt(opt.get("size", size))
            r.font.bold = opt.get("bold", bold)
            r.font.italic = opt.get("italic", italic)
            r.font.color.rgb = rgb(opt.get("color", color))
    return tb


def box(slide, x, y, w, h, fill=CARD, line="D5DDE5", radius=0.08, name=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE, width=0.75):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = rgb(fill)
    if line:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(width)
    else:
        s.line.fill.background()
    if name:
        s.name = name
    s.text_frame.text = ""
    return s


def circle(slide, x, y, d, label, fill=TEAL, color="FFFFFF", size=13, name=None):
    s = box(slide, x, y, d, d, fill=fill, line=None, shape=MSO_SHAPE.OVAL, name=name)
    tf = s.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.name, r.font.size, r.font.bold = BODY, Pt(size), True
    r.font.color.rgb = rgb(color)
    return s


def title(slide, t, color=NAVY, size=28, y=0.35):
    ph = slide.shapes.title
    ph.left, ph.top, ph.width, ph.height = Inches(0.6), Inches(y), Inches(12.1), Inches(0.75)
    tf = ph.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    r = p.add_run()
    r.text = t
    r.font.name, r.font.size, r.font.bold = HEAD, Pt(size), True
    r.font.color.rgb = rgb(color)


def footer(slide, n, dark=False):
    c = "9AA5B1" if dark else MUTED
    text(slide, 0.6, 7.05, 9.5, 0.3, [FOOTER], size=9, color=c, name="footer")
    text(slide, 11.9, 7.05, 0.8, 0.3, [str(n)], size=9, color=c, align=PP_ALIGN.RIGHT, name="slide-number")


def notes(slide, t):
    slide.notes_slide.notes_text_frame.text = t


def picture(slide, path, x, y, w, name):
    pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y), width=Inches(w))
    pic.name = name
    pic.line.color.rgb = rgb("C9D2DC")
    pic.line.width = Pt(0.75)
    return pic


def load_results():
    d = json.loads((ROOT / "docs" / "milestone_6" / "evaluation_results.json").read_text(encoding="utf-8"))
    return d["metrics"]


# ------------------------------------------------------------------ slides
def s1_title(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, NAVY_DARK)
    text(s, 0.6, 1.15, 12, 0.4, ["CMU · Building Agentic AI Systems · Capstone (Research Assistant track)"], size=15, color="8FD3C7", bold=True)
    title(s, "Agentic Public Records Compliance Assistant", color="FFFFFF", size=44, y=1.65)
    s.shapes.title.height = Inches(1.5)
    text(s, 0.6, 3.25, 11.8, 0.9, ["Evidence-grounded redaction recommendations for New Jersey OPRA requests, with a human in charge"],
         size=21, color=ICE)
    chips = ["4 deterministic agents on LangGraph", "MCP tool server (4 tools)", "Policy RAG over 5 nj.gov documents",
             "Selective Tree of Thought", "Mandatory human authorization"]
    for i, c in enumerate(chips):
        x = 0.6 + i * 2.47
        b = box(s, x, 4.75, 2.32, 0.85, fill=NAVY, line=TEAL, width=1.25, name=f"chip-{i + 1}")
        tf = b.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_left = tf.margin_right = Inches(0.1)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = c
        r.font.name, r.font.size, r.font.color.rgb = BODY, Pt(13), rgb("FFFFFF")
    text(s, 0.6, 6.55, 12, 0.4, ["Final presentation · November 2026 · all records are synthetic; policy sources are public nj.gov documents"],
         size=12, color="9AA5B1")
    notes(s, "Introduce the project: a research-assistant agent that helps public-records staff prepare redactions under New Jersey's "
             "Open Public Records Act. Four agents coordinate on a LangGraph workflow, call tools through an MCP server, retrieve official "
             "policy text, use Tree of Thought only for ambiguous cases, and stop for a human to authorize every output. "
             "Say up front: the agents are deterministic and rule-based; no LLM is called in the workflow; all records are synthetic.")


def s2_problem(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, LIGHT_BG)
    title(s, "Redaction is manual, and every one needs a legal basis")
    text(s, 0.6, 1.45, 5.7, 0.7, ["Before releasing a record under New Jersey's Open Public Records Act (OPRA), a custodian must:"], size=16)
    text(s, 0.6, 2.2, 5.7, 2.2, [
        "find personal information: phone numbers, licence numbers, home addresses, IP addresses",
        "decide whether the statute or GRC guidance requires redaction, and cite it",
        "redact consistently and record why",
    ], size=15, bullets=True, space_after=8)
    b = box(s, 0.6, 4.45, 5.7, 1.65, fill="FFF4D6", line="E8C463", name="callout")
    text(s, 0.85, 4.6, 5.25, 1.4, [[("Manual, repetitive, and easy to get wrong. ", {"bold": True}),
                                    ("A missed number is a privacy breach; an unsupported redaction wrongly withholds public information.", {})]],
         size=15, anchor=MSO_ANCHOR.MIDDLE)
    cards = [("1", "Intended users", "Public-records custodians and review staff at New Jersey agencies"),
             ("2", "What the assistant does", "Finds candidate personal information, retrieves the governing OPRA/GRC text, recommends with citations, escalates when unsure"),
             ("3", "What it never does", "Decide disclosure. A named human approves every redaction, and nothing is published")]
    for i, (n, h, body) in enumerate(cards):
        y = 1.45 + i * 1.6
        box(s, 6.85, y, 5.85, 1.42, name=f"card-{n}")
        circle(s, 7.05, y + 0.22, 0.5, n, fill=NAVY if n != "3" else RED)
        text(s, 7.75, y + 0.15, 4.8, 0.4, [h], size=16, bold=True, color=NAVY)
        text(s, 7.75, y + 0.55, 4.8, 0.85, [body], size=13.5, color=INK)
    text(s, 0.6, 6.35, 12.1, 0.4, ["Prototype scope: synthetic text records · public nj.gov policy documents · no real records or personal data"],
         size=12, color=MUTED, italic=True)
    footer(s, 2)
    notes(s, "The users are records custodians. The hard part is not spotting a phone number; it is establishing that a specific provision "
             "requires redaction, applying it consistently, and keeping a record. Errors go both ways: missing personal data is a breach, "
             "redacting without a basis wrongly withholds public information. The assistant prepares evidence and recommendations; the "
             "custodian decides.")


def s3_why(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, LIGHT_BG)
    title(s, "Why a standalone LLM is not enough")
    text(s, 0.6, 1.3, 5.3, 0.4, ["A single LLM prompt"], size=16, bold=True, color=MUTED)
    text(s, 6.6, 1.3, 6.1, 0.4, ["This agentic system"], size=16, bold=True, color=NAVY)
    rows = [("Can invent a plausible statute or section", "Cites only retrieved chunk ids; validate_citations checks each exists and is verbatim"),
            ("Knows only its training data", "Retrieves current text from 5 official nj.gov documents"),
            ("Cannot act on the record", "Calls detection, retrieval and validation tools through MCP"),
            ("One pass, no recovery", "Bounded multi-step loops with retries, timeouts and a step limit"),
            ("May follow instructions hidden in documents", "Retrieved text is evidence only; non-official sources cannot be cited"),
            ("No audit trail; may imply a decision", "Hash-chained audit log; a named human authorizes every output")]
    for i, (a, b) in enumerate(rows):
        y = 1.8 + i * 0.8
        box(s, 0.6, y, 5.3, 0.66, fill="EEF1F4", line="D5DDE5", name=f"llm-{i + 1}")
        text(s, 0.8, y, 5.0, 0.66, [a], size=13.5, color="3D4852", anchor=MSO_ANCHOR.MIDDLE)
        arrow = box(s, 6.02, y + 0.2, 0.45, 0.26, fill=TEAL, line=None, shape=MSO_SHAPE.RIGHT_ARROW, name=f"arrow-{i + 1}")
        box(s, 6.6, y, 6.1, 0.66, fill=NAVY, line=None, name=f"agentic-{i + 1}")
        text(s, 6.8, y, 5.8, 0.66, [b], size=13.5, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    footer(s, 3)
    notes(s, "A compliance answer needs evidence, actions and accountability that one prompt cannot provide. The agentic design separates "
             "detection, retrieval, validation and review; constrains citations to retrieved, validated chunks; recovers from tool failures "
             "within limits; and records everything for audit. The final decision stays with a person.")


def s4_architecture(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, "FFFFFF")
    title(s, diagram.TITLE, size=24, y=0.22)
    text(s, 0.25, 0.8, 12.8, 0.3, [diagram.SUBTITLE], size=12, color=MUTED)
    s.shapes.title.left = Inches(0.25)
    diagram.draw(s)
    notes(s, "Walk the diagram left to right. The Orchestrator owns the workflow state and routing. Record Analysis detects candidate "
             "personal data, Policy Research retrieves official text, and Compliance & QA proposes redactions, validates every citation "
             "and classifies the evidence. CLEAR evidence takes the standard ReAct path; MISSING evidence gets one targeted retrieval and "
             "then escalates; only AMBIGUOUS or CONFLICTING evidence enters the Tree of Thought subgraph, which is part of the workflow, not "
             "a fifth agent. Every path stops at human review (a LangGraph interrupt). All tool calls go through the MCP server. "
             "The grey box lists future work that is not implemented.")


def s5_react_mcp(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, LIGHT_BG)
    title(s, "ReAct tool use through MCP: decide, call a tool, observe, repeat")
    steps = [("1", "Decide", "The agent's rules pick the next tool from the current state"),
             ("2", "Act", "Call the tool through MCP; inputs validated, call timed out and logged"),
             ("3", "Observe", "The structured result is written to the shared workflow state"),
             ("4", "Repeat or stop", "Bounded: ≤ 2 attempts, ≤ 2 queries per category, step limit 30")]
    for i, (n, h, b) in enumerate(steps):
        y = 1.45 + i * 1.03
        box(s, 0.6, y, 5.6, 0.88, name=f"step-{n}")
        circle(s, 0.78, y + 0.19, 0.5, n, fill=NAVY)
        text(s, 1.45, y + 0.08, 4.6, 0.35, [h], size=15, bold=True, color=NAVY)
        text(s, 1.45, y + 0.42, 4.6, 0.45, [b], size=12.5)
    box(s, 0.6, 5.65, 5.6, 1.1, fill="FFF4D6", line="E8C463", name="honesty")
    text(s, 0.8, 5.7, 5.25, 1.0, [[("Rule-based, not LLM-driven. ", {"bold": True}),
                                   ("A Claude planner exists (Milestone 3) but was tested only with a fake client. No LLM runs in the workflow.", {})]],
         size=12.5, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 6.7, 1.4, 6.0, 0.6, [[("MCP server", {"bold": True, "color": NAVY, "size": 17}),
                                  ("  official MCP Python SDK 2.3.0 · stdio · 4 read-only tools", {"color": MUTED, "size": 13})]])
    tools = [("detect_pii", "Presidio + spaCy: spans, category, confidence", "Record Analysis"),
             ("search_policy", "Hybrid retrieval with an evidence status", "Policy Research"),
             ("propose_redactions", "Deterministic rules; accepts chunk ids only", "Compliance & QA"),
             ("validate_citations", "Chunk exists, excerpt verbatim, source official", "QA and the ToT critic")]
    for i, (name, what, who) in enumerate(tools):
        y = 2.05 + i * 0.93
        box(s, 6.7, y, 6.0, 0.8, name=f"tool-{name}")
        chip = box(s, 6.85, y + 0.17, 2.05, 0.46, fill="6C5BA7", line=None, name=f"chip-{name}")
        text(s, 6.85, y + 0.17, 2.05, 0.46, [name], size=12.5, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        text(s, 9.05, y + 0.08, 3.55, 0.4, [what], size=12)
        text(s, 9.05, y + 0.44, 3.55, 0.3, [f"called by {who}"], size=11, color=MUTED, italic=True)
    box(s, 6.7, 5.85, 6.0, 0.9, fill="E6F4F1", line="9BD3C9", name="trace")
    text(s, 6.9, 5.88, 5.7, 0.85, [[("Recorded trace, Scenario A: ", {"bold": True}),
                                    ("detect_pii → search_policy → propose_redactions → validate_citations (5/5 valid). No ToT call.", {})]],
         size=12.5, anchor=MSO_ANCHOR.MIDDLE)
    footer(s, 5)
    notes(s, "Each agent runs a bounded ReAct-style loop: decide on a tool, call it, observe the structured result, then decide again or "
             "stop. The decisions are made by rules, which makes the behaviour reproducible and testable; it is not LLM tool selection. "
             "The four tools are exposed by an MCP server built with the official Python SDK over stdio; all are read-only. The Scenario A "
             "trace shows the four calls of the standard path.")


def s6_retrieval(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, LIGHT_BG)
    title(s, "Policy retrieval grounds every recommendation in official text")
    steps = ["5 official nj.gov documents: OPRA statute, GRC exemptions, GRC redaction guidance, Custodian's Handbook, AG Directive 2022-1",
             "496 section-aware chunks with title, section (e.g. 47:1A-5(a)), page and source URL",
             "Hybrid search: BM25 + ChromaDB (all-MiniLM-L6-v2), top 4 passages",
             "Evidence thresholds: SUFFICIENT or INSUFFICIENT",
             "Provenance guard: only nj.gov sources can be cited"]
    y = 1.4
    for i, st in enumerate(steps):
        h = 0.95 if i == 0 else 0.72
        box(s, 0.6, y, 4.7, h, fill=NAVY if i < 3 else "2C5E86", line=None, name=f"pipe-{i + 1}")
        text(s, 0.78, y, 4.4, h, [st], size=12, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
        y += h
        if i < len(steps) - 1:
            box(s, 2.8, y + 0.02, 0.3, 0.16, fill=TEAL, line=None, shape=MSO_SHAPE.DOWN_ARROW, name=f"pipe-arrow-{i + 1}")
            y += 0.2
    text(s, 5.75, 1.35, 6.9, 0.35, ["Executed query (Checkpoint 3.1, reproduced in Milestone 7)"], size=14, bold=True, color=NAVY)
    text(s, 5.75, 1.72, 6.9, 0.65, ["“What New Jersey law applies when a public record contains someone's personal telephone number?”"],
         size=14, italic=True)
    text(s, 5.75, 2.38, 6.9, 0.35, [[("Rank 2 · N.J.S.A. 47:1A-5(a) · p. 15 · chunk opra_statute:0052 · cosine 0.654", {"bold": True, "color": TEAL})]], size=13)
    picture(s, CROPS / "A_card.png", 5.75, 2.85, 6.95, "screenshot-scenario-A-card")
    text(s, 5.75, 4.98, 6.95, 0.3, ["Screenshot: the running reviewer app, Scenario A finding card with real citations and URLs"],
         size=10, color=MUTED, italic=True)
    box(s, 5.75, 5.45, 6.95, 1.0, fill="FBE9EC", line="E3A1AC", name="missing-callout")
    text(s, 5.95, 5.45, 6.6, 1.0, [[("No passage names the category → MISSING → “Human review required”. ", {"bold": True}),
                                    ("Never an invented citation (Scenario C: IP address).", {})]], size=13, anchor=MSO_ANCHOR.MIDDLE)
    footer(s, 6)
    notes(s, "The knowledge base is five official New Jersey documents chunked by section, so every passage carries its title, section, page "
             "and URL. Retrieval is hybrid: keyword BM25 plus semantic search in ChromaDB. For the phone-number question, the operative "
             "statute 47:1A-5(a) is returned at rank 2. The screenshot is from the real app. If no passage names the category, the evidence is "
             "MISSING and the item goes to human review; the system never writes a citation of its own.")


def s7_tot(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, LIGHT_BG)
    title(s, "Selective Tree of Thought: only when the evidence is ambiguous")
    routes = [("CLEAR", "standard path · 14 of 14 straightforward cases bypassed ToT", NAVY),
              ("AMBIGUOUS / CONFLICTING", "ToT subgraph · 7 of 7 activated", TEAL),
              ("MISSING", "one targeted retrieval, then escalate", "8A939B")]
    for i, (k, v, c) in enumerate(routes):
        y = 1.4 + i * 0.58
        box(s, 0.6, y, 2.3, 0.46, fill=c, line=None, name=f"route-{i + 1}")
        text(s, 0.6, y, 2.3, 0.46, [k], size=11, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        text(s, 3.0, y, 2.9, 0.46, [v], size=11.5, anchor=MSO_ANCHOR.MIDDLE)
    for i, c in enumerate(["branching ≤ 3", "depth ≤ 3", "beam 2", "select ≥ 70"]):
        x = 0.6 + i * 1.33
        box(s, x, 3.25, 1.23, 0.42, fill="FFFFFF", line=TEAL, name=f"param-{i + 1}")
        text(s, x, 3.25, 1.23, 0.42, [c], size=12, bold=True, color=TEAL, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.6, 3.85, 5.3, 0.3, ["Critic rubric (deterministic Compliance & QA)"], size=12.5, bold=True, color=NAVY)
    for i, (k, v) in enumerate([("Policy relevance", 30), ("Citation validity", 30), ("Consistency with facts", 25), ("Explanation & uncertainty", 15)]):
        y = 4.22 + i * 0.36
        text(s, 0.6, y, 2.2, 0.3, [k], size=11.5, anchor=MSO_ANCHOR.MIDDLE)
        box(s, 2.85, y + 0.04, 2.6 * v / 30, 0.24, fill=AMBER, line=None, radius=0.3, name=f"rubric-{i + 1}")
        text(s, 2.9 + 2.6 * v / 30, y, 0.5, 0.3, [str(v)], size=11.5, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.6, 5.7, 5.4, 1.2, [[("Rejected outright: ", {"bold": True}), ("fabricated or altered citation · unsupported factual assumption · REDACT with no evidence.", {})],
                                 [("REDACT selectable only if ", {"bold": True}), ("every assumption is SUPPORTED and an operative provision is cited.", {})]],
         size=11.5, space_after=3)
    text(s, 6.4, 1.35, 6.3, 0.35, ["Real run: Scenario B, a retired judge's home address"], size=14, bold=True, color=NAVY)
    rows = [("Branch", "Depth", "Action", "Rel", "Cit", "Cons", "Expl", "Total", "Status"),
            ("d1.1", "1", "REDACT", "20", "15", "13", "15", "63", "unverified"),
            ("d1.2", "1", "HUMAN REVIEW", "15", "30", "25", "15", "85", "evaluated"),
            ("d1.1>2", "2", "REDACT", "30", "30", "25", "15", "100", "selected")]
    widths = [0.75, 0.55, 1.25, 0.5, 0.5, 0.55, 0.55, 0.6, 1.05]
    tbl = s.shapes.add_table(len(rows), len(widths), Inches(6.4), Inches(1.8), Inches(sum(widths)), Inches(1.6))
    tbl.name = "tot-branch-table"
    t = tbl.table
    for j, w in enumerate(widths):
        t.columns[j].width = Inches(w)
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            cell = t.cell(i, j)
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(NAVY if i == 0 else ("E6F4F1" if row[0] == "d1.1>2" else "FFFFFF"))
            cell.margin_left = cell.margin_right = Inches(0.04)
            tf = cell.text_frame
            tf.paragraphs[0].alignment = PP_ALIGN.CENTER
            r = tf.paragraphs[0].add_run()
            r.text = v
            r.font.name, r.font.size = BODY, Pt(11)
            r.font.bold = i == 0 or row[0] == "d1.1>2"
            r.font.color.rgb = rgb("FFFFFF" if i == 0 else INK)
    text(s, 6.4, 3.55, 6.3, 1.9, [
        "Depth 2 checked “covered person” against the record (“retired Superior Court judge”) and spent the one targeted retrieval, adding the operative 47:1A-5(a) text.",
        "The reviewer then answered covered status = unconfirmed. ToT re-ran and the case escalated (d1.2, 85).",
        "Across the evaluation: 8 ToT runs, 25 branches, 2 rejected, 13 kept as uncertain, 0 limit violations.",
    ], size=12, bullets=True, space_after=6)
    box(s, 6.4, 5.65, 6.3, 1.15, fill="FFFFFF", line="D5DDE5", name="implemented-vs-proposed")
    text(s, 6.6, 5.68, 5.95, 1.1, [[("Implemented: ", {"bold": True, "color": TEAL}), ("a LangGraph subgraph run by the Orchestrator, not a fifth agent.", {})],
                                   [("Changed from proposal: ", {"bold": True, "color": RED}), ("CrewAI/LangChain critic agent → deterministic QA critic.", {})]],
         size=12, anchor=MSO_ANCHOR.MIDDLE, space_after=3)
    footer(s, 7)
    notes(s, "ToT runs only when the QA agent classifies the evidence as ambiguous or conflicting, for example a provision that protects "
             "home addresses only for certain classes of people. It is a beam search: up to three candidate interpretations, depth three, "
             "keep the best two, and a branch must score at least 70. Scores use the 30/30/25/15 rubric from Checkpoint 4.1, but scores alone "
             "never authorize a redaction: every factual assumption must be supported and an operative provision cited. The table is the real "
             "Scenario B run. Be clear that the critic is deterministic, not an LLM agent as originally proposed.")


def s8_results(prs, M):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, LIGHT_BG)
    title(s, "Evaluation: 22 scenarios, ReAct-only vs ReAct + ToT")
    keys = [("Detection precision", "detection", "precision"), ("Detection recall", "detection", "recall"),
            ("Acceptable action", "recommendations", "action_correct"), ("Preferred action", "recommendations", "preferred_action"),
            ("Grounded citations", "recommendations", "grounded_citation_accuracy"), ("Escalation recall", "workflow", "escalation_recall"),
            ("End-to-end success", "workflow", "end_to_end_success")]
    cd = CategoryChartData()
    cd.categories = [k[0] for k in keys]
    cd.add_series("A: ReAct-only", [round(M["A"][g][k]["value"], 3) for _, g, k in keys])
    cd.add_series("B: ReAct + ToT", [round(M["B"][g][k]["value"], 3) for _, g, k in keys])
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(1.3), Inches(7.8), Inches(4.75), cd)
    gf.name = "chart-a-vs-b"
    ch = gf.chart
    ch.has_title = False
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = True
    ch.legend.font.size, ch.legend.font.name = Pt(12), BODY
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale, va.major_unit = 0.0, 1.15, 0.25
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb("DDE3EA")
    va.tick_labels.font.size, va.tick_labels.number_format, va.tick_labels.number_format_is_linked = Pt(10), "0.00", False
    va.format.line.fill.background()
    ca = ch.category_axis
    ca.tick_labels.font.size, ca.tick_labels.font.name = Pt(10.5), BODY
    plot = ch.plots[0]
    plot.gap_width, plot.overlap = 60, -5
    plot.has_data_labels = True
    dl = plot.data_labels
    dl.number_format, dl.number_format_is_linked = "0.00#", False
    dl.position = XL_LABEL_POSITION.OUTSIDE_END
    dl.font.size, dl.font.name = Pt(9), BODY
    for ser, c in zip(plot.series, ("8A939B", TEAL)):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = rgb(c)
    stats = [("0", "unsupported redactions in either configuration", NAVY),
             ("+1", "preferred decision with ToT (S12, retired judge's address)", TEAL),
             ("21/21", "correct ToT activation or bypass decisions", NAVY),
             ("+13", "extra tool calls; +0.11 s per ToT case; $0 (no LLM)", AMBER)]
    for i, (big, small, c) in enumerate(stats):
        x, y = 8.6 + (i % 2) * 2.1, 1.35 + (i // 2) * 2.0
        box(s, x, y, 1.95, 1.8, name=f"stat-{i + 1}")
        text(s, x, y + 0.12, 1.95, 0.75, [big], size=34, bold=True, color=c if c != AMBER else "B7791F", align=PP_ALIGN.CENTER, font=HEAD)
        text(s, x + 0.1, y + 0.9, 1.75, 0.85, [small], size=11, align=PP_ALIGN.CENTER, color=INK)
    text(s, 8.6, 5.4, 4.1, 0.7, [f"Recall 17/19: the detector misses a spelled-out and a 7-digit phone number; QA flags both for the reviewer."],
         size=11, color=MUTED)
    box(s, 0.6, 6.15, 12.1, 0.55, fill=NAVY, line=None, name="demo-strip")
    text(s, 0.8, 6.15, 11.8, 0.55, [[("Live demo: ", {"bold": True, "color": AMBER}),
                                     ("A clear → approved and redacted · B ambiguous → ToT 100 → reviewer feedback → escalated · C missing evidence → finalization blocked", {"color": "FFFFFF"})]],
         size=12.5, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.6, 6.72, 12.1, 0.3, ["Source: docs/milestone_6/evaluation_results.json (commit 7a1a875), recomputed from 44 raw traces (33/33 match). Deterministic agents, "
                                   "simulated reviewer, self-authored scenarios: not a general accuracy claim."], size=9, color=MUTED, italic=True)
    text(s, 11.9, 7.05, 0.8, 0.3, ["8"], size=9, color=MUTED, align=PP_ALIGN.RIGHT, name="slide-number")
    notes(s, "Same 22 scenarios in both configurations; only enable_tot differs. Most metrics are identical: precision 1.00, recall 0.895, "
             "every action acceptable, all escalations correct, 22 of 22 end-to-end. ToT changed one decision: S12, where it found a fully "
             "verified redaction instead of abstaining, so the preferred-action rate rose from 0.88 to 0.94. It cost 13 extra tool calls and "
             "about a tenth of a second per ToT case. Importantly, before the evaluation-driven fixes ToT made two unsupported redactions; "
             "they are fixed and covered by regression tests. With 7 ToT cases this is evidence of no harm and one gain, not a general accuracy "
             "claim. Then run the live demo: scenarios A, B and C.")


def s9_guardrails(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, LIGHT_BG)
    title(s, "Guardrails keep a human in charge")
    rails = [("No invented law", "Only retrieved chunk ids can be cited; QA vetoes any invalid citation"),
             ("Provenance", "Non-nj.gov text is never cited; the injected document in S20 was blocked"),
             ("Bounded", "Retries, step limit 30, ToT depth/beam/tool budgets, ≤ 2 re-evaluations"),
             ("Originals untouched", "Derivative written separately; source hash re-checked at finalization"),
             ("Human authorization", "LangGraph interrupt; named reviewer; every finding resolved first"),
             ("Audit", "Append-only, hash-chained review events and full tool-call history")]
    for i, (h, b) in enumerate(rails):
        y = 1.4 + i * 0.75
        circle(s, 0.6, y + 0.1, 0.46, str(i + 1), fill=NAVY, size=12)
        text(s, 1.2, y, 5.4, 0.32, [h], size=14, bold=True, color=NAVY)
        text(s, 1.2, y + 0.32, 5.4, 0.4, [b], size=12)
    box(s, 0.6, 5.98, 6.0, 0.62, fill="E6F4F1", line="9BD3C9", name="safety-results")
    text(s, 0.8, 5.98, 5.7, 0.62, [[("16/16 ", {"bold": True}), ("safety checks over 44 runs · ", {}), ("40/40 ", {"bold": True}),
                                    ("guardrail tests · ", {}), ("314/314 ", {"bold": True}), ("tests", {})]], size=13, anchor=MSO_ANCHOR.MIDDLE)
    picture(s, CROPS / "C_blocked.png", 7.0, 1.4, 5.7, "screenshot-scenario-C-blocked")
    text(s, 7.0, 3.98, 5.7, 0.3, ["Real UI, Scenario C: finalization blocked for an escalated, unsupported finding"], size=10, color=MUTED, italic=True)
    box(s, 7.0, 4.45, 5.7, 2.15, fill="FFFFFF", line="D5DDE5", name="limitations")
    text(s, 7.2, 4.5, 5.35, 0.35, ["Limitations"], size=14, bold=True, color=RED)
    text(s, 7.2, 4.88, 5.35, 1.7, ["Rule-based agents; no LLM in the loop",
                                   "Detector misses spelled-out and 7-digit numbers",
                                   "22 self-authored scenarios; simulated reviewer",
                                   "Text only; single local reviewer, no access control"], size=12, bullets=True, space_after=3)
    footer(s, 9)
    notes(s, "These controls are enforced in code and covered by tests, not just described. The screenshot is the real app refusing to "
             "finalize Scenario C: the finding is escalated, so the reviewer must reject or modify it as their own decision. Be direct about "
             "limitations: the agents are rule-based, the detector has known gaps that QA flags for the reviewer, and the dataset is small "
             "and self-written.")


def s10_close(prs):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_bg(s, NAVY_DARK)
    title(s, "Lessons learned, conclusion and roadmap", color="FFFFFF")
    cols = [("Lessons learned", TEAL, ["Comparing A with B exposed 4 defects that unit tests missed",
                                       "A high rubric score is not legal correctness",
                                       "Provenance checks mattered more than extra branches",
                                       "Deterministic agents made every result reproducible"]),
            ("Conclusion", AMBER, ["Every course concept implemented, with tests and traces",
                                   "Selective ToT helps a narrow class of ambiguous cases at small cost",
                                   "The human reviewer remains the decision maker"]),
            ("Roadmap", "9AA5B1", ["LLM planner and critic behind the same guardrails",
                                                             "Better detection of spelled-out numbers",
                                                             "More policy sources with provenance checks",
                                                             "Synthetic audio and video",
                                                             "Multi-user review and a study with records staff"])]
    for i, (h, c, items) in enumerate(cols):
        x = 0.6 + i * 4.1
        box(s, x, 1.45, 3.9, 4.35, fill=NAVY, line=c, width=1.5, name=f"col-{i + 1}")
        text(s, x + 0.25, 1.6, 3.4, 0.5, [h], size=17, bold=True, color=c if c != "9AA5B1" else "D0D7DE")
        if h == "Roadmap":
            text(s, x + 0.25, 2.05, 3.4, 0.3, ["future work; none of it is implemented"], size=12, italic=True, color="D0D7DE")
        text(s, x + 0.25, 2.45, 3.45, 3.3, items, size=14, color="FFFFFF", bullets=True, space_after=10)
    text(s, 0.6, 6.15, 12.1, 0.55, ["Thank you. Questions?"], size=22, bold=True, color="FFFFFF", font=HEAD)
    footer(s, 10, dark=True)
    notes(s, "Close with three lessons. First, evaluation against a baseline found real defects, including two that only appeared with ToT. "
             "Second, a confident score is not correctness, which is why redaction needs verified facts, official citations and a human. "
             "Third, controlling what counts as evidence mattered more than generating more reasoning paths. The roadmap items are future "
             "work, not implemented.")


def main() -> int:
    M = load_results()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    prs.core_properties.title = "Agentic Public Records Compliance Assistant: Final Presentation"
    prs.core_properties.subject = "CMU Building Agentic AI Systems capstone"
    s1_title(prs)
    s2_problem(prs)
    s3_why(prs)
    s4_architecture(prs)
    s5_react_mcp(prs)
    s6_retrieval(prs)
    s7_tot(prs)
    s8_results(prs, M)
    s9_guardrails(prs)
    s10_close(prs)
    prs.save(OUT)
    print(f"wrote {OUT} ({len(prs.slides)} slides)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
