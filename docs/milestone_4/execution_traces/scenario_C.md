# Scenario C — Missing evidence

Request `OPRA-TEST-203` → record `TEST-203`. Expected: Bounded targeted retry, then human escalation; no invented citations.

Synthetic record text: *The complaint was submitted through the online portal from IP address 203.0.113.45 at 9:14 p.m. It concerned a broken streetlight near the community garden.*

Agents are deterministic (rule-based tool use over MCP); no LLM was called in this run.

## Checks

- [PASS] paused for human review; nothing authorized
- [PASS] every recommendation requires human approval
- [PASS] one targeted retry
- [PASS] HUMAN_REVIEW_REQUIRED with no citations
- [PASS] no statute or section invented

## State transitions

`orchestrator_start → record_analysis → policy_research → evidence_merge → compliance_qa → evidence_decision → targeted_retrieval → evidence_merge → compliance_qa → evidence_decision → finalize_recommendations`

Route chosen by the Orchestrator: **standard**. Steps: 11 / 30.

## Tool calls

| # | Agent | Tool | Arguments | Status | Result | ms |
|---|---|---|---|---|---|---|
| 1 | record_analysis | detect_pii | `{"record_id": "TEST-203"}` | ok | 1 finding(s): IP_ADDRESS (0.95) | 58 |
| 2 | policy_research | search_policy | `{"query": "What New Jersey law applies when a public record contains a person's IP address?", "k": 4}` | ok | SUFFICIENT: opra_statute:0053, grc_exemptions:0026, opra_statute:0023, opra_statute:0021 | 342 |
| 3 | policy_research | search_policy | `{"query": "internet protocol address personal information", "k": 4}` | ok | SUFFICIENT: opra_statute:0034, grc_exemptions:0025, opra_statute:0020, grc_exemptions:0042 | 326 |
| 4 | compliance_qa | propose_redactions | `{"record_id": "TEST-203", "finding_ids": ["TEST-203-P01"], "policy_chunk_ids": ["opra_statute:0053", "grc_exemptions:0026", "opra_statute:0023", "opra_statute:0` | ok | TEST-203-P01=HUMAN_REVIEW_REQUIRED | 5 |
| 5 | policy_research | search_policy | `{"query": "ip address personal information exempt from public access redaction", "k": 4}` | ok | SUFFICIENT: grc_exemptions:0062, grc_exemptions:0002 | 350 |
| 6 | compliance_qa | propose_redactions | `{"record_id": "TEST-203", "finding_ids": ["TEST-203-P01"], "policy_chunk_ids": ["opra_statute:0053", "grc_exemptions:0026", "opra_statute:0023", "opra_statute:0` | ok | TEST-203-P01=HUMAN_REVIEW_REQUIRED | 3 |

## Evidence and QA assessment

- IP_ADDRESS: **MISSING**; queries ["What New Jersey law applies when a public record contains a person's IP address?", 'internet protocol address personal information', 'ip address personal information exempt from public access redaction']; chunks ['opra_statute:0053', 'grc_exemptions:0026', 'opra_statute:0023', 'opra_statute:0021', 'opra_statute:0034', 'grc_exemptions:0025', 'opra_statute:0020', 'grc_exemptions:0042', 'grc_exemptions:0062', 'grc_exemptions:0002']
- Assessment TEST-203-P01: **MISSING** — No retrieved passage names IP_ADDRESS.

## Recommendations (all require human approval)

- `TEST-203-P01` → **HUMAN_REVIEW_REQUIRED** (detector confidence 0.95)
  - Rationale: Insufficient policy evidence: no retrieved passage names IP_ADDRESS after a targeted retry. Human review required.
  - Open question: Which provision, if any, applies to this information?

## Escalations

- TEST-203-P01: insufficient policy evidence; human review required.

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
| 6 | evidence_decision | orchestrator | route=targeted_retrieval |
| 7 | targeted_retrieval | policy_research | targeted retry for missing evidence |
| 8 | evidence_merge | orchestrator | merged findings with policy evidence |
| 9 | compliance_qa | compliance_qa | validated proposal and assessed evidence |
| 10 | evidence_decision | orchestrator | route=standard |
| 11 | finalize_recommendations | orchestrator | assembled recommendations |
