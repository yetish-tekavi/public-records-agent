# Milestone 5 — Summary

**Human review and redaction execution with ToT integration.** Results come from
`python -m app.m5_demo` (tools via the MCP server), recorded in [review_scenarios.md](review_scenarios.md)
and [test_results.md](test_results.md).

## Completion criteria

| Criterion | Evidence | Status |
|---|---|---|
| A clear-policy recommendation can be approved and redacted | Scenario A: REDACT citing N.J.S.A. 47:1A-5(a), approved, finalized; derivative "…My personal phone number is [REDACTED: PHONE_NUMBER]." | Met |
| A ToT recommendation displays evidence and alternative branches | Scenario B and UI test `test_tot_recommendation_shows_branches_scores_and_pruning`: the selected branch, every actual branch with its scores, pruning and retrieval status | Met |
| Reviewer feedback is preserved and can trigger bounded re-evaluation | Scenario B: structured feedback re-ran the ToT; REDACT (100) became HUMAN_REVIEW_REQUIRED (85); previous recommendation kept; limit of 2 enforced (test) | Met |
| Unresolved findings cannot be finalized | Scenario C: finalization blocked ("TEST-203-P01 is not resolved (status ESCALATED)"), and the blocked attempt was recorded | Met |
| Original records are never overwritten | File hash checked in Scenario A and in tests; finalization refuses the original path and a changed source | Met |
| Audit reports accurately reflect actual execution | Reports built from session events and the workflow snapshot; a test checks that branch scores in the report equal the ToT result | Met |
| Existing and new tests pass | 277 passed (242 earlier plus 35 new) | Met |

## Stages

| Stage | Files | Result |
|---|---|---|
| 1. Schemas and review states | `app/review/session.py`; `Assumption.key`, `reviewer_facts`, `recommendation_from_tot` in `app/workflows/` | Item statuses, append-only hash-chained events, re-evaluation request schema |
| 2. Streamlit reviewer interface | `app/review_app.py` | Request, original record with highlights, findings table, per-finding cards, a "Reasoning and Evidence" panel |
| 3. Approve, reject, modify | `session.py` | Approve only supported redactions; reason required to modify; spans must be confirmed |
| 4. Re-evaluation | `app/review/reevaluation.py` | Reviewer-initiated only; retrieval only when needed; existing ToT reused; feedback accumulates; 2-attempt limit, then manual resolution |
| 5. Deterministic redaction | `app/review/redact.py` | Validated spans, overlap merge, no collateral change, repeated-value guard |
| 6. Final authorization | `session.finalize` | Separate action, five conditions, blocked attempts recorded, nothing published |
| 7. Audit and tests | `app/review/report.py`, `tests/test_m5_*.py`, `app/m5_demo.py` | Audit reports, 35 new tests, scenario evidence |

## Scenario results

| Scenario | What happened | Final state |
|---|---|---|
| A — clear policy | Standard workflow (no ToT); approved; finalized; LangGraph run resumed to APPROVED_BY_HUMAN | Finalized derivative (not published) |
| B — ambiguous policy | Actual ToT result shown (3 branches, selected 100). The reviewer requested re-evaluation, marking covered status unconfirmed. The ToT re-ran: the redaction branch scored 88 but was **not selectable** (unverified condition), and the review branch (85) was selected | Updated recommendation HUMAN_REVIEW_REQUIRED, returned for decision |
| C — missing evidence | Escalated with no citations. The reviewer's re-evaluation searched again and still found no basis. Finalization blocked | Escalated; not finalized |

Reviewer inputs in the demo were **scripted** and labelled as such. In normal use a person enters
them in the Streamlit app.

## Bugs found and fixed during the milestone

1. **Reviewer feedback was lost.** A second re-evaluation without factual answers fell back to the
   record-text heuristic and returned to REDACT. Factual answers now accumulate across attempts,
   and a test covers this.
2. **UI errors showed late.** Refused actions displayed their error one interaction late; the app
   now reruns immediately.

## Implemented vs. not implemented

- **Implemented and tested:** everything listed above.
- **Not implemented:**
  - multi-user access control or authentication (the reviewer name is self-entered);
  - persistent resumption of a paused LangGraph run in a new process;
  - audio and video redaction;
  - automated end-to-end browser tests (the UI is tested with Streamlit's headless `AppTest`).
- **Agents remain deterministic.** No LLM is called, so there is no model reasoning to hide or show.

## Remaining work (Stage 7)

- **Evaluation:** a broader synthetic evaluation set and metrics (detection precision and recall,
  routing accuracy, escalation rate).
- **Observability:** a summary across runs.
- **Final deliverables:** the final demo script, capstone report and presentation outline.
