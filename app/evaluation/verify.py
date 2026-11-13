"""Recompute the reported Milestone 6 metrics from the raw per-run traces and compare.

    python -m app.evaluation.verify

Reads docs/milestone_6/execution_traces/final/*.json (one file per scenario and configuration),
recomputes every headline metric with the same definitions, and compares the result with
docs/milestone_6/evaluation_results.json. Exits non-zero on any mismatch.
"""

from __future__ import annotations

import json
import sys

from app.data_loader import PROJECT_ROOT
from app.evaluation import metrics as m
from app.evaluation.dataset import load_expectations

OUT = PROJECT_ROOT / "docs" / "milestone_6"
KEYS = [
    ("detection", "precision"), ("detection", "recall"),
    ("recommendations", "action_correct"), ("recommendations", "preferred_action"), ("recommendations", "grounded_citation_accuracy"),
    ("workflow", "workflow_completion"), ("workflow", "end_to_end_success"), ("workflow", "tool_failure_safe_handling"),
    ("workflow", "tool_failure_full_recovery"), ("workflow", "escalation_recall"), ("workflow", "false_escalation_rate"),
]


def main() -> int:
    scenarios = load_expectations()
    traces = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((OUT / "execution_traces" / "final").glob("*.json"))]
    reported = json.loads((OUT / "evaluation_results.json").read_text(encoding="utf-8"))["metrics"]
    official = m.official_chunk_ids()
    rows, mismatches = [], 0
    for config in ("A", "B"):
        runs = [t for t in traces if t["config"] == config]
        recomputed = {"detection": m.detection_metrics(scenarios, runs), "recommendations": m.recommendation_metrics(scenarios, runs, official),
                      "workflow": m.workflow_metrics(scenarios, runs)}
        for group, key in KEYS:
            a, b = recomputed[group][key], reported[config][group][key]
            same = (a["numerator"], a["denominator"]) == (b["numerator"], b["denominator"])
            mismatches += not same
            rows.append((config, f"{group}.{key}", f"{b['numerator']}/{b['denominator']}", f"{a['numerator']}/{a['denominator']}", same))
        unsup_same = recomputed["recommendations"]["unsupported_redactions"] == reported[config]["recommendations"]["unsupported_redactions"]
        mismatches += not unsup_same
        rows.append((config, "recommendations.unsupported_redactions", reported[config]["recommendations"]["unsupported_redactions"],
                     recomputed["recommendations"]["unsupported_redactions"], unsup_same))
    tot = m.tot_metrics(scenarios, [t for t in traces if t["config"] == "B"], [t for t in traces if t["config"] == "A"])
    for key in ("activation", "branches_generated", "rejected_unsupported_factual_assumption", "rejected_fabricated_or_altered_citation",
                "uncertain_branches_preserved", "escalated_runs", "tot_tool_calls", "invariant_violations"):
        same = tot[key] == reported["tot"][key]
        mismatches += not same
        rows.append(("B", f"tot.{key}", reported["tot"][key], tot[key], same))
    safety = m.safety_checks(scenarios, traces)
    same = [c["passed"] for c in safety] == [c["passed"] for c in reported["safety"]]
    mismatches += not same
    rows.append(("A+B", "safety checks passed", sum(c["passed"] for c in reported["safety"]), sum(c["passed"] for c in safety), same))
    print(f"traces read: {len(traces)}")
    for r in rows:
        print(f"  [{'OK' if r[4] else 'MISMATCH'}] {r[0]:3} {r[1]:45} reported {r[2]!s:24} recomputed {r[3]!s}")
    print(f"{len(rows) - mismatches}/{len(rows)} metrics match")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
