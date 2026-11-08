"""Checkpoint 3.1 demonstration: real NJ source -> semantic retrieval -> agent recommendation.

    python -m app.checkpoint_3_1               # network ingestion, agent over MCP, runs the tests
    python -m app.checkpoint_3_1 --offline     # use the genuine local copy if nj.gov is unreachable
    python -m app.checkpoint_3_1 --skip-tests

Every artifact in docs/checkpoint_3_1/ is written from what actually executed in this run,
including the generated report checkpoint_3_1_demo.md. Failed checks are reported, not hidden,
and the process exits non-zero if any check fails.
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
from pathlib import Path
from typing import Any

from app.agent_demo import format_report
from app.agents.executors import McpToolExecutor
from app.agents.models import AgentReport
from app.agents.planners import RuleBasedPlanner
from app.agents.react_agent import ReActAgent
from app.console import prepare_stdout
from app.data_loader import PROJECT_ROOT, load_review_cases
from app.policies.ingest import IngestionRecord, ingest
from app.policies.retriever import PolicyRetriever
from app.tools.service import ToolError

OUT = PROJECT_ROOT / "docs" / "checkpoint_3_1"
CHECKPOINT_DATA = PROJECT_ROOT / "data" / "checkpoint_3_1"
TA_QUERY = "What New Jersey law applies when a public record contains someone's personal telephone number?"
SECTION = "N.J.S.A. 47:1A-5(a)"
MISSING_CORPUS = ["ag_bwc_directive"]

logger = logging.getLogger("app.checkpoint")


def norm(text: str) -> str:
    return " ".join(text.split())


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, area: str, name: str, passed: bool, detail: str = "") -> None:
        self.items.append({"area": area, "check": name, "result": "PASS" if passed else "FAIL", "detail": detail})
        print(f"  [{'PASS' if passed else 'FAIL'}] {area}: {name}" + (f" ({detail})" if detail else ""))

    @property
    def all_passed(self) -> bool:
        return all(i["result"] == "PASS" for i in self.items)


class RecordingExecutor:
    """Passes calls through to the real executor and records the raw inputs and outputs."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.transport = inner.transport
        self.calls: list[dict[str, Any]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"tool": tool, "arguments": arguments, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.calls.append(entry)
        try:
            entry["result"] = await self.inner.call(tool, arguments)
            return entry["result"]
        except ToolError as exc:
            entry["error"] = exc.to_dict()
            raise


def write(name: str, content: str | dict | list) -> None:
    path = OUT / name
    text = content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


# --- Task 1 ----------------------------------------------------------------------


def task_ingest(offline: bool, checks: Checks) -> IngestionRecord:
    print("\n[Task 1] Ingest the official OPRA document")
    network_error = None
    try:
        record = ingest("opra_statute", "47:1A-5(a)", offline=offline)
    except OSError as exc:
        network_error = f"{type(exc).__name__}: {exc}"
        print(f"  network ingestion failed ({network_error}); falling back to the genuine local copy")
        record = ingest("opra_statute", "47:1A-5(a)", offline=True)
        record.log.insert(0, f"Network ingestion failed: {network_error}. Fell back to the local copy.")

    lines = ["# Source ingestion log (Checkpoint 3.1)", "", *record.log, "", "## Ingestion record"]
    lines += [f"{k}: {v}" for k, v in record.model_dump().items() if k not in ("log", "passage")]
    lines += ["", f"## Original retrieved passage ({record.section}, page(s) {record.passage_pages})", "", record.passage]
    write("01_source_ingestion_log.txt", "\n".join(lines))
    write("01_ingestion_record.json", record.model_dump())

    if record.mode == "network":
        checks.add("ingestion", "official landing page lists the source PDF", bool(record.landing_page_lists_source))
        checks.add("ingestion", "downloaded file matches the indexed local copy", bool(record.identical_to_local_copy),
                   f"sha256 {record.sha256[:16]}…")
    else:
        checks.add("ingestion", "network download", False, network_error or "run with --offline")
    checks.add("ingestion", f"{SECTION} passage extracted from the document", "personal telephone number" in norm(record.passage),
               f"{len(record.passage)} chars, pages {record.passage_pages}")
    checks.add("ingestion", "source version read from the document", bool(record.source_version), record.source_version or "")
    return record


# --- Task 2 ----------------------------------------------------------------------


def task_retrieval(record: IngestionRecord, checks: Checks) -> dict[str, Any]:
    print("\n[Task 2] Semantic retrieval")
    retriever = PolicyRetriever.from_directory()
    if retriever.semantic is None:
        checks.add("retrieval", "semantic index available", False, "embedding model or chromadb unavailable")
        return {}
    threshold = retriever.thresholds.semantic_min_similarity
    hits = retriever.semantic.search(TA_QUERY, k=4)
    results = [
        {
            "rank": i,
            "chunk_id": h.chunk.chunk_id,
            "title": h.chunk.title,
            "section": h.chunk.section,
            "pages": [h.chunk.page_start, h.chunk.page_end],
            "source_url": h.chunk.source_url,
            "effective_date": h.chunk.effective_date,
            "cosine_similarity": round(h.similarity, 4),
            "cosine_distance": round(1 - h.similarity, 4),
            "meets_evidence_threshold": h.similarity >= threshold,
            "passage": h.chunk.text,
        }
        for i, h in enumerate(hits, start=1)
    ]
    keyword = retriever.search(TA_QUERY, method="keyword", k=4)
    hybrid = retriever.search(TA_QUERY, method="hybrid", k=4)
    payload = {
        "query": TA_QUERY,
        "embedding_model": retriever.semantic.embedder.name,
        "vector_store": "ChromaDB (persistent, cosine)",
        "corpus_chunks": len(retriever.chunks),
        "semantic_evidence_threshold": threshold,
        "semantic_top4": results,
        "comparison": {
            "keyword_only": {"status": keyword.status.value, "chunk_ids": [p.chunk.chunk_id for p in keyword.passages],
                             "unknown_query_terms": retriever.keyword.unknown_terms(TA_QUERY)},
            "hybrid": {"status": hybrid.status.value, "chunk_ids": [p.chunk.chunk_id for p in hybrid.passages]},
        },
    }
    write("02_retrieval_results.json", payload)

    lines = [f"QUERY: {TA_QUERY}", f"Embedding model: {payload['embedding_model']}; vector store: {payload['vector_store']}; "
             f"{payload['corpus_chunks']} chunks; evidence threshold {threshold}", ""]
    for r in results:
        lines += [f"[{r['rank']}] {r['title']}", f"    Section:  {r['section']}", f"    Pages:    {r['pages']}",
                  f"    Source:   {r['source_url']}", f"    Score:    cosine similarity {r['cosine_similarity']} "
                  f"(distance {r['cosine_distance']}); meets threshold: {r['meets_evidence_threshold']}",
                  f"    Chunk:    {r['chunk_id']}", "    Passage:", *[f"      | {line}" for line in r["passage"].splitlines()], ""]
    lines += [f"Keyword-only retrieval for the same query: {keyword.status.value} {payload['comparison']['keyword_only']['chunk_ids']} "
              f"(unknown query terms: {payload['comparison']['keyword_only']['unknown_query_terms']})",
              f"Hybrid retrieval for the same query: {hybrid.status.value} {payload['comparison']['hybrid']['chunk_ids']}"]
    write("02_retrieval_results.txt", "\n".join(lines))

    target = [r for r in results if (r["section"] or "").startswith(SECTION) and "personal telephone number" in norm(r["passage"])]
    checks.add("retrieval", "semantic search executed against the ChromaDB index", len(results) == 4, f"{len(results)} results")
    checks.add("retrieval", f"{SECTION} telephone provision in the semantic top 4", bool(target),
               f"rank {target[0]['rank']}, similarity {target[0]['cosine_similarity']}" if target else "not found")
    if target:
        text = norm(target[0]["passage"])
        tail = text[text.find("Prior to allowing access"):] if "Prior to allowing access" in text else text
        checks.add("retrieval", "retrieved passage is verbatim text of the ingested official passage", tail in norm(record.passage))
    return payload


# --- Tasks 3 and 4 ---------------------------------------------------------------


async def run_agent_over_mcp(env: dict[str, str]) -> tuple[AgentReport, list[dict[str, Any]]]:
    [case] = load_review_cases(CHECKPOINT_DATA)
    async with McpToolExecutor(env=env) as mcp:
        recorder = RecordingExecutor(mcp)
        report = await ReActAgent(RuleBasedPlanner(), recorder).run(case.request, case.record)
    return report, recorder.calls


def task_agent(checks: Checks) -> tuple[AgentReport, list[dict[str, Any]]]:
    print("\n[Task 3] Agent run (tools called through the MCP server)")
    report, calls = asyncio.run(run_agent_over_mcp({"PRA_DATA_DIR": str(CHECKPOINT_DATA), "PRA_SEMANTIC": "1"}))
    write("03_agent_trace.txt", format_report(report))
    write("03_agent_report.json", report.model_dump(mode="json"))
    write("03_tool_calls_raw.json", calls)
    findings = {f.finding_id: f for f in report.findings}
    write("04_redaction_recommendation.json", [
        {"finding": findings[r.finding_id].model_dump(mode="json") if r.finding_id in findings else None, **r.model_dump(mode="json")}
        for r in report.recommendations
    ])

    searches = [c for c in calls if c["tool"] == "search_policy"]
    received = [p["chunk"] for c in searches for p in c.get("result", {}).get("passages", [])]
    target = [c for c in received if (c["section"] or "").startswith(SECTION) and "personal telephone number" in norm(c["text"])]
    rec = report.recommendations[0] if report.recommendations else None
    checks.add("agent", "identified the personal phone number",
               any(f.category.value == "PHONE_NUMBER" and f.text == "609-555-0142" for f in report.findings))
    checks.add("agent", "called search_policy through MCP", bool(searches) and report.transport == "mcp-stdio",
               f"{len(searches)} call(s), transport {report.transport}")
    checks.add("agent", f"received the actual {SECTION} passage from the tool", bool(target), target[0]["chunk_id"] if target else "")
    checks.add("agent", "evidence supports a proposed redaction", bool(rec) and rec.action.value == "REDACT",
               rec.action.value if rec else "no recommendation")
    first = rec.policy_references[0] if rec and rec.policy_references else None
    checks.add("agent", f"recommendation cites {SECTION} and the official source URL",
               bool(first) and SECTION in first.citation and first.source_url.startswith("https://www.nj.gov/grc/laws/act/"),
               f"{first.chunk_id}, p. {first.page}" if first else "")
    checks.add("agent", "exceptions / uncertainty listed for review", bool(rec) and any("Exception language" in q for q in rec.open_questions),
               f"{len(rec.open_questions)} open question(s)" if rec else "")
    checks.add("agent", "human authorization required before release",
               report.requires_human_approval and all(r.requires_human_approval for r in report.recommendations))
    return report, calls


def task_missing(checks: Checks) -> tuple[AgentReport, list[dict[str, Any]]]:
    print("\n[Task 4] Missing-evidence run (corpus restricted to the AG body-worn camera directive)")
    report, calls = asyncio.run(run_agent_over_mcp({
        "PRA_DATA_DIR": str(CHECKPOINT_DATA), "PRA_SEMANTIC": "1", "PRA_POLICY_DOC_IDS": ",".join(MISSING_CORPUS),
    }))
    write("05_missing_evidence_trace.txt", format_report(report))
    write("05_missing_evidence_report.json", report.model_dump(mode="json"))
    write("05_missing_evidence_tool_calls_raw.json", calls)

    searches = [c for c in calls if c["tool"] == "search_policy"]
    rec = report.recommendations[0] if report.recommendations else None
    produced = (rec.rationale + " " + " ".join(rec.open_questions) if rec else "") + report.final_summary + " ".join(report.escalations)
    checks.add("missing evidence", "every policy search returned INSUFFICIENT with no passages",
               bool(searches) and all(c.get("result", {}).get("status") == "INSUFFICIENT" and not c["result"]["passages"] for c in searches),
               f"{len(searches)} search(es)")
    checks.add("missing evidence", "recommendation is HUMAN_REVIEW_REQUIRED with no citations",
               bool(rec) and rec.action.value == "HUMAN_REVIEW_REQUIRED" and not rec.policy_references)
    checks.add("missing evidence", "insufficient policy evidence reported", bool(rec) and "no policy basis" in rec.rationale
               and any("human review required" in e for e in report.escalations))
    checks.add("missing evidence", "no statute, section, or legal explanation invented",
               not re.search(r"N\.J\.S\.A|47:1A|\bstatute\b|§", produced))
    return report, calls


# --- Task 5 ----------------------------------------------------------------------


def task_tests() -> dict[str, str]:
    print("\n[Task 5] Running the test suite")
    targeted = subprocess.run([sys.executable, "-m", "pytest", "-v", "tests/test_checkpoint_3_1.py"],
                              cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    full = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def summary(out: str) -> str:
        lines = [l for l in out.strip().splitlines() if re.search(r"\d+ (passed|failed|error)", l)]
        return lines[-1].strip("= ") if lines else "no summary line found"

    write("06_test_results.txt", "\n".join([
        "$ python -m pytest -v tests/test_checkpoint_3_1.py", targeted.stdout.strip(), "",
        "$ python -m pytest -q", full.stdout.strip(),
    ]))
    result = {"checkpoint_tests": summary(targeted.stdout), "full_suite": summary(full.stdout),
              "checkpoint_exit": str(targeted.returncode), "full_exit": str(full.returncode)}
    print(f"  checkpoint tests: {result['checkpoint_tests']}\n  full suite: {result['full_suite']}")
    return result


def trace_block(report: AgentReport) -> str:
    return format_report(report).split("\nRECOMMENDATIONS")[0]


def write_report(record: IngestionRecord, retrieval: dict[str, Any], agent: AgentReport, missing: AgentReport,
                 tests: dict[str, str] | None, checks: Checks, started: str) -> None:
    passed = sum(1 for c in checks.items if c["result"] == "PASS")
    rec = agent.recommendations[0] if agent.recommendations else None
    miss = missing.recommendations[0] if missing.recommendations else None
    md = [
        "# Checkpoint 3.1 — Semantic Retrieval Against a Real NJ Policy Source",
        "",
        f"Generated by `python -m app.checkpoint_3_1` at {started} (UTC). Every value below is read from this run's outputs.",
        f"Environment: Python {platform.python_version()} on {platform.system()} {platform.release()}; planner `{agent.planner}`; "
        f"tool transport `{agent.transport}`.",
        "",
        f"**Result: {passed}/{len(checks.items)} checks passed.**",
        "",
        "| Area | Check | Result | Detail |",
        "|---|---|---|---|",
        *[f"| {c['area']} | {c['check']} | {c['result']} | {c['detail']} |" for c in checks.items],
        "",
        "## 1. Source ingestion",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Document | {record.title} |",
        f"| Official source URL | {record.source_url} |",
        f"| Landing page | {record.landing_url} |",
        f"| Section | {record.section} |",
        f"| Source version (from the document) | {record.source_version} |",
        f"| Retrieved | {record.retrieved_at} ({record.mode}) |",
        f"| SHA-256 | `{record.sha256}` |",
        f"| Identical to indexed local copy | {record.identical_to_local_copy} |",
        f"| Passage pages | {record.passage_pages} |",
        "",
        "Original retrieved passage (verbatim text extracted from the downloaded PDF):",
        "",
        "```text", record.passage, "```",
        "",
        "Full log: [01_source_ingestion_log.txt](01_source_ingestion_log.txt)",
        "",
        "## 2. Semantic retrieval",
        "",
        f"Query: *{TA_QUERY}*",
        "",
        f"Embedding model `{retrieval.get('embedding_model')}`, {retrieval.get('vector_store')}, {retrieval.get('corpus_chunks')} chunks. "
        f"Evidence threshold: cosine similarity ≥ {retrieval.get('semantic_evidence_threshold')}.",
        "",
        "| Rank | Section | Pages | Similarity | Distance | Chunk |",
        "|---|---|---|---|---|---|",
        *[f"| {r['rank']} | {r['section']} | {r['pages'][0]}–{r['pages'][1]} | {r['cosine_similarity']} | {r['cosine_distance']} | `{r['chunk_id']}` |"
          for r in retrieval.get("semantic_top4", [])],
        "",
    ]
    for r in retrieval.get("semantic_top4", []):
        md += [f"<details><summary>[{r['rank']}] {r['title']} — {r['section']}</summary>", "",
               f"Source: {r['source_url']}", "", "```text", r["passage"], "```", "</details>", ""]
    comp = retrieval.get("comparison", {})
    if comp:
        md += [f"For comparison, keyword-only retrieval for the same query returned **{comp['keyword_only']['status']}** "
               f"{comp['keyword_only']['chunk_ids']} (query terms absent from the corpus: {comp['keyword_only']['unknown_query_terms']}); "
               f"hybrid returned {comp['hybrid']['status']} {comp['hybrid']['chunk_ids']}.", ""]
    md += [
        "## 3. Agent run (grounded recommendation)",
        "",
        "Synthetic record `TEST-101`: *During the interaction, the resident stated: My personal phone number is 609-555-0142.*",
        "",
        "```text", trace_block(agent), "```",
        "",
    ]
    if rec:
        md += [f"**Recommendation:** `{rec.action.value}` for `{rec.finding_id}` (detector confidence {rec.detection_confidence}); "
               f"requires human approval: {rec.requires_human_approval}.", "", f"Rationale: {rec.rationale}", "", "Citations:", ""]
        md += [f"- {ref.citation}, p. {ref.page} — {ref.source_url} (`{ref.chunk_id}`)" for ref in rec.policy_references]
        md += ["", "Open questions for the reviewer:", ""] + [f"- {q}" for q in rec.open_questions] + [""]
    md += ["Raw tool inputs/outputs: [03_tool_calls_raw.json](03_tool_calls_raw.json) · full report: [03_agent_report.json](03_agent_report.json)", "",
           "## 4. Missing-evidence run", "",
           f"Same record and agent; the MCP server's policy corpus restricted to {MISSING_CORPUS} (`PRA_POLICY_DOC_IDS`), "
           "a real document that does not address telephone numbers. Retrieval is real, not stubbed.", "",
           "```text", format_report(missing).split("\nESCALATIONS")[0], "```", ""]
    if miss:
        md += [f"Escalations: {missing.escalations}", f"Status: `{missing.status.value}`", ""]
    md += ["## 5. Test results", ""]
    if tests:
        md += [f"- `pytest -v tests/test_checkpoint_3_1.py`: {tests['checkpoint_tests']}", f"- `pytest -q` (full suite): {tests['full_suite']}",
               "", "Output: [06_test_results.txt](06_test_results.txt)", ""]
    else:
        md += ["Tests were skipped in this run (`--skip-tests`).", ""]
    md += [
        "## 6. Reproduction",
        "",
        "```bash",
        "pip install -r requirements.txt          # includes chromadb, presidio-analyzer, en_core_web_md, mcp",
        "python -m app.checkpoint_3_1             # downloads from nj.gov; add --offline to use the local copy",
        "python -m app.policies.ingest            # ingestion only",
        "python -m pytest tests/test_checkpoint_3_1.py -v",
        "```",
        "",
        "The first semantic run downloads all-MiniLM-L6-v2 (~80 MB) and builds the index in `outputs/policy_index/`.",
        "",
        "## 7. Notes and limitations",
        "",
        f"- The planner is `{agent.planner}` (deterministic). The Claude planner exists but was not used: it needs Anthropic credentials.",
        "- The missing-evidence scenario restricts the corpus to one real document rather than stubbing the retriever.",
        "- Exception detection is keyword-based: it quotes any exception language that follows the provision within the same "
        "subsection, which can include clauses that do not qualify the redaction rule. The reviewer decides.",
        "- Recommendations are evidence for a human reviewer, not legal determinations.",
    ]
    agent_queries = [s.arguments.get("query") for s in agent.steps if s.tool == "search_policy"]
    if agent_queries and agent_queries[0] != TA_QUERY:
        md.append(f"- The agent's own search used the planner's wording ({agent_queries[0]!r}); the TA's exact wording was run "
                  "in section 2. The planner avoids \"someone\" because that word never occurs in the corpus, which makes "
                  "keyword retrieval veto every match (see the keyword-only comparison in section 2).")
    write("checkpoint_3_1_demo.md", "\n".join(md))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Checkpoint 3.1 demonstration")
    parser.add_argument("--offline", action="store_true", help="use the genuine local copy instead of downloading")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
    prepare_stdout()
    OUT.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    checks = Checks()
    record = task_ingest(args.offline, checks)
    retrieval = task_retrieval(record, checks)
    agent, _ = task_agent(checks)
    missing, _ = task_missing(checks)
    tests = None if args.skip_tests else task_tests()
    if tests:
        checks.add("tests", "checkpoint tests pass", tests["checkpoint_exit"] == "0", tests["checkpoint_tests"])
        checks.add("tests", "full suite passes", tests["full_exit"] == "0", tests["full_suite"])
    write("checks.json", checks.items)
    write_report(record, retrieval, agent, missing, tests, checks, started)
    print(f"\n{sum(c['result'] == 'PASS' for c in checks.items)}/{len(checks.items)} checks passed. Artifacts: {OUT.relative_to(PROJECT_ROOT)}")
    return 0 if checks.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
