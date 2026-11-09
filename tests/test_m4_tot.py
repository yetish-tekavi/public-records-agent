"""Evidence classification and the bounded Tree of Thought (generation, scoring, pruning, limits)."""

from __future__ import annotations

import asyncio

import pytest

from app.schemas import PolicyChunk, SensitiveCategory, SensitiveDataFinding
from app.tools.service import ToolService
from app.workflows import evidence as ev
from app.workflows.agents import ComplianceQAAgent, PolicyResearchAgent
from app.workflows.state import BranchScore, EvidenceDecision, ThoughtNode, ToTConfig, WorkflowConfig
from app.workflows.tot import build_tot_graph, choose_best, generate_interpretations
from tests.m4_support import ScriptedExecutor, StubRetriever

COVERED_TEXT = "A caller who identified himself as a retired Superior Court judge reported a fallen limb outside his home at 9 Juniper Court."
RESIDENT_TEXT = "The caller, a resident of 31 Larchmont Terrace, asked that her complaint remain anonymous."
AGENCY_TEXT = "For permits, call the Larkspur Township main line at (201) 555-0100 during business hours."


def finding(text: str, value: str, category: str, confidence: float = 0.9) -> SensitiveDataFinding:
    s = text.index(value)
    return SensitiveDataFinding(finding_id="TEST-900-P01", record_id="TEST-900", category=category, text=value,
                                start=s, end=s + len(value), confidence=confidence)


@pytest.fixture(scope="module")
def chunks(policy_chunks):
    return {c.chunk_id: c for c in policy_chunks}


# --- evidence analysis on real provisions ------------------------------------------------


def test_phone_clause_is_general_although_followed_by_restricted_clause(chunks):
    a = ev.analyse_passage(chunks["opra_statute:0052"], SensitiveCategory.PHONE_NUMBER)
    assert a.operative and not a.restricted_class


def test_home_address_clause_is_restricted_and_definition_is_not_operative(chunks):
    assert ev.analyse_passage(chunks["opra_statute:0052"], SensitiveCategory.STREET_ADDRESS).restricted_class
    definition = ev.analyse_passage(chunks["opra_statute:0034"], SensitiveCategory.STREET_ADDRESS)
    assert definition.definition and not definition.operative


@pytest.mark.parametrize("text, value, category, ids, expected", [
    ("My personal phone number is 609-555-0142.", "609-555-0142", "PHONE_NUMBER", ["opra_statute:0052"], EvidenceDecision.CLEAR),
    (COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS", ["opra_statute:0020", "grc_exemptions:0025"], EvidenceDecision.AMBIGUOUS),
    (AGENCY_TEXT, "(201) 555-0100", "PHONE_NUMBER", ["opra_statute:0052", "opra_statute:0034"], EvidenceDecision.CONFLICTING),
    ("Logged from 203.0.113.45.", "203.0.113.45", "IP_ADDRESS", ["opra_statute:0052"], EvidenceDecision.MISSING),
])
def test_assess_finding(chunks, text, value, category, ids, expected):
    assessment = ev.assess_finding(finding(text, value, category), [chunks[i] for i in ids], text)
    assert assessment.decision is expected, assessment.reasons


def test_low_confidence_categorical_finding_is_ambiguous(chunks):
    text = "My personal phone number is 609-555-0142."
    a = ev.assess_finding(finding(text, "609-555-0142", "PHONE_NUMBER", confidence=0.45), [chunks["opra_statute:0052"]], text)
    assert a.decision is EvidenceDecision.AMBIGUOUS


def test_possibly_missed_sweep_flags_undetected_values():
    flags = ev.possibly_missed("Call 609-555-0177 or 609-555-0142.", [finding("Call 609-555-0177 or 609-555-0142.", "609-555-0142", "PHONE_NUMBER")])
    assert len(flags) == 1 and "609-555-0177" in flags[0]


# --- generation ----------------------------------------------------------------------


def test_generation_respects_branching_and_keeps_review_candidate(chunks):
    f = finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS")
    evidence = [chunks[i] for i in ("opra_statute:0052", "opra_statute:0034", "opra_statute:0020", "grc_exemptions:0025")]
    nodes = generate_interpretations(f, evidence, ToTConfig(max_branching=2), COVERED_TEXT)
    assert len(nodes) == 2
    assert nodes[-1].action == "HUMAN_REVIEW_REQUIRED"
    assert all(set(n.cited_chunk_ids) <= {c.chunk_id for c in evidence} for n in nodes)  # cites retrieved text only


# --- ToT runs with the real corpus behind stub tools --------------------------------------


def make_tot(policy_chunks, data_dir=None, **executor_kwargs):
    service = ToolService(retriever=StubRetriever(policy_chunks), detector=object(), **({"data_dir": data_dir} if data_dir else {}))
    executor = ScriptedExecutor(service, **executor_kwargs)
    cfg = WorkflowConfig()
    return build_tot_graph(PolicyResearchAgent(executor, cfg), ComplianceQAAgent(executor, cfg)), executor


def run(run_tot, f, text, evidence, config=None):
    return asyncio.run(run_tot(f, text, evidence, config or ToTConfig()))


def test_supported_branch_is_selected_after_verification_and_targeted_retrieval(policy_chunks, chunks):
    run_tot, executor = make_tot(policy_chunks)
    result, calls = run(run_tot, finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS"), COVERED_TEXT,
                        [chunks["opra_statute:0020"], chunks["grc_exemptions:0025"]])
    assert result.outcome == "SUPPORTED_RECOMMENDATION" and result.action == "REDACT"
    assert result.selected_score >= 70
    selected = next(n for n in result.nodes if n.node_id == result.selected_node_id)
    assert "opra_statute:0052" in selected.cited_chunk_ids  # added by the one targeted retrieval
    assert all(a.status == "SUPPORTED" for a in selected.assumptions)
    assert result.targeted_retrieval_used and executor.calls.count("search_policy") == 1
    parent = next(n for n in result.nodes if n.node_id == selected.parent_id)
    assert "opra_statute:0052" not in parent.cited_chunk_ids  # branch-local evidence: parent unchanged


def test_unsupported_factual_assumption_is_rejected_and_case_escalated(policy_chunks, chunks):
    run_tot, executor = make_tot(policy_chunks)
    result, _ = run(run_tot, finding(RESIDENT_TEXT, "31 Larchmont Terrace", "STREET_ADDRESS"), RESIDENT_TEXT,
                    [chunks["opra_statute:0020"], chunks["grc_exemptions:0025"]])
    assert result.outcome == "ESCALATED" and result.action == "HUMAN_REVIEW_REQUIRED"
    assert any("unsupported factual assumption" in p.reason for p in result.pruned)
    assert "search_policy" not in executor.calls  # no retrieval spent on a rejected branch


def test_conflicting_exclusion_blocks_redaction(policy_chunks, chunks):
    run_tot, _ = make_tot(policy_chunks)
    result, _ = run(run_tot, finding(AGENCY_TEXT, "(201) 555-0100", "PHONE_NUMBER"), AGENCY_TEXT,
                    [chunks["opra_statute:0052"], chunks["opra_statute:0034"]])
    assert result.action == "HUMAN_REVIEW_REQUIRED"
    assert any("not a public agency" in p.reason for p in result.pruned)


def test_fabricated_citation_is_rejected(policy_chunks, chunks):
    run_tot, _ = make_tot(policy_chunks)
    forged = chunks["opra_statute:0052"].model_copy(update={"chunk_id": "opra_statute:9999"})
    result, _ = run(run_tot, finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS"), COVERED_TEXT, [forged])
    assert any("fabricated" in p.reason for p in result.pruned)
    assert result.outcome == "ESCALATED"


def test_altered_excerpt_is_rejected(policy_chunks, chunks):
    run_tot, _ = make_tot(policy_chunks)
    altered = chunks["opra_statute:0052"].model_copy(update={"text": chunks["opra_statute:0052"].text.replace("shall redact", "may redact")})
    result, _ = run(run_tot, finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS"), COVERED_TEXT, [altered])
    assert any("fabricated or altered" in p.reason for p in result.pruned)


def test_beam_width_limits_retained_candidates(policy_chunks, chunks):
    run_tot, _ = make_tot(policy_chunks)
    evidence = [chunks[i] for i in ("opra_statute:0052", "opra_statute:0034", "grc_exemptions:0025")]
    result, _ = run(run_tot, finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS"), COVERED_TEXT, evidence, ToTConfig(beam_width=1))
    assert any("beam width 1" in p.reason for p in result.pruned)
    for depth in range(2, result.depth_reached + 1):
        assert len([n for n in result.nodes if n.depth == depth]) <= 1


def test_depth_limit_stops_search_and_escalates(policy_chunks, chunks):
    run_tot, executor = make_tot(policy_chunks)
    result, _ = run(run_tot, finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS"), COVERED_TEXT,
                    [chunks["opra_statute:0020"]], ToTConfig(max_depth=1))
    assert result.depth_reached == 1
    assert any("max depth 1" in e for e in result.budget_events)
    assert result.action == "HUMAN_REVIEW_REQUIRED"  # unverified redaction branch cannot be selected
    assert "search_policy" not in executor.calls


def test_tool_budget_exhaustion_prevents_selection(policy_chunks, chunks):
    run_tot, executor = make_tot(policy_chunks)
    result, _ = run(run_tot, finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS"), COVERED_TEXT,
                    [chunks["opra_statute:0020"]], ToTConfig(max_tool_calls=0))
    assert result.tool_calls_used == 0 and executor.calls == []
    assert any("budget exhausted" in e for e in result.budget_events)
    assert result.outcome == "ESCALATED" and result.selected_node_id is None


def test_targeted_retrieval_disabled_leaves_gap_unresolved(policy_chunks, chunks):
    run_tot, _ = make_tot(policy_chunks)
    result, _ = run(run_tot, finding(COVERED_TEXT, "9 Juniper Court", "STREET_ADDRESS"), COVERED_TEXT,
                    [chunks["opra_statute:0020"]], ToTConfig(allow_targeted_retrieval=False))
    assert not result.targeted_retrieval_used
    assert result.action == "HUMAN_REVIEW_REQUIRED"
    assert any("No retrieved operative provision" in q for q in result.unresolved)


# --- tie resolution -------------------------------------------------------------------------


def node(node_id, action, score, doc_id):
    chunk = PolicyChunk(chunk_id=f"{doc_id}:0001", doc_id=doc_id, title="t", source_url="https://example.invalid", text="x")
    return ThoughtNode(node_id=node_id, parent_id=None, depth=1, stage="INTERPRETATION", action=action, provision_type="X",
                       interpretation="i", evidence=[chunk], cited_chunk_ids=[chunk.chunk_id],
                       score=BranchScore(policy_relevance=30, citation_validity=30, consistency=25, explanation=15, total=score))


def test_higher_score_wins():
    best, conflict = choose_best([node("a", "HUMAN_REVIEW_REQUIRED", 85, "opra_statute"), node("b", "REDACT", 100, "grc_handbook")])
    assert best.node_id == "b" and conflict is None


def test_tie_goes_to_stronger_primary_source():
    best, conflict = choose_best([node("a", "REDACT", 90, "grc_handbook"), node("b", "HUMAN_REVIEW_REQUIRED", 90, "opra_statute")])
    assert best.node_id == "b" and conflict is None


def test_unresolved_material_conflict_escalates():
    best, conflict = choose_best([node("a", "REDACT", 90, "opra_statute"), node("b", "HUMAN_REVIEW_REQUIRED", 90, "opra_statute")])
    assert best is None and "material conflict" in conflict
