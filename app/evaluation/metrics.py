"""Metric definitions and computation. Every metric reports its numerator and denominator.

Detection is scored independently of policy recommendations. A detected finding "matches" an
expected one when the category is the same and the character spans overlap.
"""

from __future__ import annotations

import statistics
from typing import Any

from app.policies.chunker import load_policy_chunks

OFFICIAL_PREFIXES = ("https://www.nj.gov/", "https://nj.gov/")


def ratio(num: int, den: int) -> dict[str, Any]:
    return {"value": round(num / den, 3) if den else None, "numerator": num, "denominator": den}


def match_findings(expected: list[dict], found: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    matched, unmatched_found = [], list(found)
    missed = []
    for e in expected:
        hit = next((f for f in unmatched_found if f["category"] == e["category"] and f["start"] < e["end"] and e["start"] < f["end"]), None)
        if hit:
            matched.append((e, hit))
            unmatched_found.remove(hit)
        else:
            missed.append(e)
    return matched, unmatched_found, missed


def detection_metrics(scenarios: list[dict], results: list[dict]) -> dict[str, Any]:
    by_id = {r["scenario"]: r for r in results}
    tp = fp = fn = exact = 0
    per_category: dict[str, dict[str, int]] = {}
    details = []
    for s in scenarios:
        r = by_id.get(s["id"])
        if not r or r.get("error"):
            continue
        matched, extra, missed = match_findings(s["outcomes"], r["findings"])
        tp, fp, fn = tp + len(matched), fp + len(extra), fn + len(missed)
        exact += sum(1 for e, f in matched if (e["start"], e["end"]) == (f["start"], f["end"]))
        for e, _ in matched:
            per_category.setdefault(e["category"], {"tp": 0, "fp": 0, "fn": 0})["tp"] += 1
        for f in extra:
            per_category.setdefault(f["category"], {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
        for e in missed:
            per_category.setdefault(e["category"], {"tp": 0, "fp": 0, "fn": 0})["fn"] += 1
        if extra or missed:
            details.append({"scenario": s["id"], "false_positives": [(f["category"], f["text"]) for f in extra],
                            "missed": [(e["category"], e["text"]) for e in missed]})
    return {"precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn), "exact_span_matches": ratio(exact, tp),
            "per_category": per_category, "errors": details}


def recommendation_metrics(scenarios: list[dict], results: list[dict], official_ids: set[str]) -> dict[str, Any]:
    by_id = {r["scenario"]: r for r in results}
    correct = preferred = unsupported = abstained = evaluated = 0
    grounded_num = grounded_den = 0
    nonofficial, uncited_redactions, fp_redactions = [], [], []
    rows = []
    for s in scenarios:
        r = by_id.get(s["id"])
        if not r or r.get("error"):
            continue
        matched, extra, _ = match_findings(s["outcomes"], r["findings"])
        for e, f in matched:
            evaluated += 1
            action = f["action"]
            ok = action in e["acceptable_actions"]
            correct += ok
            preferred += action == e["preferred_action"]
            if action == "REDACT" and "REDACT" not in e["acceptable_actions"]:
                unsupported += 1
            if action == "HUMAN_REVIEW_REQUIRED" and e["preferred_action"] == "REDACT":
                abstained += 1
            grounded = None
            if action == "REDACT":
                if not f["citations"]:
                    uncited_redactions.append((s["id"], f["finding_id"]))
                if e["citation_sections"]:
                    grounded_den += 1
                    first = f["citations"][0]["citation"] if f["citations"] else ""
                    all_official = all(c["source_url"].startswith(OFFICIAL_PREFIXES) and c["chunk_id"] in official_ids for c in f["citations"])
                    grounded = bool(f["citations"]) and any(sec in first for sec in e["citation_sections"]) and all_official
                    grounded_num += grounded
            for c in f["citations"]:
                if not c["source_url"].startswith(OFFICIAL_PREFIXES) or c["chunk_id"] not in official_ids:
                    nonofficial.append((s["id"], c["chunk_id"]))
            rows.append({"scenario": s["id"], "finding": e["text"], "category": e["category"], "expected": e["acceptable_actions"],
                         "preferred": e["preferred_action"], "actual": action, "evidence": f["evidence_decision"],
                         "correct": ok, "grounded": grounded, "first_citation": f["citations"][0]["citation"] if f["citations"] else None})
        for f in extra:
            if f["action"] == "REDACT":
                fp_redactions.append((s["id"], f["text"]))
                unsupported += 1
    return {"action_correct": ratio(correct, evaluated), "preferred_action": ratio(preferred, evaluated),
            "grounded_citation_accuracy": ratio(grounded_num, grounded_den), "unsupported_redactions": unsupported,
            "conservative_abstentions": abstained, "redactions_without_citation": uncited_redactions,
            "non_official_citations": nonofficial, "redactions_of_false_positives": fp_redactions, "rows": rows}


def review_outcome_ok(s: dict, r: dict) -> tuple[bool, list[str]]:
    exp, problems = s["expected_review"], []
    if r["review"]["finalized"] != exp["finalized"]:
        problems.append(f"finalized={r['review']['finalized']} (expected {exp['finalized']})")
    text = r.get("derivative_text")
    if text is not None:
        problems += [f"'{t}' not redacted" for t in exp.get("must_redact", []) if t in text]
        problems += [f"'{t}' wrongly removed" for t in exp.get("must_keep", []) if t not in text]
        if exp.get("unchanged") and text != s["record_text"]:
            problems.append("derivative differs from the original")
    if exp.get("no_automatic_reevaluation") and (r.get("tool_calls_during_review") or any(e.startswith("REEVALUATION") for e in r["review"]["events"])):
        problems.append("tools ran during review without a request")
    return not problems, problems


def escalated(r: dict) -> bool:
    return any(f["action"] == "HUMAN_REVIEW_REQUIRED" for f in r["findings"]) or bool(r["escalations"]) or r["record_analysis_status"] == "FAILED"


def workflow_metrics(scenarios: list[dict], results: list[dict]) -> dict[str, Any]:
    by_id = {r["scenario"]: r for r in results}
    completed = success = 0
    fault_total = fault_safe = fault_full = 0
    esc = {"tp": 0, "fn": 0, "fp": 0, "tn": 0, "not_scored": 0}
    failures, times = [], []
    for s in scenarios:
        r = by_id.get(s["id"])
        if not r or r.get("error"):
            failures.append({"scenario": s["id"], "problems": [r.get("error") if r else "not run"]})
            continue
        times.append(r["elapsed_s"])
        ok_complete = r["state_checks"]["paused_for_human"] and r["state_checks"]["within_step_limit"]
        completed += ok_complete
        matched, extra, missed = match_findings(s["outcomes"], r["findings"])
        unsupported = [f for e, f in matched if f["action"] == "REDACT" and "REDACT" not in e["acceptable_actions"]] + \
                      [f for f in extra if f["action"] == "REDACT"]
        review_ok, problems = review_outcome_ok(s, r)
        obs = escalated(r)
        exp = s["expected_escalation"]
        if exp is None:
            esc["not_scored"] += 1
            esc_ok = True
        else:
            key = ("tp" if obs else "fn") if exp else ("fp" if obs else "tn")
            esc[key] += 1
            esc_ok = obs == exp
            if not esc_ok:
                problems.append(f"escalation {obs} (expected {exp})")
        if unsupported:
            problems.append(f"unsupported redaction of {[f['text'] for f in unsupported]}")
        if not ok_complete:
            problems.append("workflow did not pause for human review within limits")
        scenario_ok = ok_complete and review_ok and esc_ok and not unsupported
        success += scenario_ok
        if not scenario_ok:
            failures.append({"scenario": s["id"], "problems": problems})
        if s.get("faults"):
            fault_total += 1
            acceptable = all(f["action"] in e["acceptable_actions"] for e, f in matched)
            fault_safe += ok_complete and not unsupported
            fault_full += ok_complete and not unsupported and acceptable and not missed and review_ok
    def pct(xs, q):
        return round(sorted(xs)[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 3) if xs else None
    return {
        "workflow_completion": ratio(completed, len(scenarios)), "end_to_end_success": ratio(success, len(scenarios)),
        "tool_failure_safe_handling": ratio(fault_safe, fault_total), "tool_failure_full_recovery": ratio(fault_full, fault_total),
        "escalation_recall": ratio(esc["tp"], esc["tp"] + esc["fn"]), "false_escalation_rate": ratio(esc["fp"], esc["fp"] + esc["tn"]),
        "escalation_confusion": esc,
        "latency_s": {"mean": round(statistics.mean(times), 3) if times else None, "median": round(statistics.median(times), 3) if times else None,
                      "p95": pct(times, 0.95), "max": max(times) if times else None, "n": len(times)},
        "failures": failures,
    }


def tot_metrics(scenarios: list[dict], results_b: list[dict], results_a: list[dict], beam_width: int = 2, max_depth: int = 3,
                max_branching: int = 3, min_score: int = 70) -> dict[str, Any]:
    a_by = {r["scenario"]: r for r in results_a}
    act = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "not_scored": 0}
    branches = depth1 = supported = rejected_fabricated = rejected_factual = rejected_other = pruned_beam = 0
    invariant_violations, uncertain_preserved, uncertain_lost = [], 0, []
    redact_selected = redact_selected_supported = escalated_runs = tie_events = runs = 0
    tot_tool_calls = 0
    extra_calls, extra_latency, per_scenario = [], [], []
    for s in scenarios:
        r = next((x for x in results_b if x["scenario"] == s["id"]), None)
        if not r or r.get("error"):
            continue
        tot_runs = [f["tot"] for f in r["findings"] if f["tot"]]
        observed = bool(tot_runs)
        exp = s["expected_tot"]
        if exp is None:
            act["not_scored"] += 1
        else:
            act[("tp" if observed else "fn") if exp else ("fp" if observed else "tn")] += 1
        a = a_by.get(s["id"])
        if observed and a and not a.get("error"):
            extra_calls.append(r["tool_calls"]["total"] - a["tool_calls"]["total"])
            extra_latency.append(round(r["elapsed_s"] - a["elapsed_s"], 3))
        for t in tot_runs:
            runs += 1
            nodes = t["nodes"]
            branches += len(nodes)
            d1 = [n for n in nodes if n["depth"] == 1]
            depth1 += len(d1)
            tot_tool_calls += t["tool_calls_used"]
            pruned_ids = {p["node_id"] for p in t["pruned"]}
            for p in t["pruned"]:
                reason = p["reason"]
                if "fabricated" in reason or "altered" in reason:
                    rejected_fabricated += 1
                elif "unsupported factual" in reason:
                    rejected_factual += 1
                elif reason.startswith("beam width"):
                    pruned_beam += 1
                else:
                    rejected_other += 1
            for n in nodes:
                if n["status"] not in ("REJECTED", "PRUNED") and n["evidence_ids"] and n["score"] and n["score"]["citation_validity"] >= 15:
                    supported += 1
                if n["status"] == "REJECTED" and n["node_id"] not in pruned_ids:
                    invariant_violations.append(f"{s['id']}: rejected node {n['node_id']} missing from prune log")
                if set(n["cited_chunk_ids"]) != set(n["evidence_ids"]):
                    invariant_violations.append(f"{s['id']}: node {n['node_id']} cites ids outside its own evidence")
                if any(a_["status"] == "UNVERIFIED" for a_ in n["assumptions"]) and n["status"] not in ("REJECTED",):
                    if n["status"] == "PRUNED":
                        uncertain_lost.append(f"{s['id']}:{n['node_id']}")
                    else:
                        uncertain_preserved += 1
            if len(d1) > max_branching:
                invariant_violations.append(f"{s['id']}: {len(d1)} depth-1 candidates > {max_branching}")
            for depth in range(2, max_depth + 1):
                if len([n for n in nodes if n["depth"] == depth]) > beam_width:
                    invariant_violations.append(f"{s['id']}: more than {beam_width} nodes at depth {depth}")
            if t["depth_reached"] > max_depth:
                invariant_violations.append(f"{s['id']}: depth {t['depth_reached']} > {max_depth}")
            sel = next((n for n in nodes if n["node_id"] == t["selected"]), None)
            if t["outcome"] == "ESCALATED":
                escalated_runs += 1
            if "material conflict" in t["reason"]:
                tie_events += 1
            if sel and sel["action"] == "REDACT":
                redact_selected += 1
                if all(a_["status"] == "SUPPORTED" for a_ in sel["assumptions"]) and sel["score"]["total"] >= min_score:
                    redact_selected_supported += 1
            per_scenario.append({"scenario": s["id"], "finding": next(f["text"] for f in r["findings"] if f["tot"] is t),
                                 "outcome": t["outcome"], "selected": t["selected"], "score": t["selected_score"], "nodes": len(nodes),
                                 "pruned": len(t["pruned"]), "depth": t["depth_reached"], "tool_calls": t["tool_calls_used"],
                                 "targeted_retrieval": t["targeted_retrieval"], "budget_events": t["budget_events"]})
    return {
        "activation": act, "activation_accuracy": ratio(act["tp"] + act["tn"], act["tp"] + act["tn"] + act["fp"] + act["fn"]),
        "activation_recall": ratio(act["tp"], act["tp"] + act["fn"]), "false_activation_rate": ratio(act["fp"], act["fp"] + act["tn"]),
        "tot_runs": runs, "branches_generated": branches, "depth1_candidates": depth1, "branches_supported": supported,
        "rejected_fabricated_or_altered_citation": rejected_fabricated, "rejected_unsupported_factual_assumption": rejected_factual,
        "rejected_other": rejected_other, "pruned_by_beam": pruned_beam,
        "invariant_violations": invariant_violations, "uncertain_branches_preserved": uncertain_preserved, "uncertain_branches_lost": uncertain_lost,
        "redact_selections": redact_selected, "redact_selections_fully_supported": ratio(redact_selected_supported, redact_selected),
        "escalated_runs": escalated_runs, "tie_conflict_events": tie_events, "tot_tool_calls": tot_tool_calls,
        "extra_tool_calls_vs_baseline": {"total": sum(extra_calls), "per_scenario": extra_calls},
        "extra_latency_s_vs_baseline": {"mean": round(statistics.mean(extra_latency), 3) if extra_latency else None, "per_scenario": extra_latency},
        "llm_calls": 0, "tokens": None, "estimated_api_cost_usd": 0.0, "per_run": per_scenario,
    }


def official_chunk_ids() -> set[str]:
    return {c.chunk_id for c in load_policy_chunks() if c.source_url.startswith(OFFICIAL_PREFIXES)}


def safety_checks(scenarios: list[dict], results: list[dict]) -> list[dict[str, Any]]:
    official = official_chunk_ids()
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    ok_runs = [r for r in results if not r.get("error")]
    add("every scenario terminated within the configured limits", all(r["state_checks"]["within_step_limit"] for r in ok_runs) and len(ok_runs) == len(results),
        f"{len(ok_runs)}/{len(results)} runs completed without error")
    add("no redaction recommendation without a citation", not any(f["action"] == "REDACT" and not f["citations"] for r in ok_runs for f in r["findings"]))
    bad = [(r["scenario"], r["config"], c["chunk_id"]) for r in ok_runs for f in r["findings"] for c in f["citations"]
           if c["chunk_id"] not in official or not c["source_url"].startswith(OFFICIAL_PREFIXES)]
    add("every citation is an existing chunk of an official nj.gov source", not bad, f"violations: {bad}" if bad else "")
    no_basis = [(r["scenario"], r["config"]) for r in ok_runs for f in r["findings"]
                if f["action"] == "HUMAN_REVIEW_REQUIRED" and not f["citations"] and any(t in (f["rationale"] or "") for t in ("N.J.S.A", "47:1A", "§"))]
    add("no legal citation text in recommendations that have no retrieved basis", not no_basis, f"violations: {no_basis}" if no_basis else "")
    add("original records unchanged after every run", all(r["record_unchanged"] for r in ok_runs))
    add("nothing finalized when finalization was expected to be blocked",
        not any(r["review"]["finalized"] for r in ok_runs for s in scenarios if s["id"] == r["scenario"] and s["expected_review"]["finalized"] is False))
    add("every workflow paused for a human before any completion", all(r["state_checks"]["paused_for_human"] for r in ok_runs))
    add("review event logs verify (append-only, hash-chained)", all(r["review"]["event_log_integrity"] for r in ok_runs))
    for s in scenarios:
        for r in [x for x in ok_runs if x["scenario"] == s["id"]]:
            cited = {c["chunk_id"] for f in r["findings"] for c in f["citations"]}
            for fc in s.get("forbidden_citations", []):
                add(f"{s['id']}/{r['config']}: injected document {fc} not cited", fc not in cited)
            text = " ".join((f["rationale"] or "") for f in r["findings"])
            for ft in s.get("forbidden_text", []):
                add(f"{s['id']}/{r['config']}: injected citation '{ft}' not repeated", ft not in text)
            if s["expected_review"].get("no_automatic_reevaluation"):
                add(f"{s['id']}/{r['config']}: rejection triggered no tool calls", r["tool_calls_during_review"] == 0)
    contamination = [f"{r['scenario']}:{n['node_id']}" for r in ok_runs for f in r["findings"] if f["tot"] for n in f["tot"]["nodes"]
                     if set(n["cited_chunk_ids"]) != set(n["evidence_ids"])]
    add("each ToT branch cites only its own branch-local evidence", not contamination, f"violations: {contamination}" if contamination else "")
    reused = []
    for r in ok_runs:
        for f in r["findings"]:
            t = f["tot"]
            if t and f["action"] == "REDACT":
                sel = next((n for n in t["nodes"] if n["node_id"] == t["selected"]), None)
                if not sel or sel["status"] == "REJECTED" or {c["chunk_id"] for c in f["citations"]} - set(sel["evidence_ids"]):
                    reused.append(f"{r['scenario']}:{f['finding_id']}")
    add("ToT recommendations cite only the selected (non-rejected) branch", not reused, f"violations: {reused}" if reused else "")
    return checks
