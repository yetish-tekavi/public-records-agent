# Milestone 6 — Summary

Evaluation of the four-agent LangGraph workflow, retrieval, MCP tools, selective ToT, and
human-review safeguards on 22 synthetic scenarios under two configurations (44 runs).

- **Sources:** [evaluation_results.md](evaluation_results.md), [tot_comparison.md](tot_comparison.md),
  [safety_test_results.md](safety_test_results.md), the machine-readable `evaluation_results.json`,
  and per-run traces in `execution_traces/final/`.
- **Pre-fix measurement:** kept in `evaluation_results_prefix.*`, `tot_comparison_prefix.md` and
  `execution_traces/prefix/`.

## What was verified before measuring

| Question | Finding |
|---|---|
| Are agent decisions LLM-driven? | **No.** All agents are deterministic and rule-based. No LLM is called, and no API key is configured. LLM calls, tokens and API cost are reported as 0, 0 and $0 because the workflow never constructs an LLM client |
| Do ToT generation, evaluation, pruning and branch tracking run? | **Yes.** Every ToT node, score and prune reason in the traces comes from executed runs |
| Existing evaluation framework? | None beyond per-scenario checks in demos and unit tests. The metrics and harness are new (`app/evaluation/`) |
| Checkpoint 3.1 retrieval | Preserved; its tests still pass |

## Results (final run)

| Metric | Baseline A (ToT off) | Configuration B (ToT on) |
|---|---|---|
| PII detection precision | 1.000 (17/17) | 1.000 (17/17) |
| PII detection recall | 0.895 (17/19) | 0.895 (17/19) |
| Recommendation in acceptable set | 1.000 (17/17) | 1.000 (17/17) |
| Preferred recommendation | 0.882 (15/17) | **0.941 (16/17)** |
| Grounded citation accuracy | 1.000 (6/6) | 1.000 (7/7) |
| Unsupported redactions | 0 | 0 |
| Workflow completion | 1.000 (22/22) | 1.000 (22/22) |
| End-to-end success | 1.000 (22/22) | 1.000 (22/22) |
| Tool failure safe handling / full recovery | 1.000 (3/3) / 1.000 (3/3) | 1.000 (3/3) / 1.000 (3/3) |
| Escalation recall / false escalation rate | 1.000 (11/11) / 0.000 (0/9) | 1.000 (11/11) / 0.000 (0/9) |
| Latency mean / median / p95 (s) | 0.36 / 0.344 / 1.023 | 0.38 / 0.35 / 0.93 |

**ToT (Configuration B):**
- **Activation:** 7 true positives, 0 false positives, 0 false negatives, 14 true negatives (S16 not scored).
- **Branches:** 8 runs; 25 branches generated; 2 rejected for unsupported factual assumptions; 13
  uncertain branches preserved and none lost.
- **Limits:** no invariant violations (branching, depth, beam, prune logging, branch-local citations).
- **Decisions:** the one REDACT selection was fully supported; 7 runs were escalated.
- **Cost of ToT:** 13 extra tool calls and +0.12 s mean latency per ToT scenario; 0 LLM calls.

**Safety:** 16 of 16 checks passed across all 44 runs, and 40 of 40 mapped guardrail tests passed.
**Test suite:** 314 passed, 0 failed, 0 skipped.

The latency values in the table are from an earlier final run. The latest values are in
`evaluation_results.json`; latency varies by a few hundredths of a second between runs.

## What ToT changes (measured, not assumed)

- **Gain:** one scenario. S12 (retired judge's address) is REDACT under B, citing 47:1A-5(a)'s
  covered-person provision; A abstains to human review.
- **Everything else is identical** across A and B: actions, grounding, escalation, end-to-end success.
- **Cost:** 13 extra tool calls in total, and roughly 0.1 s per ToT scenario.
- **Before the fixes, ToT made results worse:** B had 2 unsupported redactions (S15, S20) and 3 false
  activations, against 0 and 0 for A. The ToT's own check called all 4 of its REDACT selections
  "fully supported", which shows that a rubric score measures internal consistency, not correctness.
- **Sample size:** 22 scenarios with 7 ToT cases is too few to generalise. The evidence supports only
  "selective ToT did not reduce safety and improved one ambiguous case."

## Defects found by the evaluation, and fixes

| # | Scenario | Pre-fix behaviour | Root cause | Fix | Regression test |
|---|---|---|---|---|---|
| F1 | S20 | B **redacted** a resident's address citing only the injected non-official document; A listed it as a citation | Retrieved text from any source could become cited authority | Provenance guard: Policy Research drops non-nj.gov passages (recorded in `excluded_sources`); `validate_citations` marks them invalid; QA and ToT reject them | `test_F1_*` (3 tests) |
| F2 | S15 | B redacted an agency number detected at 0.40 confidence | The ToT ignored detection confidence | REDACT branches get a "detection is correct" condition that only a reviewer can confirm | `test_F2_low_detection_confidence_blocks_tot_redaction_until_confirmed` |
| F3 | S06, S07 | Purchase-order and equipment text detected as street addresses | Presidio's default regex flags include IGNORECASE | Street-address recognizer made case-sensitive | `test_F3_street_address_recognizer_is_case_sensitive` |
| F4 | S02 | B escalated a driver licence number (false ToT activation) | Retrieval returned only the GRC list, not 47:1A-5(a)'s operative text | For categories OPRA lists outright, Policy Research runs its reformulated query until operative text is found; reformulations use the statute's wording | `test_F4_research_continues_until_operative_text_for_categorical_types` |

**Expected outcomes were not changed to make results pass.** One fixture was changed. The S20
injected document was reworded to resemble the agents' own questions, because the first version was
never retrieved and therefore tested nothing. This is documented in `app/evaluation/scenarios.py`,
and the pre-fix run was repeated with it.

## Missed detections (S04, S05): what changed and what didn't

**The detector still misses them.** Recall is 17/19 in every run: Presidio does not report a
spelled-out number or a 7-digit number without an area code.

The workflow now handles them in two steps:
1. **QA flags them.** The Compliance & QA Agent's pattern sweep gained two patterns: a 7-digit number
   that isn't part of a longer hyphenated code, and a run of 7 or more spelled-out digits. Each flag
   is a session escalation, and finalization is blocked until a reviewer acknowledges it. Across all
   44 runs, the new patterns flagged only S04 and S05, with no new false escalations.
2. **The reviewer adds the redaction.** In `ReviewSession` and the Streamlit app, a reviewer can
   select exact text in the original record, give a category and a reason, then confirm the span.
   It's recorded as an append-only reviewer decision, explicitly with *no policy basis claimed by
   the system*, and is applied deterministically like any other span.

**Why the success rate rose:** before this change, S04 and S05 failed (end-to-end 20/22, escalation
recall 9/11). They now succeed (22/22, 11/11) **because the system flags them and the scripted
reviewer acts on the flag.** Their expected outcomes did not change. Their reviewer scripts went
from empty, because no such action existed, to "acknowledge flag + add redaction".

A real deployment still depends on the reviewer acting on the flag. Formats outside the two sweep
patterns would still be missed.

**Tests:** 17 new tests in `tests/test_m6_manual_redaction.py`, covering the sweep, validation,
the confirmation requirement, withdrawal, overlap merging, the audit record, the UI, and an
end-to-end run with a detector that misses everything.

## Remaining limitation

- **S16, conservative abstention.** The date of birth is detected at 0.55 confidence, so both
  configurations escalate instead of redacting the month and day. The scripted reviewer then redacts
  "03/17" and keeps "1988".

## Limitations

1. **Small, self-authored dataset.** 22 scenarios and 19 expected findings, written with knowledge of
   the system. The metrics show behaviour on these cases, not general accuracy.
2. **Deterministic agents.** There is no nondeterminism to sample, so repeated trials would add nothing
   except latency variation. Results say nothing about LLM-driven behaviour.
3. **Scripted reviewer.** Reviewer actions are simulated; usability with real reviewers was not evaluated.
4. **Retrieval sensitivity.** Several outcomes depend on which 4 passages hybrid retrieval returns
   (for example, F4). Different wording can change results.
5. **In-process transport.** The evaluation used the in-process tool executor, with the same
   ToolService the MCP server exposes. MCP transport is exercised by separate integration tests and
   the Milestone 4 and 5 demos.
6. **Rubric scores are not probabilities of legal correctness.** The pre-fix S15 and S20 results show
   that a "fully supported" branch can still be wrong.

## Recommendations for the final capstone (November 19)

- **Present:** the A-vs-B comparison, the four evaluation-driven fixes, and the S04 and S05 limitation, as honest findings.
- **Feature freeze:** keep to November 15. The reviewer "add redaction" action and the extended QA
  sweep were added before the freeze to address missed detections. After the freeze, fix only
  critical defects.
- **Demo:** S01 (clear), S12 (ToT gain), S20 (injection blocked), S10 (missing evidence blocked).

## Completion criteria

| Criterion | Status |
|---|---|
| At least 15 synthetic scenarios evaluated | Met (22) |
| All original metrics reported | Met |
| Selective ToT activation and bypass tested | Met (7 TP, 14 TN, 0 FP or FN, plus unit tests) |
| Branch generation, scoring, pruning, termination validated | Met (trace invariants plus unit tests; beam pruning and ties covered by unit tests only, because no scenario triggered them) |
| ReAct-only vs ReAct + ToT compared | Met |
| Safety and missing-evidence escalation tests run | Met (16/16 checks, 40/40 guardrail tests) |
| Human authorization safeguards verified | Met |
| Results, failures and limitations documented | Met (detector recall gap documented) |
| Regression suite passing | Met (314 passed) |
