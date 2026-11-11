"""Materialise the Milestone 6 scenarios into the synthetic dataset layout plus a separate expectations file.

    data/milestone_6/synthetic_records/record_6NN.txt     (agent input)
    data/milestone_6/synthetic_requests/request_6NN.txt   (agent input)
    data/milestone_6/expected_results/record_6NN.expected.json  (ground-truth findings)
    data/milestone_6/expected/scenarios.json              (expected outcomes; never read by the agents)
"""

from __future__ import annotations

import json
from pathlib import Path

from app.data_loader import PROJECT_ROOT
from app.evaluation.scenarios import INJECTION_TEXT, SCENARIOS
from app.schemas import PolicyChunk

DATA = PROJECT_ROOT / "data" / "milestone_6"
EXPECTATIONS = DATA / "expected" / "scenarios.json"


def record_id(scenario: dict) -> str:
    return f"TEST-6{scenario['id'][1:]}"


def injection_chunk() -> PolicyChunk:
    return PolicyChunk(
        chunk_id="eval_injection:0000", doc_id="eval_injection",
        title="SYNTHETIC TEST FIXTURE: prompt-injection document (not official policy)",
        source_url="https://example.invalid/prompt-injection-fixture", effective_date=None,
        section="Home address handling notice", page_start=1, page_end=1, text=INJECTION_TEXT,
    )


def materialize(out: Path = DATA) -> list[dict]:
    for sub in ("synthetic_records", "synthetic_requests", "expected_results", "expected"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    expectations = []
    for s in SCENARIOS:
        rid, num = record_id(s), record_id(s)[-3:]
        text = s["text"]
        (out / "synthetic_records" / f"record_{num}.txt").write_text(
            f"Record ID: {rid}\nRecord Type: call_transcript\nTitle: {s['id']} - {s['title']}\nRecord Date: 2026-11-10\n"
            f"Synthetic: yes\n\n{text}\n", encoding="utf-8", newline="\n")
        (out / "synthetic_requests" / f"request_{num}.txt").write_text(
            f"Request ID: OPRA-{rid}\nRecord ID: {rid}\nDate Received: 2026-11-10\nRequester: Evaluation Requester\nSynthetic: yes\n\n"
            f"I request a copy of record {rid}.\n", encoding="utf-8", newline="\n")
        findings = []
        for i, o in enumerate(s["outcomes"], 1):
            start = text.index(o["text"])
            assert text.count(o["text"]) == 1, (s["id"], o["text"])
            o["start"], o["end"] = start, start + len(o["text"])
            findings.append({"finding_id": f"{rid}-F{i:02d}", "record_id": rid, "category": o["category"], "text": o["text"],
                             "start": start, "end": start + len(o["text"]), "detector": "ground_truth", "confidence": 1.0})
        (out / "expected_results" / f"record_{num}.expected.json").write_text(json.dumps(
            {"request_id": f"OPRA-{rid}", "record_id": rid, "requires_redaction": bool(findings), "findings": findings,
             "notes": f"{s['id']}: {s['title']}"}, indent=2) + "\n", encoding="utf-8", newline="\n")
        expectations.append({**{k: v for k, v in s.items() if k != "text"}, "record_id": rid, "request_id": f"OPRA-{rid}",
                             "record_text": text, "faults": s.get("faults", []), "corpus": s.get("corpus", "official")})
    EXPECTATIONS.parent.mkdir(parents=True, exist_ok=True)
    (out / "expected" / "scenarios.json").write_text(json.dumps({"scenarios": expectations}, indent=2) + "\n", encoding="utf-8", newline="\n")
    return expectations


def load_expectations(path: Path = EXPECTATIONS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]


if __name__ == "__main__":
    print(f"{len(materialize())} scenarios written to {DATA.relative_to(PROJECT_ROOT)}")
