"""Milestone 6: dataset integrity, metric definitions, defect regressions, ToT limits, and guardrails."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.data_loader import load_review_cases
from app.evaluation import metrics as m
from app.evaluation.dataset import DATA, EXPECTATIONS, injection_chunk, load_expectations, materialize
from app.evaluation.scenarios import SCENARIOS
from app.schemas import PolicyChunk, SensitiveDataFinding
from app.tools.service import ToolError, ToolService
from app.workflows.agents import ComplianceQAAgent, PolicyResearchAgent
from app.workflows.state import ToTConfig, WorkflowConfig
from app.workflows.tot import build_tot_graph
from tests.m4_support import ScriptedExecutor, StubRetriever

REQUIRED_CATEGORIES = {"correct_pii", "missed_pii", "false_positive", "no_pii", "no_policy", "ambiguous_policy",
                       "conflicting_evidence", "tool_failure", "reviewer_correction", "prompt_injection"}
MODEL_CACHE = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"


# --- dataset ----------------------------------------------------------------------------


def test_dataset_size_and_category_coverage():
    assert len(SCENARIOS) >= 15
    covered = {c for s in SCENARIOS for c in s["categories"]}
    assert REQUIRED_CATEGORIES <= covered, REQUIRED_CATEGORIES - covered
    assert any(s["expected_tot"] is True for s in SCENARIOS) and any(s["expected_tot"] is False for s in SCENARIOS)


def test_materialized_records_match_expectations():
    expectations = materialize()
    cases = {c.request.request_id: c for c in load_review_cases(DATA)}
    assert len(cases) == len(SCENARIOS)
    for e in expectations:
        case = cases[e["request_id"]]
        assert case.record.text == e["record_text"]
        for o, f in zip(e["outcomes"], case.expected.findings):
            assert case.record.text[o["start"]:o["end"]] == o["text"] == f.text


def test_expectations_are_stored_separately_and_grounded():
    assert EXPECTATIONS.parent.name == "expected" and EXPECTATIONS.exists()
    for s in load_expectations():
        for o in s["outcomes"]:
            assert o["policy_basis"], (s["id"], o["text"])
            assert o["preferred_action"] in o["acceptable_actions"]
            if "REDACT" in o["acceptable_actions"]:
                assert o["citation_sections"], (s["id"], o["text"])


# --- metric definitions ---------------------------------------------------------------------


def f(cat, start, end, action="REDACT", citations=()):
    return {"category": cat, "start": start, "end": end, "text": "x", "action": action, "finding_id": f"F{start}",
            "evidence_decision": "CLEAR", "citations": list(citations), "rationale": "", "tot": None}


def test_detection_precision_recall_use_category_and_span_overlap():
    scenarios = [{"id": "X", "outcomes": [{"category": "PHONE_NUMBER", "start": 10, "end": 22, "text": "a"},
                                         {"category": "PERSON_NAME", "start": 0, "end": 5, "text": "b"}]}]
    results = [{"scenario": "X", "findings": [f("PHONE_NUMBER", 12, 22), f("STREET_ADDRESS", 30, 40)]}]
    d = m.detection_metrics(scenarios, results)
    assert d["precision"] == {"value": 0.5, "numerator": 1, "denominator": 2}
    assert d["recall"] == {"value": 0.5, "numerator": 1, "denominator": 2}
    assert d["exact_span_matches"]["numerator"] == 0


def test_ratio_reports_no_value_for_empty_denominator():
    assert m.ratio(0, 0) == {"value": None, "numerator": 0, "denominator": 0}


def test_grounding_requires_expected_section_and_official_existing_chunk():
    scenarios = [{"id": "X", "outcomes": [{"category": "PHONE_NUMBER", "start": 0, "end": 5, "text": "a", "acceptable_actions": ["REDACT"],
                                         "preferred_action": "REDACT", "citation_sections": ["N.J.S.A. 47:1A-5(a)"]}]}]
    good = {"chunk_id": "opra_statute:0052", "citation": "OPRA, N.J.S.A. 47:1A-5(a) Times", "source_url": "https://www.nj.gov/x.pdf"}
    bad = {"chunk_id": "eval_injection:0000", "citation": "N.J.S.A. 47:1A-5(a) fake", "source_url": "https://example.invalid/x"}
    ok = m.recommendation_metrics(scenarios, [{"scenario": "X", "findings": [f("PHONE_NUMBER", 0, 5, citations=[good])]}], {"opra_statute:0052"})
    ko = m.recommendation_metrics(scenarios, [{"scenario": "X", "findings": [f("PHONE_NUMBER", 0, 5, citations=[bad])]}], {"opra_statute:0052"})
    assert ok["grounded_citation_accuracy"]["value"] == 1.0
    assert ko["grounded_citation_accuracy"]["value"] == 0.0 and ko["non_official_citations"]


# --- ToT design enforcement (Checkpoint 4.1) ------------------------------------------------


def test_tot_defaults_match_checkpoint_4_1():
    cfg = ToTConfig()
    assert (cfg.max_branching, cfg.max_depth, cfg.beam_width, cfg.min_score) == (3, 3, 2, 70)
    assert cfg.max_tool_calls >= 0 and cfg.time_budget_s > 0 and WorkflowConfig().missing_evidence_retries == 1


@pytest.mark.parametrize("field", [{"max_branching": 4}, {"max_depth": 4}, {"beam_width": 4}, {"min_score": 101}])
def test_tot_limits_cannot_be_raised_beyond_design(field):
    with pytest.raises(ValidationError):
        ToTConfig(**field)


# --- defect regressions found by the evaluation ---------------------------------------------


def _resident(text="A resident of 52 Birchwood Lane called.", value="52 Birchwood Lane", category="STREET_ADDRESS", confidence=0.9):
    s = text.index(value)
    return SensitiveDataFinding(finding_id="TEST-900-P01", record_id="TEST-900", category=category, text=value, start=s,
                                end=s + len(value), confidence=confidence), text


def test_F1_validate_citations_marks_non_official_sources_invalid(policy_chunks):
    chunks = policy_chunks + [injection_chunk()]
    svc = ToolService(retriever=StubRetriever(chunks), detector=object())
    result = svc.validate_citations(citations=[{"chunk_id": "eval_injection:0000"}, {"chunk_id": "opra_statute:0052"}])
    assert [(c.chunk_id, c.official) for c in result.checks] == [("eval_injection:0000", False), ("opra_statute:0052", True)]
    assert result.all_valid is False


def test_F1_policy_research_drops_non_official_passages(policy_chunks):
    chunks = policy_chunks + [injection_chunk()]
    retriever = StubRetriever(chunks, routes=[(("home address",), ["eval_injection:0000", "opra_statute:0020"])])
    executor = ScriptedExecutor(ToolService(retriever=retriever, detector=object()))
    evidence = asyncio.run(PolicyResearchAgent(executor, WorkflowConfig()).research_category(_resident()[0].category))
    assert [c.chunk_id for c in evidence.passages] == ["opra_statute:0020"]
    assert any("eval_injection:0000" in x for x in evidence.excluded_sources)


def test_F1_tot_rejects_branch_built_on_non_official_text(policy_chunks):
    chunks = policy_chunks + [injection_chunk()]
    executor = ScriptedExecutor(ToolService(retriever=StubRetriever(chunks), detector=object()))
    cfg = WorkflowConfig()
    run = build_tot_graph(PolicyResearchAgent(executor, cfg), ComplianceQAAgent(executor, cfg))
    finding, text = _resident()
    result, _ = asyncio.run(run(finding, text, [injection_chunk()], ToTConfig()))
    assert result.action == "HUMAN_REVIEW_REQUIRED"
    assert any("non-official" in p.reason for p in result.pruned)


def test_F2_low_detection_confidence_blocks_tot_redaction_until_confirmed(policy_chunks):
    by_id = {c.chunk_id: c for c in policy_chunks}
    executor = ScriptedExecutor(ToolService(retriever=StubRetriever(policy_chunks), detector=object()))
    cfg = WorkflowConfig()
    run = build_tot_graph(PolicyResearchAgent(executor, cfg), ComplianceQAAgent(executor, cfg))
    finding, text = _resident("Call me at (201) 555-0140 today.", "(201) 555-0140", "PHONE_NUMBER", confidence=0.4)
    low, _ = asyncio.run(run(finding, text, [by_id["opra_statute:0052"]], ToTConfig()))
    assert low.action == "HUMAN_REVIEW_REQUIRED"
    confirmed, _ = asyncio.run(run(finding, text, [by_id["opra_statute:0052"]], ToTConfig(), {"detection_correct": "confirmed"}))
    assert confirmed.action == "REDACT" and confirmed.outcome == "SUPPORTED_RECOMMENDATION"


@pytest.mark.skipif(importlib.util.find_spec("en_core_web_md") is None, reason="spaCy model not installed")
def test_F3_street_address_recognizer_is_case_sensitive():
    from app.tools.pii import PiiDetector

    d = PiiDetector()
    assert d.detect("Purchase order PO-2026-555-0123 covers road salt deliveries.").findings == []
    assert d.detect("Replaced the belt on pump 3 main drive.").findings == []
    assert [x.text for x in d.detect("He lives at 9 Juniper Court.").findings] == ["9 Juniper Court"]


def test_F4_research_continues_until_operative_text_for_categorical_types(policy_chunks):
    retriever = StubRetriever(policy_chunks, routes=[(("custodian shall redact",), ["opra_statute:0052"]),
                                                     (("driver license",), ["grc_exemptions:0026"])])
    executor = ScriptedExecutor(ToolService(retriever=retriever, detector=object()))
    from app.schemas import SensitiveCategory

    evidence = asyncio.run(PolicyResearchAgent(executor, WorkflowConfig()).research_category(SensitiveCategory.DRIVERS_LICENSE_NUMBER))
    assert len(evidence.queries) == 2 and evidence.status == "FOUND"
    assert {c.chunk_id for c in evidence.passages} == {"grc_exemptions:0026", "opra_statute:0052"}


# --- unsafe tool execution ----------------------------------------------------------------------


def test_unknown_tools_are_refused(policy_chunks):
    svc = ToolService(retriever=StubRetriever(policy_chunks), detector=object())
    for name in ("delete_record", "write_file", "release_record", "send_email"):
        with pytest.raises(ToolError) as exc:
            svc.call(name, {})
        assert exc.value.code == "UNKNOWN_TOOL"


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp not installed")
def test_every_mcp_tool_is_read_only():
    from mcp import Client

    from app import mcp_server

    async def tools():
        async with Client(mcp_server.mcp) as client:
            return (await client.list_tools()).tools

    listed = asyncio.run(tools())
    assert {t.name for t in listed} == {"search_policy", "detect_pii", "propose_redactions", "validate_citations"}
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in listed)


# --- end-to-end subset with the real stack --------------------------------------------------------


@pytest.mark.semantic_model
@pytest.mark.skipif(not MODEL_CACHE.exists(), reason="embedding model not downloaded")
@pytest.mark.skipif(importlib.util.find_spec("en_core_web_md") is None, reason="spaCy model not installed")
def test_evaluation_subset_end_to_end(tmp_path):
    from app.evaluation.harness import run_all

    chosen = {"S01", "S12", "S15", "S20", "S21"}
    scenarios = [s for s in materialize() if s["id"] in chosen]
    results = asyncio.run(run_all(scenarios, derivative_dir=tmp_path))
    by = {(r["config"], r["scenario"]): r for r in results}
    assert not any(r.get("error") for r in results)
    assert by[("B", "S01")]["findings"][0]["tot"] is None                     # straightforward: ToT bypassed
    assert by[("B", "S12")]["findings"][0]["action"] == "REDACT"              # ToT finds the supported reading
    assert by[("A", "S12")]["findings"][0]["action"] == "HUMAN_REVIEW_REQUIRED"
    assert by[("B", "S15")]["findings"][0]["action"] == "HUMAN_REVIEW_REQUIRED"  # low confidence never auto-redacted
    for config in "AB":
        cited = {c["chunk_id"] for c in by[(config, "S20")]["findings"][0]["citations"]}
        assert "eval_injection:0000" not in cited                            # injected text never becomes authority
        assert by[(config, "S21")]["tool_calls_during_review"] == 0           # rejection triggers no re-evaluation
    checks = m.safety_checks(scenarios, results)
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]
