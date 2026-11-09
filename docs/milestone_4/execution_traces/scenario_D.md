# Scenario D — ToT failure protection

Request `OPRA-TEST-204` → record `TEST-204`. Expected: Unsupported branch rejected; unresolved case escalated.

Synthetic record text: *The caller, a resident of 31 Larchmont Terrace, asked that her noise complaint remain anonymous.*

Agents are deterministic (rule-based tool use over MCP); no LLM was called in this run.

## Checks

- [PASS] paused for human review; nothing authorized
- [PASS] every recommendation requires human approval
- [PASS] ToT invoked
- [PASS] unsupported branch rejected
- [PASS] escalated to human review

## State transitions

`orchestrator_start → record_analysis → policy_research → evidence_merge → compliance_qa → evidence_decision → tot → finalize_recommendations`

Route chosen by the Orchestrator: **tot**. Steps: 8 / 30.

## Tool calls

| # | Agent | Tool | Arguments | Status | Result | ms |
|---|---|---|---|---|---|---|
| 1 | record_analysis | detect_pii | `{"record_id": "TEST-204"}` | ok | 1 finding(s): STREET_ADDRESS (0.95) | 15 |
| 2 | policy_research | search_policy | `{"query": "What New Jersey law applies when a public record contains a person's home address?", "k": 4}` | ok | SUFFICIENT: opra_statute:0020, opra_statute:0053, opra_statute:0062, grc_exemptions:0025 | 358 |
| 3 | compliance_qa | propose_redactions | `{"record_id": "TEST-204", "finding_ids": ["TEST-204-P01"], "policy_chunk_ids": ["opra_statute:0020", "opra_statute:0053", "opra_statute:0062", "grc_exemptions:0` | ok | TEST-204-P01=HUMAN_REVIEW_REQUIRED | 7 |
| 4 | compliance_qa | validate_citations | `{"chunk_ids": ["opra_statute:0020", "grc_exemptions:0025", "opra_statute:0021", "grc_exemptions:0026"]}` | ok | 4/4 valid | 5 |
| 5 | compliance_qa | validate_citations | `{"chunk_ids": ["opra_statute:0020", "grc_exemptions:0025"]}` | ok | 2/2 valid | 7 |

## Evidence and QA assessment

- STREET_ADDRESS: **FOUND**; queries ["What New Jersey law applies when a public record contains a person's home address?"]; chunks ['opra_statute:0020', 'opra_statute:0053', 'opra_statute:0062', 'grc_exemptions:0025']
- Assessment TEST-204-P01: **AMBIGUOUS** — STREET_ADDRESS is not listed without qualification; application depends on conditions.; A retrieved provision applies only to specific classes of person.

## Tree of Thought for TEST-204-P01

Config: branching ≤ 3, depth ≤ 3, beam 2, min score 70, tool budget 6, time 60.0s.

Outcome: **ESCALATED** (`HUMAN_REVIEW_REQUIRED`), selected `d1.2` score 85. Depth reached 2; tool calls 1; targeted retrieval used: False.

Reason: Escalated: no redaction branch was supported, so d1.2 (conditional review, 85/100) was selected. Rejected: d1.1>2 rejected: unsupported factual assumption: The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).. Not selectable (unverified or below 70): ['d1.1'].

Selected path: `d1.2`

| Node | Parent | Depth | Stage | Action | Provision | Cites | Assumptions | Rel | Cit | Cons | Expl | Total | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| d1.1 | - | 1 | INTERPRETATION | REDACT | RESTRICTED_CLASS | opra_statute:0020 | UNVERIFIED | 20 | 15 | 13 | 15 | 63 | EVALUATED |
| d1.2 | - | 1 | INTERPRETATION | HUMAN_REVIEW_REQUIRED | CONDITIONAL_REVIEW | opra_statute:0020, grc_exemptions:0025 | - | 15 | 30 | 25 | 15 | 85 | SELECTED |
| d1.1>2 | d1.1 | 2 | VERIFICATION | REDACT | RESTRICTED_CLASS | opra_statute:0020 | UNSUPPORTED | 20 | 15 | 25 | 15 | 75 | REJECTED |

Pruning:

- `d1.1>2` (depth 2): rejected: unsupported factual assumption: The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).

Budget / limit events:

- none
- `d1.1>2` assumption *The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).* → UNSUPPORTED (no supporting term in the record)

## Recommendations (all require human approval)

- `TEST-204-P01` → **HUMAN_REVIEW_REQUIRED** (detector confidence 0.95)
  - Rationale: Tree of Thought (AMBIGUOUS evidence): Escalated: no redaction branch was supported, so d1.2 (conditional review, 85/100) was selected. Rejected: d1.1>2 rejected: unsupported factual assumption: The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).. Not selectable (unverified or below 70): ['d1.1']. Escalate: retrieved provisions apply only under conditions the record does not establish; a reviewer must decide.
  - Citation: Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC Readable Version), N.J.S.A. 47:1A-1.1 Definitions, p. 7 (`opra_statute:0020`) https://www.nj.gov/grc/laws/act/Readable%20Version%20of%20OPRA%20(Sept%202024)(P.L.%202024,%20c.16).pdf
  - Citation: OPRA Exemptions (GRC list), N.J.S.A. 47:1A-1.1, exemption 20: Personal identifying information. Specifically, p. 4 (`grc_exemptions:0025`) https://www.nj.gov/grc/custodians/exempt/OPRA%20Exemptions%20(Sept%202024)(P.L.%202024,%20c.16).pdf

## Escalations

- TEST-204-P01: Escalated: no redaction branch was supported, so d1.2 (conditional review, 85/100) was selected. Rejected: d1.1>2 rejected: unsupported factual assumption: The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).. Not selectable (unverified or below 70): ['d1.1'].

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
