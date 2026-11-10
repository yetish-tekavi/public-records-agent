"""Milestone 5 demonstration: human review, re-evaluation, redaction, and finalization controls.

    python -m app.m5_demo                 # tools via the MCP server, then the test suite
    python -m app.m5_demo --skip-tests

Reviewer inputs in this script are SCRIPTED (simulated for the demonstration) and labelled as such.
Everything else (workflow runs, ToT results, tool calls, redaction, audit reports) is actual execution.
Outputs: docs/milestone_5/scenarios/, review_scenarios.md, audit_report_example.json, test_results.md.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.executors import LocalToolExecutor, McpToolExecutor
from app.console import prepare_stdout
from app.data_loader import PROJECT_ROOT, load_review_cases
from app.review.reevaluation import reevaluate
from app.review.report import build_audit_report
from app.review.session import ItemStatus, ReevaluationRequest, ReviewSession
from app.tools.service import ToolService
from app.workflows.graph import Orchestrator
from app.workflows.runner import resume, start

DATA = PROJECT_ROOT / "data" / "milestone_4"
OUT = PROJECT_ROOT / "docs" / "milestone_5"
SCEN = OUT / "scenarios"
REVIEWER = "Demo Reviewer (scripted input)"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def scenario_a(orch, cases) -> dict[str, Any]:
    case = cases["OPRA-TEST-201"]
    record_path = DATA / "synthetic_records" / "record_201.txt"
    before = file_hash(record_path)
    run = await start(orch, case.request, case.record, thread_id="m5-A")
    session = ReviewSession.from_workflow(run.state, record_path, actor="orchestrator")
    fid = session.items[0].finding.finding_id
    session.approve(fid, REVIEWER, note="OPRA names personal telephone numbers; evidence cites N.J.S.A. 47:1A-5(a).")
    result = session.finalize(REVIEWER, authorize=True)
    graph = await resume(run, {"decision": "approve", "reviewer": REVIEWER, "notes": "final redaction plan authorized"})
    checks = [
        ("ToT not invoked", session.items[0].reasoning_method == "STANDARD" and "tot" not in run.state["transitions"]),
        ("recommendation REDACT with N.J.S.A. 47:1A-5(a) evidence", session.items[0].recommendation.action.value == "REDACT"
         and "47:1A-5(a)" in session.items[0].recommendation.policy_references[0].citation),
        ("reviewer approval recorded", session.item_state(fid).status is ItemStatus.APPROVED),
        ("derivative finalized after explicit authorization", result.finalized),
        ("phone number masked in derivative", bool(result.redaction) and "[REDACTED: PHONE_NUMBER]" in result.redaction.text
         and "609-555-0142" not in result.redaction.text),
        ("original record unchanged", file_hash(record_path) == before),
        ("LangGraph workflow completed only after the human decision", graph.state["completion"].value == "APPROVED_BY_HUMAN"),
    ]
    return {"key": "A", "title": "Clear policy", "session": session, "result": result, "checks": checks,
            "graph_completion": graph.state["completion"].value}


async def scenario_b(orch, cases) -> dict[str, Any]:
    case = cases["OPRA-TEST-202"]
    run = await start(orch, case.request, case.record, thread_id="m5-B")
    session = ReviewSession.from_workflow(run.state, DATA / "synthetic_records" / "record_202.txt", actor="orchestrator")
    fid = session.items[0].finding.finding_id
    initial = session.item_state(fid)
    outcome = await reevaluate(orch, session, ReevaluationRequest(
        finding_id=fid, reviewer=REVIEWER,
        reason="The caller only identified himself as a retired judge; the record does not establish covered status.",
        factual_confirmations={"covered_person": "unconfirmed"}))
    updated = session.item_state(fid)
    checks = [
        ("actual bounded ToT result displayed (from the workflow)", initial.reasoning_method == "TOT" and initial.tot_result is not None
         and len(initial.tot_result.nodes) >= 2),
        ("reviewer feedback recorded as structured input", any(e.action == "REEVALUATION_REQUESTED" for e in session.events)),
        ("bounded ToT re-run with the feedback", outcome.get("tot_result") is not None),
        ("updated recommendation compared with the previous one", outcome["comparison"]["action_changed"]),
        ("updated recommendation returned for human approval", updated.status in (ItemStatus.PENDING, ItemStatus.ESCALATED)),
        ("no automatic finalization", not session.finalized),
    ]
    return {"key": "B", "title": "Ambiguous policy", "session": session, "result": None, "checks": checks, "outcome": outcome}


async def scenario_c(orch, cases) -> dict[str, Any]:
    case = cases["OPRA-TEST-203"]
    run = await start(orch, case.request, case.record, thread_id="m5-C")
    session = ReviewSession.from_workflow(run.state, DATA / "synthetic_records" / "record_203.txt", actor="orchestrator")
    fid = session.items[0].finding.finding_id
    outcome = await reevaluate(orch, session, ReevaluationRequest(
        finding_id=fid, reviewer=REVIEWER, reason="Check whether any provision covers IP addresses in complaint logs.",
        additional_query="IP address personal information complaint portal"))
    blocked = session.finalize(REVIEWER, authorize=True)
    rec = session.item_state(fid).recommendation
    checks = [
        ("flagged for human review (no policy basis)", session.items[0].evidence_status == "MISSING"
         and session.items[0].recommendation.action.value == "HUMAN_REVIEW_REQUIRED"),
        ("re-evaluation retrieved again and still found no basis", outcome["retrieval_performed"] and outcome["evidence_status"] == "MISSING"),
        ("no citation invented", rec.policy_references == [] and not re.search(r"N\.J\.S\.A|47:1A|§", rec.rationale)),
        ("unsupported finalization prevented", not blocked.finalized and any("not resolved" in r for r in blocked.reasons)),
        ("escalation and blocked attempt recorded", any(e.action == "FINALIZATION_BLOCKED" for e in session.events)
         and session.item_state(fid).status is ItemStatus.ESCALATED),
    ]
    return {"key": "C", "title": "Missing evidence", "session": session, "result": blocked, "checks": checks, "outcome": outcome}


def scenario_markdown(r: dict[str, Any]) -> str:
    s: ReviewSession = r["session"]
    item = s.items[0]
    st = s.item_state(item.finding.finding_id)
    md = [f"## Scenario {r['key']} — {r['title']}", "",
          f"Request `{s.request.request_id}`, record `{s.record_id}` (source sha256 `{s.source_text_sha256[:16]}…`). "
          f"Workflow route `{s.workflow['route']}`; transitions `{' → '.join(s.workflow['transitions'])}`.", "",
          f"Record: *{s.record_text}*", "",
          "| Check | Result |", "|---|---|", *[f"| {n} | {'PASS' if ok else 'FAIL'} |" for n, ok in r["checks"]], "",
          f"**Initial recommendation** ({item.reasoning_method}, evidence {item.evidence_status}): `{item.recommendation.action.value}` — "
          f"{item.recommendation.rationale}", ""]
    for ref in item.recommendation.policy_references:
        md.append(f"- {ref.citation}, p. {ref.page} (`{ref.chunk_id}`)")
    if item.tot_result:
        t = item.tot_result
        md += ["", f"**ToT (initial):** {t.outcome}, selected `{t.selected_node_id}` score {t.selected_score}; "
               f"additional retrieval {t.targeted_retrieval_used}.", "", "| Branch | Action | Provision | Status | Total |", "|---|---|---|---|---|"]
        md += [f"| {n.node_id} | {n.action} | {n.provision_type} | {n.status} | {n.score.total if n.score else '—'} |" for n in t.nodes]
    outcome = r.get("outcome")
    if outcome and outcome.get("comparison"):
        c = outcome["comparison"]
        md += ["", f"**Re-evaluation {outcome['attempt']}** (feedback: *{outcome['reviewer_feedback']['reason']}*; "
               f"factual input {outcome['factual_confirmations_applied']}; additional retrieval {outcome['retrieval_performed']}"
               + (f", query {outcome['retrieval_query']!r}" if outcome['retrieval_query'] else "") + ")", "",
               f"- Action: {c['action_before']} → {c['action_after']}; ToT score {c['tot_score_before']} → {c['tot_score_after']}; "
               f"citations added {c['citations_added']}, removed {c['citations_removed']}",
               f"- Updated recommendation: `{st.recommendation.action.value}` — {st.recommendation.rationale}"]
        if outcome.get("tot_result"):
            t = outcome["tot_result"]
            md += ["", "| Branch (re-evaluation) | Action | Status | Total |", "|---|---|---|---|"]
            md += [f"| {n['node_id']} | {n['action']} | {n['status']} | {n['score']['total'] if n['score'] else '—'} |" for n in t["nodes"]]
            md += [f"- Pruned `{p['node_id']}`: {p['reason']}" for p in t["pruned"]]
    md += ["", "**Review events (append-only)**", "", "| # | Time (UTC) | Actor | Action |", "|---|---|---|---|"]
    md += [f"| {e.seq} | {e.timestamp} | {e.actor} | {e.action} |" for e in s.events]
    res = r.get("result")
    if res is not None:
        if res.finalized:
            md += ["", f"**Finalized derivative** (sha256 `{res.derivative_sha256[:16]}…`, not published):", "", "```text", res.redaction.text, "```"]
        else:
            md += ["", "**Finalization blocked:**", *[f"- {x}" for x in res.reasons]]
    md += ["", f"Item status at end: **{st.status.value}**. Full audit report: [scenarios/scenario_{r['key']}_audit.json](scenarios/scenario_{r['key']}_audit.json)", ""]
    return "\n".join(md)


def run_tests() -> str:
    full = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    m5 = subprocess.run([sys.executable, "-m", "pytest", "-v", "tests/test_m5_redact.py", "tests/test_m5_review.py", "tests/test_m5_app.py"],
                        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    summary = lambda out: next((l.strip("= ") for l in reversed(out.strip().splitlines()) if re.search(r"\d+ (passed|failed)", l)), "no summary")
    names = [l.split("::", 1)[1].split(" ")[0] for l in m5.stdout.splitlines() if " PASSED" in l and "::" in l]
    failed = [l for l in m5.stdout.splitlines() if " FAILED" in l]
    (OUT / "test_results.md").write_text("\n".join([
        "# Milestone 5 — Test Results", "",
        f"Generated by `python -m app.m5_demo` on {datetime.now(timezone.utc).isoformat(timespec='seconds')} (UTC), Python {platform.python_version()}.", "",
        f"- Full suite (`python -m pytest -q`): **{summary(full.stdout)}** (exit code {full.returncode})",
        f"- Milestone 5 tests (`tests/test_m5_*.py`): **{summary(m5.stdout)}** (exit code {m5.returncode})", "",
        "## Milestone 5 tests passed", "", *[f"- {n}" for n in names], "",
        *(["## Failed", "", *[f"- {l}" for l in failed], ""] if failed else []),
        "## Raw output", "", "```text", m5.stdout.strip()[-6000:], "```", ""]), encoding="utf-8")
    return f"full suite: {summary(full.stdout)}; milestone 5: {summary(m5.stdout)}"


async def run_all(transport: str) -> list[dict[str, Any]]:
    cases = {c.request.request_id: c for c in load_review_cases(DATA)}

    async def go(executor):
        orch = Orchestrator(executor)
        return [await scenario_a(orch, cases), await scenario_b(orch, cases), await scenario_c(orch, cases)]

    if transport == "mcp":
        async with McpToolExecutor(env={"PRA_DATA_DIR": str(DATA), "PRA_SEMANTIC": "1"}) as mcp:
            return await go(mcp)
    return await go(LocalToolExecutor(ToolService(data_dir=DATA)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Milestone 5 review demonstration")
    parser.add_argument("--transport", choices=["mcp", "local"], default="mcp")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
    prepare_stdout()
    SCEN.mkdir(parents=True, exist_ok=True)
    print(f"Running Milestone 5 scenarios (transport: {args.transport}; reviewer inputs are scripted)")
    results = asyncio.run(run_all(args.transport))

    sections = []
    for r in results:
        report = build_audit_report(r["session"])
        (SCEN / f"scenario_{r['key']}_audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        r["session"].save(SCEN)
        if r["result"] is not None and r["result"].finalized:
            shutil.copy(r["result"].derivative_path, SCEN / f"scenario_{r['key']}_derivative.txt")
        if r["key"] == "A":
            (OUT / "audit_report_example.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        sections.append(scenario_markdown(r))
        print(f"  Scenario {r['key']}: " + " / ".join(f"{'PASS' if ok else 'FAIL'} {n}" for n, ok in r["checks"]))

    header = ["# Milestone 5 — Review Scenarios (actual execution)", "",
              f"Generated by `python -m app.m5_demo` on {datetime.now(timezone.utc).isoformat(timespec='seconds')} (UTC). "
              f"Agents and tools ran for real (transport `{args.transport}`); agents are deterministic and no LLM was called. "
              f"**Reviewer inputs were scripted** (actor `{REVIEWER}`) to demonstrate the controls; in use, a person enters them in the Streamlit app.", ""]
    (OUT / "review_scenarios.md").write_text("\n".join(header + sections), encoding="utf-8")

    failed = [f"{r['key']}: {n}" for r in results for n, ok in r["checks"] if not ok]
    if not args.skip_tests:
        print("Running tests ...")
        print("  " + run_tests())
    print("All scenario checks passed." if not failed else "FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
