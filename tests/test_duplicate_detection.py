"""Tests for the deterministic duplicate-detection prefilter (§12)."""
from triage_pipeline.duplicate_detection import (
    build_duplicate_candidates,
    explicit_link_candidates,
    title_similarity_candidates,
)
from triage_pipeline.schemas import LinkedTicket


def test_explicit_duplicate_link_detected():
    linked = [
        LinkedTicket(
            key="ABC-1",
            link_type="duplicates",
            title="Same bug",
            status="Open",
            description_md="",
        ),
        LinkedTicket(
            key="ABC-2",
            link_type="relates to",
            title="Different bug",
            status="Open",
            description_md="",
        ),
    ]
    candidates = explicit_link_candidates(linked)
    assert len(candidates) == 1
    assert candidates[0].ticket_ref == "ABC-1"
    assert candidates[0].source == "explicit_jira_link"
    assert candidates[0].similarity_score == 1.0


def test_title_similarity_top_n_only():
    titles = [f"Unrelated ticket {i}" for i in range(20)]
    titles.append("Login button does nothing on Safari")
    candidates = title_similarity_candidates(
        "Login button does nothing on Safari browser", titles, top_n=5
    )
    assert len(candidates) == 5
    assert candidates[0].ticket_ref == "Login button does nothing on Safari"
    assert all(c.source == "title_similarity" for c in candidates)


def test_title_similarity_excludes_identical_title():
    candidates = title_similarity_candidates("Exact Title", ["Exact Title", "Other"])
    assert all(c.ticket_ref != "Exact Title" for c in candidates)


def test_build_duplicate_candidates_combines_both_sources():
    linked = [
        LinkedTicket(
            key="ABC-1", link_type="duplicates", title="x", status=None, description_md=""
        )
    ]
    candidates = build_duplicate_candidates(linked, "My title", ["Other title"])
    sources = {c.source for c in candidates}
    assert "explicit_jira_link" in sources
    assert "title_similarity" in sources
