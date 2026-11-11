"""Run the guardrail tests and write docs/milestone_6/safety_test_results.md from actual pytest outcomes.

    python -m app.evaluation.safety_report
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone

from app.data_loader import PROJECT_ROOT

OUT = PROJECT_ROOT / "docs" / "milestone_6"

GUARDRAILS: dict[str, list[str]] = {
    "Fabricated policy citations": [
        "tests/test_m4_tot.py::test_fabricated_citation_is_rejected", "tests/test_m4_tot.py::test_altered_excerpt_is_rejected",
        "tests/test_tools_service.py::test_propose_redactions_rejects_invented_chunk_ids", "tests/test_m4_workflow.py::test_invalid_citation_is_vetoed_by_qa",
        "tests/test_m6_evaluation.py::test_F1_validate_citations_marks_non_official_sources_invalid"],
    "Unsupported legal conclusions": [
        "tests/test_m4_workflow.py::test_missing_evidence_retries_once_then_escalates_without_citations",
        "tests/test_checkpoint_3_1.py::test_agent_without_relevant_policy_does_not_invent_one",
        "tests/test_m5_review.py::test_reviewer_facts_are_factual_input_only",
        "tests/test_tools_redaction.py::test_no_evidence_means_human_review_without_citations"],
    "Modification of original records": [
        "tests/test_m4_workflow.py::test_original_record_is_not_modified", "tests/test_m5_review.py::test_standard_recommendation_review_and_finalization",
        "tests/test_m5_review.py::test_source_change_after_analysis_blocks_finalization"],
    "Finalization without explicit human approval": [
        "tests/test_m5_review.py::test_unauthorized_finalization_is_blocked_and_recorded", "tests/test_m4_workflow.py::test_only_human_approval_authorizes_disclosure",
        "tests/test_m4_workflow.py::test_invalid_human_decision_is_refused", "tests/test_m5_app.py::test_approve_without_reviewer_name_is_refused"],
    "Finalization with unresolved review items": [
        "tests/test_m5_review.py::test_finalization_with_unresolved_findings_is_blocked",
        "tests/test_m5_review.py::test_unacknowledged_session_escalation_blocks_finalization",
        "tests/test_m5_app.py::test_missing_evidence_case_blocks_finalization_in_ui"],
    "Unsafe tool execution": [
        "tests/test_m6_evaluation.py::test_unknown_tools_are_refused", "tests/test_m6_evaluation.py::test_every_mcp_tool_is_read_only",
        "tests/test_tools_service.py::test_call_unknown_tool"],
    "Unbounded retries or branch expansion": [
        "tests/test_m4_tot.py::test_depth_limit_stops_search_and_escalates", "tests/test_m4_tot.py::test_beam_width_limits_retained_candidates",
        "tests/test_m4_tot.py::test_tool_budget_exhaustion_prevents_selection", "tests/test_m4_workflow.py::test_step_limit_forces_human_review",
        "tests/test_m4_workflow.py::test_persistent_record_failure_is_bounded_and_escalated",
        "tests/test_m5_review.py::test_reevaluation_limit_requires_manual_resolution", "tests/test_m6_evaluation.py::test_tot_limits_cannot_be_raised_beyond_design"],
    "Prompt injection through retrieved documents": [
        "tests/test_m6_evaluation.py::test_F1_policy_research_drops_non_official_passages",
        "tests/test_m6_evaluation.py::test_F1_tot_rejects_branch_built_on_non_official_text", "tests/test_m6_evaluation.py::test_evaluation_subset_end_to_end"],
    "Cross-contamination of evidence between ToT branches": [
        "tests/test_m4_tot.py::test_supported_branch_is_selected_after_verification_and_targeted_retrieval"],
    "Reuse of rejected branches as approved legal authority": [
        "tests/test_m4_tot.py::test_unsupported_factual_assumption_is_rejected_and_case_escalated",
        "tests/test_m4_tot.py::test_conflicting_exclusion_blocks_redaction", "tests/test_m6_evaluation.py::test_F2_low_detection_confidence_blocks_tot_redaction_until_confirmed"],
    "Tool failures during ReAct execution and during ToT": [
        "tests/test_m4_workflow.py::test_transient_record_analysis_failure_is_retried",
        "tests/test_m4_workflow.py::test_policy_tool_failure_becomes_missing_evidence_not_invented_policy",
        "tests/test_m4_workflow.py::test_tool_timeout_is_handled", "tests/test_agent.py::test_repeated_tool_failure_stops_and_escalates",
        "tests/test_m4_tot.py::test_tool_budget_exhaustion_prevents_selection"],
}


def main() -> int:
    node_ids = sorted({t for tests in GUARDRAILS.values() for t in tests})
    proc = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", *node_ids], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    per_case: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"^(tests/\S+?)\s+(PASSED|FAILED|SKIPPED|ERROR)", line)
        if m:  # parametrized cases ("name[param]") are aggregated under the base test id
            per_case.setdefault(m.group(1).split("[")[0], []).append(m.group(2))
    outcome = {t: ("PASSED" if all(s == "PASSED" for s in ss) else next(s for s in ss if s != "PASSED")) + (f" ({len(ss)} cases)" if len(ss) > 1 else "")
               for t, ss in per_case.items()}
    full = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider"], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    summary = next((l.strip("= ") for l in reversed(full.stdout.strip().splitlines()) if re.search(r"\d+ (passed|failed)", l)), "no summary")
    lines = ["# Milestone 6 — Safety and Guardrail Test Results", "",
             f"Generated by `python -m app.evaluation.safety_report` on {datetime.now(timezone.utc).isoformat(timespec='seconds')} (UTC).", "",
             f"**Full suite:** `{summary}` (exit code {full.returncode}).", "",
             "## Guardrails → tests (actual outcomes)", "", "| Guardrail | Test | Outcome |", "|---|---|---|"]
    for guardrail, tests in GUARDRAILS.items():
        for t in tests:
            lines.append(f"| {guardrail} | `{t.split('::')[1]}` ({t.split('::')[0].split('/')[-1]}) | {outcome.get(t, 'NOT RUN')} |")
    results_path = OUT / "evaluation_results.json"
    if results_path.exists():
        checks = json.loads(results_path.read_text(encoding="utf-8"))["metrics"]["safety"]
        lines += ["", "## Safety checks over all 44 evaluation runs", "",
                  *[f"- [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}" + (f" — {c['detail']}" if c["detail"] else "") for c in checks]]
    lines += ["", "## Guardrail test run output", "", "```text", proc.stdout.strip()[-5000:], "```", ""]
    (OUT / "safety_test_results.md").write_text("\n".join(lines), encoding="utf-8")
    failed = [t for t in node_ids if not outcome.get(t, "").startswith("PASSED")]
    print(f"guardrail tests: {len(node_ids) - len(failed)}/{len(node_ids)} passed; full suite: {summary}")
    for t in failed:
        print(f"  NOT PASSED: {t} ({outcome.get(t, 'NOT RUN')})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
