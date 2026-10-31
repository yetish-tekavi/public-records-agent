"""Milestone 3 demo: run the single ReAct agent and print its tool-call trace and recommendations.

Usage:
    python -m app.agent_demo                          # all requests, rule-based planner, in-process tools
    python -m app.agent_demo --request OPRA-TEST-002 --transport mcp
    python -m app.agent_demo --planner claude         # needs Anthropic credentials
    python -m app.agent_demo --json

The trace shows each tool call, its arguments, a summary of the result, and the planner's short
decision summary. Model reasoning is not displayed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from dotenv import load_dotenv

from app.agents.executors import LocalToolExecutor, McpToolExecutor
from app.agents.models import AgentReport
from app.agents.planners import ClaudePlanner, RuleBasedPlanner
from app.agents.react_agent import DEFAULT_MAX_STEPS, ReActAgent
from app.console import for_console, prepare_stdout
from app.data_loader import DataLoadError, load_review_cases
from app.tools.service import ToolService

logger = logging.getLogger("app")


def format_report(report: AgentReport) -> str:
    out = [
        f"REQUEST {report.request_id} -> record {report.record_id}",
        f"planner={report.planner}  transport={report.transport}",
        "",
        "TOOL-CALL TRACE",
    ]
    for s in report.steps:
        if s.kind == "tool_call":
            args = ", ".join(f"{k}={json.dumps(v)}" for k, v in s.arguments.items())
            out.append(f"  {s.step}. {s.tool}({args})  [{s.status}]")
        else:
            out.append(f"  {s.step}. {s.kind.upper()}  [{s.status}]")
        out.append(f"       Decision: {s.decision_summary}")
        out.append(f"       Result:   {s.observation}")

    findings = {f.finding_id: f for f in report.findings}
    out += ["", "RECOMMENDATIONS (every item requires human approval)"]
    examined = any(s.tool == "detect_pii" and s.status == "ok" for s in report.steps)
    if not report.recommendations:
        out.append("  (none: no candidate sensitive information)" if examined else "  (none: the record was not examined)")
    for r in report.recommendations:
        f = findings.get(r.finding_id)
        label = f'{f.category.value} "{f.text}"' if f else r.finding_id
        out.append(f"  {r.finding_id}  {label}  ->  {r.action.value}  (detector confidence {r.detection_confidence})")
        out.append(f"       Rationale: {r.rationale}")
        for ref in r.policy_references:
            page = f", p. {ref.page}" if ref.page else ""
            out.append(f"       Citation:  {ref.citation}{page}  [{ref.chunk_id}]")
        for q in r.open_questions:
            out.append(f"       Open question: {q}")
    out += ["", "ESCALATIONS"]
    out += [f"  - {e}" for e in report.escalations] or ["  (none)"]
    out += ["", f"STATUS: {report.status.value}", f"SUMMARY: {report.final_summary}"]
    return "\n".join(out)


async def run(args: argparse.Namespace) -> list[AgentReport]:
    cases = load_review_cases()
    if args.request:
        cases = [c for c in cases if c.request.request_id == args.request]
        if not cases:
            raise DataLoadError(args.request, "no such request")

    def make_planner():
        return ClaudePlanner() if args.planner == "claude" else RuleBasedPlanner()

    reports = []
    if args.transport == "mcp":
        env = {"PRA_SEMANTIC": "0" if args.no_semantic else "1"}
        async with McpToolExecutor(env=env) as executor:
            for case in cases:
                agent = ReActAgent(make_planner(), executor, max_steps=args.max_steps)
                reports.append(await agent.run(case.request, case.record))
    else:
        executor = LocalToolExecutor(ToolService(enable_semantic=not args.no_semantic))
        for case in cases:
            agent = ReActAgent(make_planner(), executor, max_steps=args.max_steps)
            reports.append(await agent.run(case.request, case.record))
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the single ReAct agent on synthetic requests")
    parser.add_argument("--request", help="request id, e.g. OPRA-TEST-001 (default: all)")
    parser.add_argument("--planner", choices=["rule", "claude"], default="rule")
    parser.add_argument("--transport", choices=["local", "mcp"], default="local")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--no-semantic", action="store_true", help="keyword-only policy retrieval")
    parser.add_argument("--json", action="store_true", help="print full reports as JSON")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()  # ANTHROPIC_API_KEY may live in a local, git-ignored .env file
    utf8 = prepare_stdout()
    try:
        reports = asyncio.run(run(args))
    except DataLoadError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        if args.planner == "claude":
            logger.error("Claude planner could not start (%s). Set ANTHROPIC_API_KEY or use --planner rule.", exc)
            return 1
        raise

    if args.json:
        print(json.dumps([r.model_dump(mode="json") for r in reports], indent=2))
    else:
        print(for_console(f"\n{'=' * 78}\n".join(format_report(r) for r in reports), utf8))
    if args.planner == "claude" and any("authentication" in e for r in reports for e in r.escalations):
        logger.error("No Anthropic credentials found. Set ANTHROPIC_API_KEY (or add it to .env), or use --planner rule.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
