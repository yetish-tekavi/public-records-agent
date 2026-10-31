# public-records-agent
CMU capstone project - agentic AI assistant that examines a synthetic public-records request, searches publicly available NJ policies, analyzes synthetic records, identifies potentially sensitive information, recommends redactions, invokes appropriate tools and sends the results for human review

> **Prototype only.** The assistant recommends; a human reviewer decides. It never makes
> final legal disclosure decisions.

## Status

**Milestone 1: project setup, data models, and synthetic dataset** (complete)

| Stage | Description | Status |
|---|---|---|
| 1 | Python project, schemas, synthetic records | Done |
| 2 | Deterministic policy retrieval and PII detection tools | Not started |
| 3 | Single ReAct agent | Not started |
| 4 | MCP tool server | Not started |
| 5 | Orchestrator with policy, record-analysis, and QA workers | Not started |
| 6 | Human feedback and redaction workflow | Not started |
| 7 | Safety, evaluation, observability, final demo | Not started |
| 8 | Optional synthetic video | Not started |

No LLM is used yet.

## Data safety

- Every request and record in `data/` is **fictional**. Names, addresses, and events are
  invented, and phone numbers use the reserved fictional range `555-0100` to `555-0199`.
- Every request and record file must carry a `Synthetic: yes` header. The loader refuses
  any file without it.
- `data/policy_documents/` holds public New Jersey OPRA/GRC and Attorney General documents
  downloaded from nj.gov.
- Do not add real police records, agency records, or personal data to this repository.

## Setup

Requires Python 3.11 or newer (developed on 3.14).

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python -m app.main             # summarise all synthetic review cases
python -m app.main --verbose   # include debug logging for each file loaded
```

Expected output (abridged):

```
Loaded 3 synthetic review case(s) from ...\data

OPRA-TEST-001 -> TEST-001 (interaction_transcript)
  Requires redaction: yes
  Expected findings:  2 [PHONE_NUMBER, DRIVERS_LICENSE_NUMBER]
...
```

The command exits with code `1` and logs the offending file if any data file is malformed.

## Test

```bash
python -m pytest
```

## Repository structure

```
app/
  schemas.py          Pydantic models: request, record, finding, policy reference,
                      redaction recommendation, expected result
  data_loader.py      Loads and cross-validates requests, records, and ground truth
  main.py             Command-line entry point
  agents/ policies/ tools/ workflows/   (reserved for later stages)
data/
  synthetic_requests/ request_NNN.txt          OPRA-style requests
  synthetic_records/  record_NNN.txt           Matching synthetic records
  expected_results/   record_NNN.expected.json Ground-truth findings
  policy_documents/   Public NJ policy PDFs
docs/                 Architecture and scope notes
outputs/              Generated derivatives (git-ignored; originals are never modified)
tests/                pytest suite (synthetic fixtures only)
```

## Data format

Requests and records are plain text: a `Key: Value` header block, a blank line, then the body.

```
Record ID: TEST-001
Record Type: interaction_transcript
Title: Synthetic Police Interaction Record
Record Date: 2026-09-14
Synthetic: yes

Officer: Can you confirm your contact details?
...
```

Ground-truth findings give character offsets into the record **body**, the text after the
header. The loader checks that `body[start:end]` equals each finding's `text`.

## Test cases

| Request | Record | Scenario | Expected findings |
|---|---|---|---|
| OPRA-TEST-001 | Police interaction transcript | Phone number and synthetic driver's license number | 2 |
| OPRA-TEST-002 | Non-emergency dispatch call | Name, street address, phone number; caller asks for anonymity | 3 |
| OPRA-TEST-003 | Public Works inspection log | No obvious PII; internal asset and work-order IDs as decoys | 0 |
