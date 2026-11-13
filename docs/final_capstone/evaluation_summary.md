# Evaluation Summary

All numbers below come from the committed Milestone 6 run at commit **`7a1a875`**
(`docs/milestone_6/evaluation_results.json`, generated 2026-11-10T23:57:50Z, Python 3.14.7 on
Windows 10). Nothing in this file was estimated or re-typed from memory.

- **Recomputable.** `python -m app.evaluation.verify` recomputes every headline metric from the 44
  raw per-run traces in `docs/milestone_6/execution_traces/final/`. It matched the reported values
  on **33 of 33** metrics during Milestone 7.
- **Rerun for Milestone 7.** The evaluation was rerun under a separate tag (`--tag m7check`). See
  [final_verification.md](final_verification.md) for that result.

## Method

| Item | Value |
|---|---|
| Scenarios | **22** synthetic scenarios (S01–S22), 19 expected sensitive findings |
| Configurations | **A**: ReAct-only (`WorkflowConfig.enable_tot = False`). **B**: ReAct + selective ToT (`enable_tot = True`) |
| Runs | 22 × 2 = **44** |
| What is held constant | Corpus (5 nj.gov documents, 496 chunks), hybrid retrieval (BM25 + ChromaDB all-MiniLM-L6-v2, k = 4), Presidio detector, inputs, reviewer scripts |
| Agents | Deterministic rule-based agents. **No LLM is called**: 0 LLM calls, tokens not applicable, $0.00 API cost |
| Reviewer | Scripted from each scenario definition (simulated reviewer) |
| Tool transport | In-process executor wrapping the same `ToolService` the MCP server exposes. MCP transport is covered by integration tests and the Milestone 4 and 5 demos |
| Trials | One per configuration. The agents are deterministic, so repeated runs give the same decisions and only latency varies |

### Scenario coverage

| Group | Scenarios |
|---|---|
| Clear statutory categories | S01 personal phone, S02 driver licence, S03 name + phone, S17 phone with detector failure, S21 reviewer rejection, S22 injected text in the record |
| Detector edge cases | S04 phone spelled out in words, S05 7-digit phone without an area code |
| No-PII decoys | S06 purchase order resembling an address, S07 equipment description, S08 budget note, S09 asset and work-order identifiers |
| Missing policy evidence | S10, S11 IP addresses |
| Ambiguous or conflicting policy | S12 retired judge's home address, S13 private resident's address, S14 personal email, S15 agency main line, S16 date of birth |
| Tool failures | S17 detector fails once, S18 policy search always fails, S19 citation validation fails during ToT |
| Prompt injection | S20 injected instructions in a retrieved non-official document, S22 instructions inside the record |

## Headline results

| Metric | A: ReAct-only | B: ReAct + ToT | Definition |
|---|---|---|---|
| PII detection precision | 1.000 (17/17) | 1.000 (17/17) | matched / detected |
| PII detection recall | **0.895 (17/19)** | **0.895 (17/19)** | matched / expected. The 2 misses are S04 and S05 |
| Recommendation in acceptable set | 1.000 (17/17) | 1.000 (17/17) | over matched findings |
| Preferred recommendation | 0.882 (15/17) | **0.941 (16/17)** | over matched findings |
| Grounded citation accuracy | 1.000 (6/6) | 1.000 (7/7) | REDACT recommendations whose first citation is an expected section and all citations are official, existing chunks |
| Unsupported redactions | 0 | 0 | REDACT where not acceptable |
| Conservative abstentions | 2 | 1 | HUMAN_REVIEW where REDACT was preferred |
| Workflow completion | 1.000 (22/22) | 1.000 (22/22) | paused for human review within limits |
| End-to-end success | 1.000 (22/22) | 1.000 (22/22) | expected review outcome, expected escalation, no unsupported redaction |
| Correct escalations (recall) | 1.000 (11/11) | 1.000 (11/11) | escalated / expected escalations |
| False escalation rate | 0.000 (0/9) | 0.000 (0/9) | escalated / expected no escalation |
| Tool failure: safe handling | 1.000 (3/3) | 1.000 (3/3) | no unsupported redaction after a fault |
| Tool failure: full recovery | 1.000 (3/3) | 1.000 (3/3) | also correct actions and outcome |
| Latency mean / median / p95 (s) | 0.377 / 0.371 / 1.059 | 0.388 / 0.364 / 0.987 | workflow wall time per scenario, excluding review |
| Mean tool calls per scenario | 3.59 | 4.18 | MCP-tool calls recorded in state |
| LLM calls / tokens / cost | 0 / n/a / $0.00 | 0 / n/a / $0.00 | no LLM client is constructed |

### Detection errors

The detector is identical in both configurations.

- **S04** missed `six zero nine, five five five, zero one three one` (phone number in words).
- **S05** missed `5550142` (7-digit number without an area code).
- **How these scenarios still pass end to end.** The Compliance & QA sweep flags both as session
  escalations, and the reviewer adds the redaction manually. Without that reviewer action they would
  fail. The detector itself still misses them.

## ToT results (configuration B)

| Measure | Result |
|---|---|
| Activation (scored on 21 scenarios; S16 not scored) | TP 7, FP 0, FN 0, TN 14. Accuracy 1.000 (21/21), recall 1.000 (7/7), false activation 0.000 (0/14) |
| Bypass on straightforward cases | 14 / 14 bypassed ToT |
| ToT runs | 8 (one per ambiguous finding) |
| Branches generated | 25 (15 at depth 1); 20 supported by retrieved official evidence |
| Invalid branches rejected | 2 for an unsupported factual assumption; 0 for a fabricated or altered citation |
| Pruned by beam width | 0 in the scenarios (beam pruning is covered by unit tests) |
| Uncertain branches | 13 preserved as UNVERIFIED; 0 silently lost |
| Termination | 8 of 8 runs ended within branching ≤ 3, depth ≤ 3, beam ≤ 2; no invariant violations |
| Outcomes | 1 supported REDACT (S12, score 100, all assumptions verified); 7 escalated to human review |
| Extra cost against A | 13 extra tool calls in total; +0.111 s mean latency per ToT scenario; 0 LLM calls |

## Safety results

- **16 of 16** safety checks passed over all 44 runs. Among them:
  - no redaction without a citation;
  - every citation is an existing chunk of an official source;
  - originals unchanged;
  - nothing finalized when finalization was expected to be blocked;
  - injected content was never cited;
  - rejection triggered no tool calls;
  - ToT branches cite only branch-local evidence.
- **40 of 40** guardrail tests mapped to safety requirements passed
  (`docs/milestone_6/safety_test_results.md`).

## Defects found by the evaluation (and fixed before the final run)

| # | Scenario | Before the fix | Fix |
|---|---|---|---|
| F1 | S20 | B redacted an address citing only an injected non-official document | Provenance guard: only nj.gov passages can be cited |
| F2 | S15 | B redacted an agency number detected at 0.40 confidence | Low-confidence detection adds a reviewer-only "detection is correct" condition |
| F3 | S06, S07 | Purchase-order and equipment text detected as street addresses | Street-address recognizer made case-sensitive |
| F4 | S02 | B escalated a driver licence (false ToT activation) | Policy Research keeps searching until operative statutory text is found for categories OPRA lists outright |

The pre-fix measurement is preserved in `docs/milestone_6/evaluation_results_prefix.*`. Before the
fixes, B had **2 unsupported redactions and 3 false activations**, against 0 and 0 for A.
**Expected outcomes were not changed to make results pass.**

## How to reproduce

```bash
python -m app.evaluation.run --tag rerun     # writes evaluation_results_rerun.json/.md and traces/rerun (leaves the committed final files alone)
python -m app.evaluation.verify              # recomputes the committed metrics from the committed traces
python -m app.evaluation.safety_report       # guardrail tests mapped to safety requirements (runs the full suite)
```

Running `python -m app.evaluation.run` with no tag **overwrites** the committed final results.
Decisions will be identical, but latency values will differ slightly.

## Interpretation limits

- **Small, self-authored dataset.** 22 scenarios and 19 expected findings, written by the developer
  with knowledge of the system. The numbers describe behaviour on these cases, not general accuracy.
- **Few ToT cases.** 7 ToT-positive scenarios is too few for statistical claims.
- **Deterministic agents.** Results say nothing about LLM-driven behaviour.
