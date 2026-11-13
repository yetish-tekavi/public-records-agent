"""Final architecture diagram, defined once and rendered two ways.

- to_drawio(): an editable diagrams.net (draw.io) file.
- draw(slide): native PowerPoint shapes and connectors (used for the presentation and the PNG export).

Coordinates are inches on a 13.333 x 7.5 in (16:9) canvas. The diagram shows only what is implemented;
the one dashed grey box is labelled as future architecture. ToT is a conditional subgraph inside the
workflow, not a fifth agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

# palette
NAVY, NAVY_DARK, AMBER, TEAL, TEAL_LIGHT = "1F3A5F", "14273E", "F2B134", "2A9D8F", "E6F4F1"
GREEN, GREEN_LIGHT, SLATE, SLATE_LIGHT, BLUE_LIGHT = "2D6A4F", "D8F3DC", "495057", "F1F3F5", "DCE6F0"
GREY, WHITE, INK = "8A939B", "FFFFFF", "1B2631"


@dataclass(frozen=True)
class Node:
    id: str
    text: str
    x: float
    y: float
    w: float
    h: float
    fill: str = NAVY
    color: str = WHITE
    line: str | None = None
    shape: str = "rect"          # rect | diamond | container
    size: float = 10.0
    bold_first: bool = True      # first line bold
    dashed: bool = False
    align: str = "center"        # container labels are top-left


NODES: list[Node] = [
    # containers first (drawn underneath)
    Node("wf", "LangGraph StateGraph workflow (app/workflows/graph.py)", 2.0, 1.1, 8.2, 4.2, SLATE_LIGHT, INK, "ADB5BD", "container", 10),
    Node("tot", "Conditional ToT subgraph\nonly for AMBIGUOUS / CONFLICTING\nbranching 3 · depth 3 · beam 2 · min 70",
         8.1, 2.3, 1.95, 1.95, TEAL_LIGHT, INK, TEAL, "container", 8, dashed=True),
    Node("mcp", "MCP tool layer: official MCP Python SDK, stdio server (app/mcp_server.py), 4 read-only tools",
         2.0, 5.5, 8.2, 0.85, "EEE8F6", INK, "9C89C9", "container", 9),
    Node("future", "Future architecture (not implemented)\nLLM planner and critic in the workflow\nAudio / video transcription and redaction\nMulti-user access control\nPersistent cross-process checkpoints",
         0.25, 5.5, 1.55, 1.75, "FAFAFA", "5F6B73", GREY, "rect", 8, dashed=True),
    # inputs and legend
    Node("in", "Synthetic inputs\nOPRA-style request + synthetic record\n(data/, 'Synthetic: yes' header)", 0.25, 1.45, 1.55, 1.65, "FFF4D6", INK, "D9A400", size=9),
    Node("legend", "Legend\n→ workflow control\n⇢ tool call / feedback\ndashed box: conditional\ngrey box: future", 0.25, 3.45, 1.55, 1.3, WHITE, "5F6B73", "CED4DA", size=8),
    # workflow nodes
    Node("orch", "Orchestrator Agent: plans, routes, merges evidence, bounded retries, step limit, escalation", 2.2, 1.45, 7.8, 0.55, NAVY_DARK, size=10),
    Node("ra", "Record Analysis Agent\ndetect_pii · ≤ 2 attempts", 2.2, 2.3, 1.75, 0.8, NAVY, size=9),
    Node("pr", "Policy Research Agent\nsearch_policy · ≤ 2 queries per category", 4.15, 2.3, 1.75, 0.8, NAVY, size=9),
    Node("qa", "Compliance & QA Agent\npropose_redactions · validate_citations · missed-PII sweep", 6.1, 2.3, 1.75, 0.8, NAVY, size=8.5),
    Node("dec", "Evidence\ndecision", 6.1, 3.35, 1.75, 0.95, AMBER, INK, shape="diamond", size=9),
    Node("tr", "MISSING → one targeted retrieval, then re-check", 4.15, 3.5, 1.75, 0.6, BLUE_LIGHT, INK, "8FA9C4", size=8.5, bold_first=False),
    Node("gen", "generate", 8.18, 3.0, 0.85, 0.5, TEAL, size=8.5),
    Node("eva", "evaluate\nQA critic", 9.12, 3.0, 0.85, 0.5, TEAL, size=8),
    Node("pru", "prune\nreject · beam", 8.18, 3.65, 0.85, 0.5, TEAL, size=8),
    Node("sel", "select or\nescalate", 9.12, 3.65, 0.85, 0.5, TEAL, size=8),
    Node("std", "CLEAR → standard ReAct path\n(rule tool + validated citations)", 6.1, 4.5, 1.75, 0.65, BLUE_LIGHT, INK, "8FA9C4", size=8.5, bold_first=False),
    Node("fin", "Finalize recommendations", 8.1, 4.5, 1.95, 0.65, NAVY, size=9),
    Node("state", "Shared typed WorkflowState: findings · evidence · QA report · ToT result · tool_calls · audit (append-only)",
         2.2, 4.5, 3.6, 0.65, WHITE, INK, "ADB5BD", size=8.5, bold_first=False),
    # MCP tools
    Node("t_search", "search_policy", 2.15, 5.85, 1.9, 0.38, "6C5BA7", size=9),
    Node("t_valid", "validate_citations", 4.15, 5.85, 1.9, 0.38, "6C5BA7", size=9),
    Node("t_pii", "detect_pii", 6.15, 5.85, 1.9, 0.38, "6C5BA7", size=9),
    Node("t_prop", "propose_redactions", 8.15, 5.85, 1.9, 0.38, "6C5BA7", size=9),
    # knowledge and engines
    Node("rag", "Policy RAG knowledge base\nHybrid BM25 + ChromaDB (all-MiniLM-L6-v2) · 5 official nj.gov documents · 496 chunks · provenance guard",
         2.0, 6.55, 4.05, 0.75, "E9EEF4", INK, "8FA9C4", size=8.5),
    Node("pii", "Presidio + spaCy\nPII detector", 6.15, 6.55, 1.9, 0.75, "E9EEF4", INK, "8FA9C4", size=8.5),
    Node("rules", "Deterministic redaction rules", 8.15, 6.55, 2.05, 0.75, "E9EEF4", INK, "8FA9C4", size=8.5, bold_first=False),
    # human review column
    Node("reeval", "Reviewer-requested re-evaluation\n≤ 2 per finding: Policy Research, QA and ToT run again", 10.45, 1.3, 2.6, 0.95, BLUE_LIGHT, INK, "8FA9C4", size=8.5),
    Node("hr", "Human review (mandatory)\nLangGraph interrupt() → Streamlit review session\napprove · reject · modify · re-evaluate · add missed redaction",
         10.45, 2.55, 2.6, 1.5, GREEN, size=9),
    Node("auth", "Final authorization\nnamed reviewer · every finding resolved", 10.45, 4.3, 2.6, 0.65, GREEN_LIGHT, INK, "74C69D", size=8.5),
    Node("red", "Deterministic span redaction\nderivative in outputs/ · original unchanged", 10.45, 5.2, 2.6, 0.65, GREEN_LIGHT, INK, "74C69D", size=8.5),
    Node("aud", "Audit report\nhash-chained review events · tool calls · ToT branches", 10.45, 6.1, 2.6, 0.75, GREEN_LIGHT, INK, "74C69D", size=8.5),
]


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    s: int          # connection point on source: 0 top, 1 left, 2 bottom, 3 right
    d: int
    dashed: bool = False
    label: str = ""
    lx: float = 0.0  # label position (inches)
    ly: float = 0.0
    pts: tuple[float, float, float, float] | None = None  # fixed start/end (inches) instead of connection points


EDGES: list[Edge] = [
    Edge("in", "orch", 3, 1),
    Edge("orch", "ra", 2, 0, pts=(3.075, 2.0, 3.075, 2.3)),
    Edge("ra", "pr", 3, 1),
    Edge("pr", "qa", 3, 1),
    Edge("qa", "dec", 2, 0),
    Edge("dec", "std", 2, 0),
    Edge("dec", "tot", 3, 1),
    Edge("dec", "tr", 1, 3),
    Edge("tr", "pr", 0, 2),
    Edge("gen", "eva", 3, 1),
    Edge("eva", "pru", 2, 0),
    Edge("pru", "sel", 3, 1),
    Edge("pru", "gen", 0, 2),  # next depth (at most 3)
    Edge("std", "fin", 3, 1),
    Edge("tot", "fin", 2, 0),
    Edge("fin", "hr", 3, 1),
    Edge("hr", "reeval", 0, 2, dashed=True),
    Edge("reeval", "orch", 1, 3, dashed=True, label="feedback", lx=10.0, ly=2.0),
    Edge("hr", "auth", 2, 0),
    Edge("auth", "red", 2, 0),
    Edge("red", "aud", 2, 0),
    Edge("wf", "mcp", 2, 0, dashed=True, label="every tool call (ToolCaller with timeout)", lx=3.1, ly=5.27),
    Edge("t_search", "rag", 2, 0, dashed=True),
    Edge("t_valid", "rag", 2, 0, dashed=True),
    Edge("t_pii", "pii", 2, 0, dashed=True),
    Edge("t_prop", "rules", 2, 0, dashed=True),
]

TITLE = "Final architecture (as implemented)"
SUBTITLE = "Four deterministic agents on one LangGraph workflow; ToT is a conditional subgraph, not a fifth agent; a human authorizes every output"


# ---------------------------------------------------------------- draw.io
def to_drawio(path) -> None:
    px = lambda v: round(v * 100)  # 100 px per inch
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    cells.append(f'<mxCell id="title" value="{escape(TITLE)}" style="text;html=0;fontSize=22;fontStyle=1;align=left;verticalAlign=middle;fontColor=#{INK};" vertex="1" parent="1"><mxGeometry x="25" y="20" width="900" height="40" as="geometry"/></mxCell>')
    cells.append(f'<mxCell id="subtitle" value="{escape(SUBTITLE)}" style="text;html=0;fontSize=12;align=left;verticalAlign=middle;fontColor=#5F6B73;" vertex="1" parent="1"><mxGeometry x="25" y="60" width="1200" height="30" as="geometry"/></mxCell>')
    for n in NODES:
        stroke = n.line or n.fill
        base = (f"whiteSpace=wrap;html=0;fillColor=#{n.fill};strokeColor=#{stroke};fontColor=#{n.color};"
                f"fontSize={round(n.size * 1.25)};fontFamily=Calibri;")
        if n.dashed:
            base += "dashed=1;"
        if n.shape == "diamond":
            style = "rhombus;" + base
        elif n.shape == "container":
            style = "rounded=1;arcSize=3;verticalAlign=top;align=left;spacingLeft=8;spacingTop=2;fontStyle=1;" + base
        else:
            style = "rounded=1;arcSize=8;" + base + ("fontStyle=1;" if n.bold_first and "\n" not in n.text else "")
        cells.append(f'<mxCell id="{n.id}" value="{escape(n.text, {chr(10): "&#xa;", chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1">'
                     f'<mxGeometry x="{px(n.x)}" y="{px(n.y)}" width="{px(n.w)}" height="{px(n.h)}" as="geometry"/></mxCell>')
    for i, e in enumerate(EDGES):
        style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;endArrow=block;endFill=1;strokeColor=#495057;fontSize=10;"
        if e.dashed:
            style += "dashed=1;"
        cells.append(f'<mxCell id="e{i}" value="{escape(e.label, {chr(10): "&#xa;"})}" style="{style}" edge="1" parent="1" source="{e.src}" target="{e.dst}">'
                     '<mxGeometry relative="1" as="geometry"/></mxCell>')
    xml = ('<mxfile host="app.diagrams.net"><diagram id="final-architecture" name="Final architecture">'
           '<mxGraphModel dx="1400" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
           'page="1" pageScale="1" pageWidth="1340" pageHeight="760" math="0" shadow="0"><root>'
           + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
    path.write_text(xml, encoding="utf-8")


# ---------------------------------------------------------------- PowerPoint
def draw(slide, font: str = "Calibri") -> None:
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_LINE
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Inches, Pt

    shapes: dict[str, object] = {}
    for n in NODES:
        kind = MSO_SHAPE.DIAMOND if n.shape == "diamond" else MSO_SHAPE.ROUNDED_RECTANGLE
        sh = slide.shapes.add_shape(kind, Inches(n.x), Inches(n.y), Inches(n.w), Inches(n.h))
        sh.name = f"arch-{n.id}"
        if kind == MSO_SHAPE.ROUNDED_RECTANGLE:
            sh.adjustments[0] = 0.03 if n.shape == "container" else 0.12
        sh.shadow.inherit = False
        sh.fill.solid()
        sh.fill.fore_color.rgb = RGBColor.from_string(n.fill)
        sh.line.color.rgb = RGBColor.from_string(n.line or n.fill)
        sh.line.width = Pt(1.25 if n.shape == "container" else 0.75)
        if n.dashed:
            sh.line.dash_style = MSO_LINE.DASH
        tf = sh.text_frame
        tf.word_wrap = True
        for side in ("left", "right"):
            setattr(tf, f"margin_{side}", Inches(0.06))
        tf.margin_top = tf.margin_bottom = Inches(0.03)
        tf.vertical_anchor = MSO_ANCHOR.TOP if n.shape == "container" else MSO_ANCHOR.MIDDLE
        for i, line in enumerate(n.text.split("\n")):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = PP_ALIGN.LEFT if n.shape == "container" or n.id in ("future", "legend") else PP_ALIGN.CENTER
            r = p.add_run()
            r.text = line
            r.font.name = font
            r.font.size = Pt(n.size if i == 0 else n.size - 0.5)
            r.font.bold = (i == 0 and (n.bold_first or n.shape == "container"))
            r.font.color.rgb = RGBColor.from_string(n.color)
        shapes[n.id] = sh

    for e in EDGES:
        if e.pts:
            c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, *(Inches(v) for v in e.pts))
        else:
            c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, Inches(1), Inches(1))
            c.begin_connect(shapes[e.src], e.s)
            c.end_connect(shapes[e.dst], e.d)
        c.line.color.rgb = RGBColor.from_string("495057")
        c.line.width = Pt(1.25)
        if e.dashed:
            c.line.dash_style = MSO_LINE.DASH
        ln = c.line._get_or_add_ln()
        tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(tail)
        c.name = f"edge-{e.src}-{e.dst}"
        if e.label:
            lines = e.label.split("\n")
            width = max(len(s) for s in lines) * 0.062 + 0.12
            tb = slide.shapes.add_textbox(Inches(e.lx), Inches(e.ly), Inches(width), Inches(0.17 * len(lines) + 0.04))
            tb.name = f"label-{e.src}-{e.dst}"
            tf = tb.text_frame
            tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
            for i, line in enumerate(lines):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                r = p.add_run()
                r.text = line
                r.font.name = font
                r.font.size = Pt(8)
                r.font.italic = True
                r.font.color.rgb = RGBColor.from_string("343A40")
