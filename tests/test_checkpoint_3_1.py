"""Checkpoint 3.1: real NJ source ingestion, semantic retrieval of N.J.S.A. 47:1A-5(a), and the agent.

Uses the genuine downloaded GRC document in data/policy_documents (no network in tests).
Tests marked semantic_model need the all-MiniLM-L6-v2 embedding model.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

from app.agents.executors import LocalToolExecutor
from app.agents.models import AgentStatus
from app.agents.planners import POLICY_QUERIES, RuleBasedPlanner
from app.agents.react_agent import ReActAgent
from app.data_loader import PROJECT_ROOT, load_review_cases
from app.policies.chunker import StatuteDetector
from app.policies.extract import DEFAULT_POLICY_DIR, load_manifest
from app.policies.ingest import section_passage
from app.schemas import PolicyChunk, PublicRecord, RedactionAction, RetrievedPassage, SensitiveCategory, SensitiveDataFinding
from app.tools.redaction import propose_redactions
from app.tools.service import ToolService

CHECKPOINT_DIR = PROJECT_ROOT / "data" / "checkpoint_3_1"
TA_QUERY = "What New Jersey law applies when a public record contains someone's personal telephone number?"
MODEL_CACHE = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"


def norm(text: str) -> str:
    return " ".join(text.split())


# --- subsection labelling (synthetic) ---------------------------------------------


def test_statute_subsections_follow_letter_sequence_and_allow_margin_notes():
    d = StatuteDetector()
    assert d("C.47:1A-5 Times during which records may be inspected,", "") == "N.J.S.A. 47:1A-5 Times during which records may be inspected"
    assert d("a. The custodian of a government record shall permit", "") == "N.J.S.A. 47:1A-5(a) Times during which records may be inspected"
    assert d("a. Some list item restarting at a", "") is None  # not the next letter
    assert d("Fees for copies b. (1) A copy or copies of a government record", "") == "N.J.S.A. 47:1A-5(b) Times during which records may be inspected"
    assert d("d. skipping a letter", "") is None


# --- the genuine downloaded document ------------------------------------------------


def test_local_copy_is_the_documented_official_file():
    doc = next(d for d in load_manifest() if d.doc_id == "opra_statute")
    assert doc.source_url.startswith("https://www.nj.gov/grc/laws/act/")
    assert (DEFAULT_POLICY_DIR / doc.file).exists()


def test_ingestion_extracts_47_1a_5a_verbatim_with_version():
    doc = next(d for d in load_manifest() if d.doc_id == "opra_statute")
    passage, pages, version, page_count = section_passage(DEFAULT_POLICY_DIR / doc.file, doc, "47:1A-5(a)", "Prior to allowing access")
    assert norm(passage).startswith("Prior to allowing access to any government record, the custodian thereof shall redact")
    assert "personal telephone number" in norm(passage)
    assert "except for:" in norm(passage)
    assert pages == [15, 16] and page_count == 37
    assert version == "September 3, 2024"


def test_47_1a_5a_chunk_is_labelled_and_located(policy_chunks):
    [chunk] = [c for c in policy_chunks if (c.section or "").startswith("N.J.S.A. 47:1A-5(a)") and "personal telephone number" in norm(c.text)]
    assert chunk.page_start == 15
    assert chunk.source_url.startswith("https://www.nj.gov/grc/laws/act/")
    assert chunk.effective_date.startswith("as of September 3, 2024")


# --- exception handling (synthetic) ---------------------------------------------------


def test_exceptions_in_the_following_subsection_chunk_are_surfaced():
    text = "My number is 555-0199."
    record = PublicRecord(record_id="TEST-900", record_type="call_transcript", title="t", record_date="2026-01-01", text=text, synthetic=True)
    finding = SensitiveDataFinding(finding_id="F1", record_id="TEST-900", category="PHONE_NUMBER", text="555-0199", start=13, end=21)

    def p(cid, body):
        return RetrievedPassage(chunk=PolicyChunk(chunk_id=cid, doc_id="opra_statute", title="Statute", section="N.J.S.A. 47:1A-5(a) X",
                                                  source_url="https://example.invalid", page_start=1, page_end=1, text=body), rank=1)

    unrelated = p("opra_statute:0009", "except that the full name of an official is public")
    support = p("opra_statute:0010", "the custodian shall redact the personal telephone number of any person")
    follow = p("opra_statute:0011", "member thereof; except for: use by any government agency")
    [rec] = propose_redactions(record, [finding], [support], section_context=[unrelated, follow]).recommendations
    assert rec.action is RedactionAction.REDACT
    assert any("[opra_statute:0011]" in q and "except for" in q for q in rec.open_questions)
    assert not any("opra_statute:0009" in q for q in rec.open_questions)  # precedes the provision
    assert [r.chunk_id for r in rec.policy_references] == ["opra_statute:0010", "opra_statute:0011"]


# --- real semantic retrieval and agent ---------------------------------------------


@pytest.fixture(scope="module")
def full_service():
    return ToolService(data_dir=CHECKPOINT_DIR)


@pytest.mark.semantic_model
@pytest.mark.skipif(not MODEL_CACHE.exists(), reason="embedding model not downloaded")
def test_semantic_top4_contains_47_1a_5a(full_service):
    hits = full_service.retriever.semantic.search(TA_QUERY, k=4)
    assert len(hits) == 4
    sections = [h.chunk.section or "" for h in hits]
    assert any(s.startswith("N.J.S.A. 47:1A-5(a)") for s in sections), sections
    assert any("personal telephone number" in norm(h.chunk.text) for h in hits)


def test_rule_planner_asks_a_natural_language_question():
    assert POLICY_QUERIES[SensitiveCategory.PHONE_NUMBER][0] == (
        "What New Jersey law applies when a public record contains a person's personal telephone number?"
    )


def _run_agent(service):
    [case] = load_review_cases(CHECKPOINT_DIR)
    return asyncio.run(ReActAgent(RuleBasedPlanner(), LocalToolExecutor(service)).run(case.request, case.record))


@pytest.mark.semantic_model
@pytest.mark.skipif(not MODEL_CACHE.exists(), reason="embedding model not downloaded")
@pytest.mark.skipif(importlib.util.find_spec("en_core_web_md") is None, reason="spaCy model not installed")
def test_agent_grounds_redaction_in_47_1a_5a(full_service):
    report = _run_agent(full_service)
    assert [s.tool or s.kind for s in report.steps] == ["detect_pii", "search_policy", "propose_redactions", "finish"]
    assert [(f.category, f.text) for f in report.findings] == [(SensitiveCategory.PHONE_NUMBER, "609-555-0142")]
    [rec] = report.recommendations
    assert rec.action is RedactionAction.REDACT
    assert rec.policy_references[0].citation.endswith("N.J.S.A. 47:1A-5(a) Times during which records may be inspected, examined")
    assert rec.policy_references[0].source_url.startswith("https://www.nj.gov/grc/laws/act/")
    assert "personal telephone number" in rec.rationale
    assert any("except for" in q for q in rec.open_questions)
    assert rec.requires_human_approval is True and report.requires_human_approval is True


@pytest.mark.semantic_model
@pytest.mark.skipif(not MODEL_CACHE.exists(), reason="embedding model not downloaded")
@pytest.mark.skipif(importlib.util.find_spec("en_core_web_md") is None, reason="spaCy model not installed")
def test_agent_without_relevant_policy_does_not_invent_one():
    report = _run_agent(ToolService(data_dir=CHECKPOINT_DIR, policy_doc_ids=["ag_bwc_directive"]))
    searches = [s for s in report.steps if s.tool == "search_policy"]
    assert len(searches) == 2 and all(s.observation.startswith("INSUFFICIENT") for s in searches)
    [rec] = report.recommendations
    assert rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED
    assert rec.policy_references == []
    assert "no policy basis" in rec.rationale
    produced_text = rec.rationale + " ".join(rec.open_questions) + report.final_summary + " ".join(report.escalations)
    assert not re.search(r"N\.J\.S\.A|47:1A|\bstatute\b|§", produced_text)
    assert report.status is AgentStatus.ESCALATED
