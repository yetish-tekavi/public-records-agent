# Scenario D-budget — ToT failure protection (tool budget 0)

Request `OPRA-TEST-204` → record `TEST-204`. Expected: Budget limit enforced: citations cannot be validated, no branch is selectable, case escalated.

Synthetic record text: *The caller, a resident of 31 Larchmont Terrace, asked that her noise complaint remain anonymous.*

Agents are deterministic (rule-based tool use over MCP); no LLM was called in this run.

## Checks

- [PASS] paused for human review; nothing authorized
- [PASS] every recommendation requires human approval
- [PASS] tool budget enforced
- [PASS] no branch selected; escalated

## State transitions

`orchestrator_start → record_analysis → policy_research → evidence_merge → compliance_qa → evidence_decision → tot → finalize_recommendations`

Route chosen by the Orchestrator: **tot**. Steps: 8 / 30.

## Tool calls

| # | Agent | Tool | Arguments | Status | Result | ms |
|---|---|---|---|---|---|---|
| 1 | record_analysis | detect_pii | `{"record_id": "TEST-204"}` | ok | 1 finding(s): STREET_ADDRESS (0.95) | 18 |
| 2 | policy_research | search_policy | `{"query": "What New Jersey law applies when a public record contains a person's home address?", "k": 4}` | ok | SUFFICIENT: opra_statute:0020, opra_statute:0053, opra_statute:0062, grc_exemptions:0025 | 349 |
| 3 | compliance_qa | propose_redactions | `{"record_id": "TEST-204", "finding_ids": ["TEST-204-P01"], "policy_chunk_ids": ["opra_statute:0020", "opra_statute:0053", "opra_statute:0062", "grc_exemptions:0` | ok | TEST-204-P01=HUMAN_REVIEW_REQUIRED | 3 |
| 4 | compliance_qa | validate_citations | `{"chunk_ids": ["opra_statute:0020", "grc_exemptions:0025", "opra_statute:0021", "grc_exemptions:0026"]}` | ok | 4/4 valid | 2 |

## Evidence and QA assessment

- STREET_ADDRESS: **FOUND**; queries ["What New Jersey law applies when a public record contains a person's home address?"]; chunks ['opra_statute:0020', 'opra_statute:0053', 'opra_statute:0062', 'grc_exemptions:0025']
- Assessment TEST-204-P01: **AMBIGUOUS** — STREET_ADDRESS is not listed without qualification; application depends on conditions.; A retrieved provision applies only to specific classes of person.

## Tree of Thought for TEST-204-P01

Config: branching ≤ 3, depth ≤ 3, beam 2, min score 70, tool budget 0, time 60.0s.

Outcome: **ESCALATED** (`HUMAN_REVIEW_REQUIRED`), selected `None` score None. Depth reached 2; tool calls 0; targeted retrieval used: False.

Reason: No branch reached 70/100 with verified support.

Selected path: `none`

| Node | Parent | Depth | Stage | Action | Provision | Cites | Assumptions | Rel | Cit | Cons | Expl | Total | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| d1.1 | - | 1 | INTERPRETATION | REDACT | RESTRICTED_CLASS | opra_statute:0020 | UNVERIFIED | 20 | 0 | 13 | 15 | 48 | EVALUATED |
| d1.2 | - | 1 | INTERPRETATION | HUMAN_REVIEW_REQUIRED | CONDITIONAL_REVIEW | opra_statute:0020, grc_exemptions:0025 | - | 15 | 0 | 25 | 10 | 50 | EVALUATED |
| d1.1>2 | d1.1 | 2 | VERIFICATION | REDACT | RESTRICTED_CLASS | opra_statute:0020 | UNSUPPORTED | 20 | 0 | 25 | 15 | 60 | REJECTED |

Pruning:

- `d1.1>2` (depth 2): rejected: unsupported factual assumption: The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).

Budget / limit events:

- depth 1: citation validation skipped, tool budget exhausted
- depth 2: citation validation skipped, tool budget exhausted
- `d1.1>2` assumption *The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).* → UNSUPPORTED (no supporting term in the record)

## Recommendations (all require human approval)

- `TEST-204-P01` → **HUMAN_REVIEW_REQUIRED** (detector confidence 0.95)
  - Rationale: Tree of Thought (AMBIGUOUS evidence): No branch reached 70/100 with verified support.
  - Open question: The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).

## Escalations

- TEST-204-P01: No branch reached 70/100 with verified support.

## Human review

Completion at pause: **AWAITING_HUMAN_REVIEW**; disclosure authorized: False.

## Audit log

| Step | Node | Agent | Event |
|---|---|---|---|
| 1 | orchestrator_start | orchestrator | plan |
| 2 | record_analysis | record_analysis | detect_pii OK |
| 3 | policy_research | policy_research | retrieved policy evidence |
| 4 | evidence_merge | orchestrator | merged findings with policy evidence |
| 5 | compliance_qa | compliance_qa | validated proposal and assessed evidence |
| 6 | evidence_decision | orchestrator | route=tot |
| 7 | tot | orchestrator | bounded Tree of Thought |
| 8 | finalize_recommendations | orchestrator | assembled recommendations |
