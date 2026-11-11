# Milestone 6 — Evaluation Plan

## Environment and configuration

| Item | Value |
|---|---|
| Runner | `python -m app.evaluation.run` (results), `python -m app.evaluation.safety_report` (guardrail tests) |
| Python and OS | 3.14.7 on Windows 10 (also recorded in `evaluation_results.json → environment`) |
| Detection | Presidio 2.2.364 with spaCy `en_core_web_md` 3.8.0 and the project's custom recognizers |
| Retrieval | Hybrid BM25 + semantic (ChromaDB 1.5.9, all-MiniLM-L6-v2), k = 4, over the 5 official NJ documents (496 chunks) |
| Orchestration | LangGraph 1.2.20 workflow from Milestone 4; review session from Milestone 5 |
| Tool transport | In-process `ToolService`, the same implementation the MCP server exposes. The MCP path itself is covered by integration tests |
| Planner and agents | **Deterministic rule-based agents.** No LLM is called anywhere in the evaluated workflow |
| Configurations | **Baseline A**: `WorkflowConfig(enable_tot=False)`. **Configuration B**: `enable_tot=True`. Everything else is identical |
| ToT settings | branching ≤ 3, depth ≤ 3, beam 2, minimum score 70, tool budget 6, time budget 60 s, one targeted retrieval |
| Limits | 30 workflow steps; 180 s timeout per scenario in the harness |

## Dataset

- **Definition:** 22 synthetic scenarios, defined in `app/evaluation/scenarios.py` and materialised by
  `app/evaluation/dataset.py`:
  - inputs: `data/milestone_6/synthetic_records|synthetic_requests` (what the agents see);
  - ground-truth findings: `data/milestone_6/expected_results/`;
  - expected outcomes: `data/milestone_6/expected/scenarios.json`, copied to
    [synthetic_test_cases.json](synthetic_test_cases.json). The agents never read this file.
- **Fictional values only:** 555-01XX phone numbers, RFC 5737 IP addresses, example.com email.

| Category | Scenarios |
|---|---|
| Correctly identified personal information | S01, S02, S03 |
| Missed sensitive information | S04 (spelled-out number), S05 (7-digit number) |
| False-positive detection | S06 (purchase-order number), S07 (equipment description) |
| No sensitive information | S08, S09 |
| No applicable policy | S10, S11 (IP addresses; the corpus never mentions them) |
| Ambiguous interpretation | S03 (name), S12 (retired judge's address), S13 (resident's address), S14 (personal email), S19 |
| Conflicting or incomplete evidence | S15 (agency number vs personal-number rule), S16 (date of birth: month and day only) |
| Invalid or failed tool responses | S17 (detector fails once), S18 (search always fails), S19 (citation validation fails during ToT) |
| Reviewer rejection or correction | S03, S16 (modification), S21 (rejection) |
| Prompt injection | S20 (injected instructions in a retrieved, non-official document), S22 (instructions inside the record) |

**ToT expectations:**
- **Should activate** (`expected_tot = true`): S03, S12, S13, S14, S15, S19, S20.
- **Must not activate:** 14 scenarios.
- **Not scored:** S16, where activation depends only on detector confidence.

## Ground truth

Each expected finding records:
- category, exact text and offsets;
- the expected evidence class;
- the **acceptable actions** and the **preferred action**;
- the acceptable citation sections;
- a `policy_basis` taken from the official text.

The rules used to set expectations:

- **REDACT is acceptable only when** the official text directs it for that information. N.J.S.A.
  47:1A-5(a) covers personal telephone numbers and driver licence numbers, and the home address of
  covered persons only. N.J.S.A. 47:1A-1.1 covers month and day of birth.
- **Human review is the only acceptable action when** the text makes the answer conditional:
  names (privacy balancing), private residents' addresses, personal email, agency numbers, IP addresses.
- **Both are acceptable when** a supported redaction exists but depends on a condition a human must
  confirm (S12, S16). In those cases REDACT is *preferred*.
- **No exemptions or conflicts were invented.** S15's conflict is the statute's own text: 5(a) covers
  a *personal* telephone number, and the 2024 definition excludes "telephone number of a public agency".

## Metrics (numerator / denominator; how abstentions and escalations count)

| Metric | Numerator | Denominator | Abstentions and escalations |
|---|---|---|---|
| PII detection precision | Detected findings matching an expected one (same category, overlapping span) | All detected findings | n/a (detection only) |
| PII detection recall | Expected findings matched | All expected findings | n/a |
| Recommendation in acceptable set | Matched findings whose action is acceptable | Matched findings | HUMAN_REVIEW counts as correct only when it is acceptable |
| Preferred recommendation | Matched findings whose action equals the preferred one | Matched findings | Conservative abstentions (HUMAN_REVIEW where REDACT was preferred) are counted separately |
| Grounded citation accuracy | REDACT recommendations whose **first** citation is an expected section **and** every citation is an existing chunk of an official nj.gov source | REDACT recommendations on findings that have expected sections | Abstentions are excluded from the denominator; reported as abstention counts |
| Unsupported redactions | Count of REDACT where REDACT is not acceptable, plus REDACT of any false-positive finding | — | — |
| Workflow completion | Runs that paused for human review within the step limit, without error | Scenarios | — |
| End-to-end success | Runs that meet the expected review outcome (finalized or blocked; required spans redacted, required text kept, derivative unchanged where expected; no unprompted tool calls after rejection) **and** the expected escalation **and** contain no unsupported redaction | Scenarios | An escalation is correct when it was expected |
| Tool failure: safe handling | Fault scenarios that completed with no unsupported redaction | Fault scenarios (3) | Escalation counts as safe |
| Tool failure: full recovery | Safe **and** correct actions and review outcome | Fault scenarios | — |
| Escalation recall | Scenarios escalated (any HUMAN_REVIEW recommendation, workflow escalation or record failure) where escalation was expected | Scenarios expecting escalation | Scenarios with no fixed expectation (S12, S16) are not scored |
| False escalation rate | Scenarios escalated where none was expected | Scenarios expecting no escalation | — |
| Processing time | Workflow wall time per scenario (mean, median, p95) | Scenarios | The review step is excluded |

## ToT metrics (Configuration B)

| Measure | Definition |
|---|---|
| Activation accuracy, recall and false-activation rate | Scenario-level ToT activation vs `expected_tot` |
| Branches generated, supported, rejected | Supported = not rejected or pruned, with evidence and citation validity ≥ 15. Rejected reasons are classified from the prune log |
| Pruning and limits | Every rejected node appears in the prune log; depth-1 candidates ≤ 3; nodes per depth (≥ 2) ≤ 2; depth reached ≤ 3; each branch cites only its own evidence |
| Uncertain branches | Nodes with an UNVERIFIED condition that were kept (not pruned or rejected), with their questions surfaced as unresolved |
| Decision quality | Share of REDACT selections with every condition SUPPORTED and score ≥ 70; escalated runs; tie and material-conflict events |
| Efficiency | Extra tool calls and latency of B vs A in scenarios where ToT ran; LLM calls; tokens; API cost |

## Method

1. Run all 22 scenarios under A, then under B, with the same services.
2. Inject faults at the executor level, as each scenario specifies.
3. Apply the scenario's **scripted** reviewer actions (approve, reject, modify) to a real `ReviewSession`, then attempt finalization with authorization.
4. Compute metrics only from recorded results (`execution_traces/final/*.json`).
5. Measure as the code stood ("prefix"), fix only the defects found, measure again ("final"), and report both.

The agents are deterministic, so repeated runs give the same decisions. Latency varies from run to run.
