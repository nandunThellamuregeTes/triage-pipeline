"""Deterministic orchestrator (§0.1).

Plain Python control flow -- never an LLM "supervisor"/"router". Reads
structured fields (verdict, confidence) and branches with ordinary `if`
statements.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .llm.base import LLMClient
from .rubrics import score_to_points
from .schemas import InvestigationResult, NormalizedTicket, TriageReport
from .stages import stage1_classify, stage2_investigate, stage3_writeup

DEFAULT_CHEAP_MODEL = "cheap"  # placeholder model id, resolved by the chosen backend
DEFAULT_STRONG_MODEL = "strong"


@dataclass
class ModelConfig:
    cheap_model: str = DEFAULT_CHEAP_MODEL
    strong_model: str = DEFAULT_STRONG_MODEL
    confidence_threshold: float = stage2_investigate.DEFAULT_CONFIDENCE_THRESHOLD
    max_tool_turns: int = stage2_investigate.DEFAULT_MAX_TOOL_TURNS


def run_pipeline(
    llm_client: LLMClient,
    ticket: NormalizedTicket,
    repo_root: str,
    recent_ticket_titles: Optional[list[str]] = None,
    config: Optional[ModelConfig] = None,
) -> TriageReport:
    config = config or ModelConfig()
    recent_ticket_titles = recent_ticket_titles or []

    # --- Stage 1: Understand & Classify ---
    facts, classification = stage1_classify.run(
        llm_client, ticket, recent_ticket_titles, model=config.cheap_model
    )

    investigation: Optional[InvestigationResult] = None
    story_points: Optional[int] = None

    # Deterministic branch: control flow is plain Python reading
    # `classification.verdict`, never an LLM decision (§0.1).
    if classification.verdict == "bug":
        investigation = stage2_investigate.run(
            llm_client,
            facts,
            ticket.title,
            repo_root,
            model=config.strong_model,
            confidence_threshold=config.confidence_threshold,
            max_tool_turns=config.max_tool_turns,
        )
        # Deterministic story-point scoring -- plain Python, NOT an LLM call (§4).
        if investigation.story_point_factors is not None:
            story_points = score_to_points(investigation.story_point_factors)

    # --- Stage 3: Write-up ---
    writeup = stage3_writeup.run(
        llm_client,
        ticket.ticket_id,
        facts,
        classification,
        investigation,
        story_points,
        model=config.cheap_model,
    )

    needs_human_review = (
        classification.verdict == "insufficient_info"
        or classification.confidence < 0.5
        or (investigation is not None and investigation.degraded)
        or (investigation is not None and investigation.overall_confidence < config.confidence_threshold)
    )

    return TriageReport(
        ticket_id=ticket.ticket_id,
        verdict=classification.verdict,
        reported_severity=facts.reported_severity,
        validated_severity=classification.validated_severity,
        affected_areas=investigation.candidate_areas if investigation else [],
        fix_proposals=investigation.fix_proposals if investigation else [],
        blast_radius=investigation.blast_radius if investigation else None,
        story_points=story_points,
        story_point_factors=investigation.story_point_factors if investigation else None,
        confidence=classification.confidence,
        needs_human_review=needs_human_review,
        jira_comment_md=writeup["jira_comment_md"],
        reporter_followup_md=writeup["reporter_followup_md"],
        suggested_next_steps=writeup["suggested_next_steps"],
        duplicate_of=classification.duplicate_of,
        duplicate_candidates_considered=classification.duplicate_candidates_considered,
        root_cause_hypothesis=investigation.root_cause_hypothesis if investigation else None,
        missing_info=facts.missing_info,
    )
