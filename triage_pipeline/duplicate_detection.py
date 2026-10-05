"""Deterministic duplicate-detection prefilter (§3).

This runs in plain Python BEFORE the Stage 1 LLM call. The model's job is
then narrow: adjudicate whether any of the already-scored candidates handed
to it is actually a duplicate -- it never sees a raw dump of recent ticket
titles.
"""
from __future__ import annotations

import difflib

from .schemas import DuplicateCandidate, LinkedTicket

TOP_N_TITLE_MATCHES = 5


def explicit_link_candidates(linked_tickets: list[LinkedTicket]) -> list[DuplicateCandidate]:
    """Any explicit Jira issue link whose type contains "duplicat" is a
    strong, direct candidate."""
    out = []
    for lt in linked_tickets:
        if "duplicat" in lt.link_type.lower():
            out.append(
                DuplicateCandidate(
                    ticket_ref=lt.key,
                    similarity_score=1.0,
                    source="explicit_jira_link",
                )
            )
    return out


def title_similarity_candidates(
    title: str, recent_ticket_titles: list[str], top_n: int = TOP_N_TITLE_MATCHES
) -> list[DuplicateCandidate]:
    """Deterministic string-similarity score between this ticket's title and
    every entry in ``recent_ticket_titles``; only the top N scored
    candidates are returned -- never the full raw title list."""
    scored = [
        (other, difflib.SequenceMatcher(a=title.lower(), b=other.lower()).ratio())
        for other in recent_ticket_titles
        if other.strip() and other.strip().lower() != title.strip().lower()
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [
        DuplicateCandidate(ticket_ref=ref, similarity_score=round(score, 4), source="title_similarity")
        for ref, score in scored[:top_n]
    ]


def build_duplicate_candidates(
    linked_tickets: list[LinkedTicket], title: str, recent_ticket_titles: list[str]
) -> list[DuplicateCandidate]:
    """Full deterministic prefilter: explicit links + top-N title matches."""
    return explicit_link_candidates(linked_tickets) + title_similarity_candidates(
        title, recent_ticket_titles
    )
