# Milestone 6 — ReAct-only vs ReAct + selective ToT (prefix)

Same 22 scenarios, corpus, retrieval settings, detector, and inputs; only `WorkflowConfig.enable_tot` differs. Deterministic agents: repeated runs give the same decisions, so one trial per configuration is reported (latency varies run to run).

## ToT activation

- Configuration B: activation confusion {'tp': 7, 'fp': 3, 'fn': 0, 'tn': 11, 'not_scored': 1} → accuracy 0.857 (18/21), recall 1.000 (7/7), false activation rate on straightforward cases 0.214 (3/14).
- Baseline A: activation confusion {'tp': 0, 'fp': 0, 'fn': 7, 'tn': 14, 'not_scored': 1} (ToT disabled, so it never activates).

## Branch validity, pruning, termination (Configuration B)

- ToT runs: 11; branches generated: 28 (depth-1 candidates 21); branches supported by retrieved official evidence: 21.
- Rejected: fabricated/altered citation 0, unsupported factual assumption 3, other 0; pruned by beam width: 1.
- Uncertain branches preserved (unverified, not discarded): 8; uncertain branches silently lost: 1.
- Invariant violations (branching ≤ 3, depth ≤ 3, beam ≤ 2, rejected nodes logged, branch-local citations): none.
- REDACT selections fully supported (all assumptions verified, score ≥ 70): 1.000 (4/4); escalated ToT runs: 7; tie/material-conflict events: 0 (tie rule exercised only by unit tests).

| Scenario | Finding | Outcome | Selected | Score | Nodes | Pruned | Depth | ToT tool calls | Targeted retrieval |
|---|---|---|---|---|---|---|---|---|---|
| S02 | `D12345678901234` | ESCALATED | d1.1 | 85 | 1 | 0 | 1 | 1 | False |
| S03 | `Talia Brookfield` | ESCALATED | d1.2 | 85 | 4 | 0 | 3 | 2 | True |
| S06 | `0123 covers road` | ESCALATED | d1.2 | 85 | 3 | 1 | 2 | 1 | False |
| S07 | `3 main drive` | ESCALATED | d1.2 | 85 | 3 | 1 | 2 | 1 | False |
| S12 | `9 Juniper Court` | SUPPORTED_RECOMMENDATION | d1.1>2 | 100 | 3 | 0 | 2 | 3 | True |
| S13 | `31 Larchmont Terrace` | ESCALATED | d1.2 | 85 | 3 | 1 | 2 | 1 | False |
| S14 | `r.vossberg@example.com` | ESCALATED | d1.1 | 85 | 1 | 0 | 1 | 1 | False |
| S15 | `(201) 555-0100` | SUPPORTED_RECOMMENDATION | d1.1 | 100 | 2 | 0 | 1 | 1 | False |
| S16 | `03/17/1988` | SUPPORTED_RECOMMENDATION | d1.1 | 100 | 2 | 0 | 1 | 1 | False |
| S19 | `14 Alder Lane` | ESCALATED | None | None | 3 | 0 | 2 | 3 | True |
| S20 | `52 Birchwood Lane` | SUPPORTED_RECOMMENDATION | d1.2 | 95 | 3 | 1 | 1 | 1 | False |

## Efficiency

- Additional tool calls in B vs A (scenarios where ToT ran): total 16, per scenario [1, 2, 1, 1, 3, 1, 1, 1, 1, 3, 1].
- Additional latency in B vs A (s): mean 0.092, per scenario [-0.02, 0.348, -0.108, -0.03, 0.333, -0.006, -0.015, 0.001, 0.076, 0.419, 0.013].
- LLM calls: 0 in both configurations; tokens: not applicable; estimated API cost: $0.00.

## Outcome comparison

| Measure | A | B |
|---|---|---|
| Recommendation in acceptable set | 1.000 (17/17) | 0.824 (14/17) |
| Preferred recommendation | 0.882 (15/17) | 0.824 (14/17) |
| Grounded citation accuracy | 1.000 (6/6) | 1.000 (7/7) |
| Unsupported redactions | 0 | 2 |
| Escalation recall | 0.818 (9/11) | 0.636 (7/11) |
| End-to-end success | 0.818 (18/22) | 0.682 (15/22) |
| Mean latency (s) | 0.414 | 0.428 |
| Mean tool calls per scenario | 3.82 | 4.55 |
