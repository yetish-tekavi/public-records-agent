# Scenario A — Straightforward policy

Request `OPRA-TEST-201` → record `TEST-201`. Expected: Standard workflow, no ToT invocation.

Synthetic record text: *During the interaction, the resident stated: My personal phone number is 609-555-0142.*

Agents are deterministic (rule-based tool use over MCP); no LLM was called in this run.

## Checks

- [PASS] paused for human review; nothing authorized
- [PASS] every recommendation requires human approval
- [PASS] ToT not invoked
- [PASS] REDACT citing N.J.S.A. 47:1A-5(a)
- [PASS] disclosure authorized only after the human decision

## State transitions

`orchestrator_start → record_analysis → policy_research → evidence_merge → compliance_qa → evidence_decision → finalize_recommendations`

Route chosen by the Orchestrator: **standard**. Steps: 7 / 30.

## Tool calls

| # | Agent | Tool | Arguments | Status | Result | ms |
|---|---|---|---|---|---|---|
| 1 | record_analysis | detect_pii | `{"record_id": "TEST-201"}` | ok | 1 finding(s): PHONE_NUMBER (0.75) | 3538 |
| 2 | policy_research | search_policy | `{"query": "What New Jersey law applies when a public record contains a person's personal telephone number?", "k": 4}` | ok | SUFFICIENT: grc_exemptions:0026, opra_statute:0053, opra_statute:0052, grc_handbook:0031 | 1857 |
| 3 | compliance_qa | propose_redactions | `{"record_id": "TEST-201", "finding_ids": ["TEST-201-P01"], "policy_chunk_ids": ["grc_exemptions:0026", "opra_statute:0053", "opra_statute:0052", "grc_handbook:0` | ok | TEST-201-P01=REDACT | 5 |
| 4 | compliance_qa | validate_citations | `{"chunk_ids": ["opra_statute:0052", "grc_exemptions:0026", "grc_handbook:0031", "opra_statute:0053", "opra_statute:0054"]}` | ok | 5/5 valid | 3 |

## Evidence and QA assessment

- PHONE_NUMBER: **FOUND**; queries ["What New Jersey law applies when a public record contains a person's personal telephone number?"]; chunks ['grc_exemptions:0026', 'opra_statute:0053', 'opra_statute:0052', 'grc_handbook:0031']
- Assessment TEST-201-P01: **CLEAR** — N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined names this category with operative language.

## Recommendations (all require human approval)

- `TEST-201-P01` → **REDACT** (detector confidence 0.75)
  - Rationale: Retrieved policy names this category: '...mation which discloses the social security number, credit card number, personal telephone number, or driver license number of any person, or, in accordance with section 2 of P....'.
  - Citation: Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC Readable Version), N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined, p. 15 (`opra_statute:0052`) https://www.nj.gov/grc/laws/act/Readable%20Version%20of%20OPRA%20(Sept%202024)(P.L.%202024,%20c.16).pdf
  - Citation: OPRA Exemptions (GRC list), N.J.S.A. 47:1A-1.1, exemption 20: Personal identifying information. Specifically, p. 4 (`grc_exemptions:0026`) https://www.nj.gov/grc/custodians/exempt/OPRA%20Exemptions%20(Sept%202024)(P.L.%202024,%20c.16).pdf
  - Citation: The Open Public Records Act: Handbook for Records Custodians, Eighth Edition, SECTION 1 – OPRA DEFINED: What is a “government record” under OPRA?, p. 10 (`grc_handbook:0031`) https://nj.gov/grc/custodians/handbook/Custodians%20Handbook%20(Eighth%20Edition%20-%20Jan%202025)(Final).pdf
  - Citation: Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC Readable Version), N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined, p. 15 (`opra_statute:0053`) https://www.nj.gov/grc/laws/act/Readable%20Version%20of%20OPRA%20(Sept%202024)(P.L.%202024,%20c.16).pdf
  - Citation: Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC Readable Version), N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined, p. 16 (`opra_statute:0054`) https://www.nj.gov/grc/laws/act/Readable%20Version%20of%20OPRA%20(Sept%202024)(P.L.%202024,%20c.16).pdf
  - Open question: Exception language in N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined [opra_statute:0053]: "...21, c.371 (C.47:1B-1), any immediate family member thereof; except for: ▪ use by any government agency, including any court or law enforcement agency, in carrying out its functions, or any private person or ent..." Confirm whether any listed exception applies.
  - Open question: Exception language in N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined [opra_statute:0054]: "...uthority of any statute or executive order of the Governor; Except where an agency can demonstrate an emergent need, a regulation that limits access to government records shall not be retroactive in effect..." Confirm whether any listed exception applies.
  - Open question: Is this a personal number of a private individual, not an agency or business line?

## Escalations

- none

## Human review

Completion at pause: **AWAITING_HUMAN_REVIEW**; disclosure authorized: False.

Approval-control demonstration: the paused workflow was resumed with a **scripted reviewer input** (`approve` by `demo-script (simulated reviewer input)`, simulated for this demo, not a real reviewer).
Result: completion **APPROVED_BY_HUMAN**, human review status APPROVED, disclosure authorized True.

## Audit log

| Step | Node | Agent | Event |
|---|---|---|---|
| 1 | orchestrator_start | orchestrator | plan |
| 2 | record_analysis | record_analysis | detect_pii OK |
| 3 | policy_research | policy_research | retrieved policy evidence |
| 4 | evidence_merge | orchestrator | merged findings with policy evidence |
| 5 | compliance_qa | compliance_qa | validated proposal and assessed evidence |
| 6 | evidence_decision | orchestrator | route=standard |
| 7 | finalize_recommendations | orchestrator | assembled recommendations |
