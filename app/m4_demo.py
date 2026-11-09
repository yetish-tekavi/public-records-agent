"""Milestone 4 demonstration: four scenarios through the multi-agent LangGraph workflow over MCP.

    python -m app.m4_demo                 # all scenarios, tools via the MCP server, then the test suite
    python -m app.m4_demo --skip-tests
    python -m app.m4_demo --transport local

Writes what actually executed to docs/milestone_4/execution_traces/ (JSON + Markdown per scenario)
and docs/milestone_4/test_results.md. Exits non-zero if any scenario check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from app.agents.executors import LocalToolExecutor, McpToolExecutor
from app.console import prepare_stdout
from app.data_loader import PROJECT_ROOT, load_review_cases
from app.schemas import RedactionAction
from app.tools.service import ToolService
from app.workflows.graph import Orchestrator
from app.workflows.runner import resume, start
from app.workflows.state import ToTConfig, WorkflowConfig

DATA = PROJECT_ROOT / "data" / "milestone_4"
OUT = PROJECT_ROOT / "docs" / "milestone_4"
TRACES = OUT / "execution_traces"

SCENARIOS = [
    ("A", "OPRA-TEST-201", "Straightforward policy", "Standard workflow, no ToT invocation.", None),
    ("B", "OPRA-TEST-202", "Ambiguous policy", "ToT activates, evaluates and prunes candidates, then recommends or escalates.", None),
    ("C", "OPRA-TEST-203", "Missing evidence", "Bounded targeted retry, then human escalation; no invented citations.", None),
    ("D", "OPRA-TEST-204", "ToT failure protection", "Unsupported branch rejected; unresolved case escalated.", None),
    ("D-budget", "OPRA-TEST-204", "ToT failure protection (tool budget 0)",
     "Budget limit enforced: citations cannot be validated, no branch is selectable, case escalated.", ToTConfig(max_tool_calls=0)),
]


def check_scenario(key: str, s: dict[str, Any]) -> list[tuple[str, bool]]:
    recs = s.get("recommendations", [])
    tot = s.get("tot_results", [])
    no_invented = all(not re.search(r"N\.J\.S\.A|47:1A|§", r.rationale) for r in recs if not r.policy_references)
    common = [("paused for human review; nothing authorized", s["completion"].value == "AWAITING_HUMAN_REVIEW" and not s["disclosure_authorized"]),
              ("every recommendation requires human approval", all(r.requires_human_approval for r in recs))]
    if key == "A":
        return common + [("ToT not invoked", "tot" not in s["transitions"] and not tot),
                         ("REDACT citing N.J.S.A. 47:1A-5(a)", bool(recs) and recs[0].action is RedactionAction.REDACT
                          and "47:1A-5(a)" in recs[0].policy_references[0].citation)]
    if key == "B":
        return common + [("ToT invoked", "tot" in s["transitions"] and len(tot) == 1),
                         ("several candidate interpretations generated", bool(tot) and len([n for n in tot[0].nodes if n.depth == 1]) >= 2),
                         ("selected branch scored >= 70 with verified assumptions", bool(tot) and (tot[0].selected_score or 0) >= 70
                          and tot[0].outcome == "SUPPORTED_RECOMMENDATION")]
    if key == "C":
        return common + [("one targeted retry", s["retries"].missing_evidence == 1 and s["transitions"].count("targeted_retrieval") == 1),
                         ("HUMAN_REVIEW_REQUIRED with no citations", bool(recs) and recs[0].action is RedactionAction.HUMAN_REVIEW_REQUIRED
                          and not recs[0].policy_references),
                         ("no statute or section invented", no_invented)]
    if key == "D":
        return common + [("ToT invoked", bool(tot)),
                         ("unsupported branch rejected", bool(tot) and any(p.reason.startswith("rejected") for p in tot[0].pruned)),
                         ("escalated to human review", bool(recs) and recs[0].action is RedactionAction.HUMAN_REVIEW_REQUIRED)]
    return common + [("tool budget enforced", bool(tot) and tot[0].tool_calls_used == 0 and any("budget" in e for e in tot[0].budget_events)),
                     ("no branch selected; escalated", bool(tot) and tot[0].selected_node_id is None and tot[0].outcome == "ESCALATED")]


def selected_path(tot) -> list[str]:
    by_id = {n.node_id: n for n in tot.nodes}
    path, cur = [], by_id.get(tot.selected_node_id) if tot.selected_node_id else None
    while cur:
        path.append(cur.node_id)
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    return list(reversed(path))


def to_markdown(key, title, expected, case, s, checks, resumed) -> str:
    md = [f"# Scenario {key} — {title}", "",
          f"Request `{case.request.request_id}` → record `{case.record.record_id}`. Expected: {expected}", "",
          f"Synthetic record text: *{case.record.text}*", "",
          "Agents are deterministic (rule-based tool use over MCP); no LLM was called in this run.", "",
          "## Checks", "", *[f"- [{'PASS' if ok else 'FAIL'}] {name}" for name, ok in checks], "",
          "## State transitions", "", "`" + " → ".join(s["transitions"]) + "`", "",
          f"Route chosen by the Orchestrator: **{s.get('route')}**. Steps: {s['step_count']} / {s['config'].max_steps}.", "",
          "## Tool calls", "", "| # | Agent | Tool | Arguments | Status | Result | ms |", "|---|---|---|---|---|---|---|"]
    for i, t in enumerate(s["tool_calls"], 1):
        md.append(f"| {i} | {t.agent} | {t.tool} | `{json.dumps(t.arguments)[:160]}` | {t.status} | {t.summary[:160]} | {t.duration_ms} |")
    md += ["", "## Evidence and QA assessment", ""]
    for e in s.get("evidence", []):
        md.append(f"- {e.category.value}: **{e.status}**; queries {e.queries}; chunks {[c.chunk_id for c in e.passages]}")
    for a in s["qa"].assessments if s.get("qa") else []:
        md.append(f"- Assessment {a.finding_id}: **{a.decision.value}** — {'; '.join(a.reasons)}")
    for tot in s.get("tot_results", []):
        cfg = s["config"].tot
        md += ["", f"## Tree of Thought for {tot.finding_id}", "",
               f"Config: branching ≤ {cfg.max_branching}, depth ≤ {cfg.max_depth}, beam {cfg.beam_width}, min score {cfg.min_score}, "
               f"tool budget {cfg.max_tool_calls}, time {cfg.time_budget_s}s.", "",
               f"Outcome: **{tot.outcome}** (`{tot.action}`), selected `{tot.selected_node_id}` score {tot.selected_score}. "
               f"Depth reached {tot.depth_reached}; tool calls {tot.tool_calls_used}; targeted retrieval used: {tot.targeted_retrieval_used}.", "",
               f"Reason: {tot.reason}", "", f"Selected path: `{' → '.join(selected_path(tot)) or 'none'}`", "",
               "| Node | Parent | Depth | Stage | Action | Provision | Cites | Assumptions | Rel | Cit | Cons | Expl | Total | Status |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for n in tot.nodes:
            sc = n.score
            md.append(f"| {n.node_id} | {n.parent_id or '-'} | {n.depth} | {n.stage} | {n.action} | {n.provision_type} | {', '.join(n.cited_chunk_ids)} | "
                      f"{', '.join(a.status for a in n.assumptions) or '-'} | {sc.policy_relevance if sc else '-'} | {sc.citation_validity if sc else '-'} | "
                      f"{sc.consistency if sc else '-'} | {sc.explanation if sc else '-'} | {sc.total if sc else '-'} | {n.status} |")
        md += ["", "Pruning:", ""] + ([f"- `{p.node_id}` (depth {p.depth}): {p.reason}" for p in tot.pruned] or ["- none"])
        md += ["", "Budget / limit events:", ""] + ([f"- {e}" for e in tot.budget_events] or ["- none"])
        for n in tot.nodes:
            for a in n.assumptions:
                if a.evidence:
                    md.append(f"- `{n.node_id}` assumption *{a.text}* → {a.status} ({a.evidence})")
    md += ["", "## Recommendations (all require human approval)", ""]
    for r in s.get("recommendations", []):
        md += [f"- `{r.finding_id}` → **{r.action.value}** (detector confidence {r.detection_confidence})", f"  - Rationale: {r.rationale}"]
        md += [f"  - Citation: {ref.citation}, p. {ref.page} (`{ref.chunk_id}`) {ref.source_url}" for ref in r.policy_references]
        md += [f"  - Open question: {q}" for q in r.open_questions]
    if not s.get("recommendations"):
        md.append("- none")
    md += ["", "## Escalations", ""] + ([f"- {e}" for e in s.get("escalations", [])] or ["- none"])
    md += ["", "## Human review", "", f"Completion at pause: **{s['completion'].value}**; disclosure authorized: {s['disclosure_authorized']}."]
    if resumed:
        md += ["", "Approval-control demonstration: the paused workflow was resumed with a **scripted reviewer input** "
               f"(`{resumed['decision']}` by `{resumed['reviewer']}`, simulated for this demo, not a real reviewer).",
               f"Result: completion **{resumed['completion']}**, human review status {resumed['status']}, disclosure authorized {resumed['authorized']}."]
    md += ["", "## Audit log", "", "| Step | Node | Agent | Event |", "|---|---|---|---|"]
    md += [f"| {e.step} | {e.node} | {e.agent} | {e.event} |" for e in s["audit"]]
    return "\n".join(md) + "\n"


async def run_all(transport: str) -> list[dict[str, Any]]:
    cases = {c.request.request_id: c for c in load_review_cases(DATA)}
    results = []

    async def run_with(executor):
        for key, rid, title, expected, tot_cfg in SCENARIOS:
            config = WorkflowConfig(tot=tot_cfg) if tot_cfg else WorkflowConfig()
            orch = Orchestrator(executor, config)
            case = cases[rid]
            run = await start(orch, case.request, case.record, thread_id=f"demo-{key}")
            state, resumed = run.state, None
            checks = check_scenario(key, state)
            if key == "A":  # demonstrate the approval control on one paused workflow
                done = await resume(run, {"decision": "approve", "reviewer": "demo-script (simulated reviewer input)"})
                resumed = {"decision": "approve", "reviewer": "demo-script (simulated reviewer input)",
                           "completion": done.state["completion"].value, "status": done.state["human_review"].status,
                           "authorized": done.state["disclosure_authorized"]}
                checks.append(("disclosure authorized only after the human decision", done.state["disclosure_authorized"] is True))
            results.append({"key": key, "title": title, "expected": expected, "case": case, "state": state, "checks": checks,
                            "resumed": resumed, "transport": executor.transport})
            print(f"  Scenario {key}: {' / '.join(('PASS' if ok else 'FAIL') + ' ' + n for n, ok in checks)}")

    if transport == "mcp":
        async with McpToolExecutor(env={"PRA_DATA_DIR": str(DATA), "PRA_SEMANTIC": "1"}) as mcp:
            await run_with(mcp)
    else:
        await run_with(LocalToolExecutor(ToolService(data_dir=DATA)))
    return results


def dump_state(s: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in s.items():
        if hasattr(v, "model_dump"):
            out[k] = v.model_dump(mode="json")
        elif isinstance(v, list):
            out[k] = [x.model_dump(mode="json") if hasattr(x, "model_dump") else x for x in v]
        elif hasattr(v, "value"):
            out[k] = v.value
        else:
            out[k] = v
    return out


def run_tests() -> str:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", "--durations=5"], cwd=PROJECT_ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    m4 = subprocess.run([sys.executable, "-m", "pytest", "-v", "tests/test_m4_tot.py", "tests/test_m4_workflow.py", "tests/test_m4_scenarios.py"],
                        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    summary = lambda out: next((l.strip("= ") for l in reversed(out.strip().splitlines()) if re.search(r"\d+ (passed|failed)", l)), "no summary")
    passed = [l.split("::", 1)[1].split(" ")[0] for l in m4.stdout.splitlines() if " PASSED" in l and "::" in l]
    text = ["# Milestone 4 — Test Results", "",
            f"Generated by `python -m app.m4_demo` on {datetime.now(timezone.utc).isoformat(timespec='seconds')} (UTC), "
            f"Python {platform.python_version()}.", "",
            f"- Full suite (`python -m pytest -q`): **{summary(proc.stdout)}** (exit code {proc.returncode})",
            f"- Milestone 4 tests (`tests/test_m4_*.py`): **{summary(m4.stdout)}** (exit code {m4.returncode})", "",
            "## Milestone 4 tests", "", *[f"- {t}" for t in passed], "",
            "## Raw output (Milestone 4 tests)", "", "```text", m4.stdout.strip()[-6000:], "```", ""]
    (OUT / "test_results.md").write_text("\n".join(text), encoding="utf-8")
    return f"full suite: {summary(proc.stdout)}; milestone 4: {summary(m4.stdout)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Milestone 4 demonstration")
    parser.add_argument("--transport", choices=["mcp", "local"], default="mcp")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
    prepare_stdout()
    TRACES.mkdir(parents=True, exist_ok=True)
    print(f"Running Milestone 4 scenarios (transport: {args.transport})")
    results = asyncio.run(run_all(args.transport))

    index = ["# Milestone 4 — Execution Traces", "",
             f"Generated by `python -m app.m4_demo` on {datetime.now(timezone.utc).isoformat(timespec='seconds')} (UTC). "
             f"Tool transport: `{results[0]['transport']}`. Agents are deterministic; no LLM was called.", "",
             "| Scenario | Request | Path (route) | ToT | Final action | Checks |", "|---|---|---|---|---|---|"]
    for r in results:
        s = r["state"]
        tot = s.get("tot_results", [])
        tot_text = f"{tot[0].outcome}, score {tot[0].selected_score}, {len(tot[0].nodes)} nodes, {len(tot[0].pruned)} pruned" if tot else "not invoked"
        actions = ", ".join(x.action.value for x in s.get("recommendations", [])) or "none"
        ok = sum(c for _, c in r["checks"])
        name = f"scenario_{r['key'].replace('-', '_')}"
        (TRACES / f"{name}.json").write_text(json.dumps({"scenario": r["key"], "title": r["title"], "expected": r["expected"],
                                                         "transport": r["transport"], "checks": [{"check": n, "passed": c} for n, c in r["checks"]],
                                                         "approval_demo": r["resumed"], "state": dump_state(s)}, indent=2, ensure_ascii=False), encoding="utf-8")
        (TRACES / f"{name}.md").write_text(to_markdown(r["key"], r["title"], r["expected"], r["case"], s, r["checks"], r["resumed"]), encoding="utf-8")
        index.append(f"| [{r['key']}]({name}.md) — {r['title']} | {r['case'].request.request_id} | {s.get('route')} ({s['step_count']} steps) | "
                     f"{tot_text} | {actions} | {ok}/{len(r['checks'])} |")
    (TRACES / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    failed = [f"{r['key']}: {n}" for r in results for n, ok in r["checks"] if not ok]
    if not args.skip_tests:
        print("Running tests ...")
        print("  " + run_tests())
    print(f"{'All scenario checks passed.' if not failed else 'FAILED: ' + '; '.join(failed)} Traces: {TRACES.relative_to(PROJECT_ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
