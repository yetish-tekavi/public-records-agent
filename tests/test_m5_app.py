"""Streamlit reviewer interface, driven headlessly with streamlit.testing.AppTest."""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from app.data_loader import PROJECT_ROOT  # noqa: E402
from app.review.session import ItemStatus  # noqa: E402
from tests.m5_support import isolated_outputs, make_session  # noqa: E402,F401

APP = str(PROJECT_ROOT / "app" / "review_app.py")


def open_app(session, reviewer="UI Reviewer"):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["session"] = session
    at.session_state["reviewer"] = reviewer
    return at.run()


def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [e.value for e in at.error] + [s.value for s in at.success]
    parts += [str(d.value) for d in at.dataframe]
    return "\n".join(str(p) for p in parts)


def test_standard_recommendation_display(policy_chunks):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    at = open_app(session)
    assert not at.exception
    text = all_text(at)
    assert "Standard tool workflow (rule-based ReAct)" in text
    assert "Policy evidence:** SUPPORTED" in text
    assert "N.J.S.A. 47:1A-5(a)" in text and "https://www.nj.gov/grc/laws/act/" in text
    assert "During the interaction" in text and "<mark" in text  # original record with highlighted finding
    assert any(e.label == "Reasoning and Evidence" for e in at.expander)


def test_tot_recommendation_shows_branches_scores_and_pruning(policy_chunks):
    session, _, _, state = make_session(policy_chunks, "OPRA-TEST-202")
    at = open_app(session)
    assert not at.exception
    text = all_text(at)
    tot = state["tot_results"][0]
    assert "Bounded Tree of Thought" in text
    assert f"**Selected branch:** `{tot.selected_node_id}`" in text
    for node in tot.nodes:  # every actual branch is listed, with its actual score
        assert node.node_id in text
    assert str(tot.nodes[0].score.total) in text
    assert "not probabilities of legal correctness" in text
    assert "Additional retrieval performed:** yes" in text


def test_approve_and_finalize_through_ui(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    at = open_app(session)
    at.button(key="approve-TEST-201-F01").click().run()
    assert at.session_state["session"].item_state("TEST-201-F01").status is ItemStatus.APPROVED
    at.checkbox(key="authorize").check().run()
    at.button(key="finalize").click().run()
    assert at.session_state["session"].finalized
    assert "[REDACTED: PHONE_NUMBER]" in all_text(at) or any("[REDACTED: PHONE_NUMBER]" in c.value for c in at.code)


def test_reject_through_ui_does_not_start_reevaluation(policy_chunks, isolated_outputs):
    session, _, executor, _ = make_session(policy_chunks, "OPRA-TEST-202")
    calls_before = list(executor.calls)
    at = open_app(session)
    at.text_input(key="reject-reason-TEST-202-F01").input("Covered status not shown").run()
    at.button(key="reject-TEST-202-F01").click().run()
    s = at.session_state["session"]
    assert s.item_state("TEST-202-F01").status is ItemStatus.REJECTED
    assert not any(e.action.startswith("REEVALUATION") for e in s.events) and executor.calls == calls_before


def test_missing_evidence_case_blocks_finalization_in_ui(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-203")
    at = open_app(session)
    text = all_text(at)
    assert "none: no policy basis was established" in text
    assert "escalated" in text and "not resolved (status ESCALATED)" in text
    assert at.button(key="approve-TEST-203-F01").disabled
    at.checkbox(key="authorize").check().run()
    at.button(key="finalize").click().run()
    s = at.session_state["session"]
    assert not s.finalized and s.events[-1].action == "FINALIZATION_BLOCKED"
    assert any("Finalization blocked" in e.value for e in at.error)


def test_approve_without_reviewer_name_is_refused(policy_chunks, isolated_outputs):
    session, *_ = make_session(policy_chunks, "OPRA-TEST-201")
    at = open_app(session, reviewer="")
    at.button(key="approve-TEST-201-F01").click().run()
    assert at.session_state["session"].item_state("TEST-201-F01").status is ItemStatus.PENDING
    assert any("reviewer name is required" in e.value for e in at.error)
