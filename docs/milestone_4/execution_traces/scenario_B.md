# Scenario B — Ambiguous policy

Request `OPRA-TEST-202` → record `TEST-202`. Expected: ToT activates, evaluates and prunes candidates, then recommends or escalates.

Synthetic record text: *A caller who identified himself as a retired Superior Court judge reported a fallen tree limb outside his home at 9 Juniper Court. He asked the crew not to block the driveway.*

Agents are deterministic (rule-based tool use over MCP); no LLM was called in this run.

## Checks

- [PASS] paused for human review; nothing authorized
- [PASS] every recommendation requires human approval
- [PASS] ToT invoked
- [PASS] several candidate interpretations generated
- [PASS] selected branch scored >= 70 with verified assumptions

## State transitions

`orchestrator_start → record_analysis → policy_research → evidence_merge → compliance_qa → evidence_decision → tot → finalize_recommendations`

Route chosen by the Orchestrator: **tot**. Steps: 8 / 30.

## Tool calls

| # | Agent | Tool | Arguments | Status | Result | ms |
|---|---|---|---|---|---|---|
| 1 | record_analysis | detect_pii | `{"record_id": "TEST-202"}` | ok | 1 finding(s): STREET_ADDRESS (0.6) | 20 |
| 2 | policy_research | search_policy | `{"query": "What New Jersey law applies when a public record contains a person's home address?", "k": 4}` | ok | SUFFICIENT: opra_statute:0020, opra_statute:0053, opra_statute:0062, grc_exemptions:0025 | 292 |
| 3 | compliance_qa | propose_redactions | `{"record_id": "TEST-202", "finding_ids": ["TEST-202-P01"], "policy_chunk_ids": ["opra_statute:0020", "opra_statute:0053", "opra_statute:0062", "grc_exemptions:0` | ok | TEST-202-P01=HUMAN_REVIEW_REQUIRED | 7 |
| 4 | compliance_qa | validate_citations | `{"chunk_ids": ["opra_statute:0020", "grc_exemptions:0025", "opra_statute:0021", "grc_exemptions:0026"]}` | ok | 4/4 valid | 4 |
| 5 | compliance_qa | validate_citations | `{"chunk_ids": ["opra_statute:0020", "grc_exemptions:0025"]}` | ok | 2/2 valid | 3 |
| 6 | compliance_qa | validate_citations | `{"chunk_ids": ["opra_statute:0052"]}` | ok | 1/1 valid | 2 |
| 7 | policy_research | search_policy | `{"query": "custodian shall redact home address judicial officer prosecutor law enforcement officer", "k": 4}` | ok | SUFFICIENT: opra_statute:0052, opra_statute:0020, grc_exemptions:0025, grc_handbook:0030 | 365 |

## Evidence and QA assessment

- STREET_ADDRESS: **FOUND**; queries ["What New Jersey law applies when a public record contains a person's home address?"]; chunks ['opra_statute:0020', 'opra_statute:0053', 'opra_statute:0062', 'grc_exemptions:0025']
- Assessment TEST-202-P01: **AMBIGUOUS** — STREET_ADDRESS is not listed without qualification; application depends on conditions.; A retrieved provision applies only to specific classes of person.

## Tree of Thought for TEST-202-P01

Config: branching ≤ 3, depth ≤ 3, beam 2, min score 70, tool budget 6, time 60.0s.

Outcome: **SUPPORTED_RECOMMENDATION** (`REDACT`), selected `d1.1>2` score 100. Depth reached 2; tool calls 3; targeted retrieval used: True.

Reason: Selected d1.1>2 (RESTRICTED_CLASS) with score 100/100; all assumptions verified.

Selected path: `d1.1 → d1.1>2`

| Node | Parent | Depth | Stage | Action | Provision | Cites | Assumptions | Rel | Cit | Cons | Expl | Total | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| d1.1 | - | 1 | INTERPRETATION | REDACT | RESTRICTED_CLASS | opra_statute:0020 | UNVERIFIED | 20 | 15 | 13 | 15 | 63 | EVALUATED |
| d1.2 | - | 1 | INTERPRETATION | HUMAN_REVIEW_REQUIRED | CONDITIONAL_REVIEW | opra_statute:0020, grc_exemptions:0025 | - | 15 | 30 | 25 | 15 | 85 | EVALUATED |
| d1.1>2 | d1.1 | 2 | VERIFICATION | REDACT | RESTRICTED_CLASS | opra_statute:0020, opra_statute:0052 | SUPPORTED | 30 | 30 | 25 | 15 | 100 | SELECTED |

Pruning:

- none

Budget / limit events:

- d1.1>2: targeted retrieval 'custodian shall redact home address judicial officer prosecutor law enforcement officer' -> ['opra_statute:0052', 'opra_statute:0020', 'grc_exemptions:0025', 'grc_handbook:0030']
- `d1.1>2` assumption *The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).* → SUPPORTED (record: 'identified himself as a retired Superior Court judge reported a fallen tree limb outside his')

## Recommendations (all require human approval)

- `TEST-202-P01` → **REDACT** (detector confidence 0.6)
  - Rationale: Tree of Thought (AMBIGUOUS evidence): Selected d1.1>2 (RESTRICTED_CLASS) with score 100/100; all assumptions verified. Redact: N.J.S.A. 47:1A-1.1 Definitions protects this home address if the person belongs to a covered class.
  - Citation: Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC Readable Version), N.J.S.A. 47:1A-1.1 Definitions, p. 7 (`opra_statute:0020`) https://www.nj.gov/grc/laws/act/Readable%20Version%20of%20OPRA%20(Sept%202024)(P.L.%202024,%20c.16).pdf
  - Citation: Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC Readable Version), N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined, p. 15 (`opra_statute:0052`) https://www.nj.gov/grc/laws/act/Readable%20Version%20of%20OPRA%20(Sept%202024)(P.L.%202024,%20c.16).pdf
  - Open question: Confirm the condition 'The person is a covered person (judicial officer, prosecutor, law enforcement officer, child protective investigator, or immediate family member).' (basis: record: 'identified himself as a retired Superior Court judge reported a fallen tree limb outside his').

## Escalations

- none

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
