"""Deterministic orchestrator (§0.1).

Plain Python control flow -- never an LLM "supervisor"/"router". Reads
structured fields (verdict, confidence) and branches with ordinary `if`
statements.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .llm.base import LLMClient
from .rubrics import score_to_points
from .schemas import InvestigationResult, NormalizedTicket, TriageReport
from .stages import stage1_classify, stage2_investigate, stage3_writeup
from .stages import stage1_5_challenge

DEFAULT_CHEAP_MODEL = "cheap"  # placeholder model id, resolved by the chosen backend
DEFAULT_STRONG_MODEL = "strong"


@dataclass
class ModelConfig:
    cheap_model: str = DEFAULT_CHEAP_MODEL
    strong_model: str = DEFAULT_STRONG_MODEL
    confidence_threshold: float = stage2_investigate.DEFAULT_CONFIDENCE_THRESHOLD
    max_tool_turns: int = stage2_investigate.DEFAULT_MAX_TOOL_TURNS
    # Challenge loop config
    enable_challenge: bool = False
    challenge_confidence_threshold: float = 0.75
    challenge_max_loops: int = stage1_5_challenge.DEFAULT_MAX_LOOPS


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
    print(f"[stage1] Classifying ticket {ticket.ticket_id}...")
    t0 = time.monotonic()
    facts, classification = stage1_classify.run(
        llm_client, ticket, recent_ticket_titles, model=config.cheap_model
    )
    print(
        f"[stage1] Done in {time.monotonic() - t0:.1f}s -- verdict={classification.verdict}, "
        f"confidence={classification.confidence:.0%}"
    )

    investigation: Optional[InvestigationResult] = None
    story_points: Optional[int] = None
    final_confidence = classification.confidence
    challenge_history = []
    was_challenged = False
    escalated_to_human = False

    # Deterministic branch: control flow is plain Python reading
    # `classification.verdict`, never an LLM decision (§0.1).
    if classification.verdict == "bug":
        print("[stage2] Investigating (localize + fix + impact, this can take a while)...")
        t0 = time.monotonic()
        investigation = stage2_investigate.run(
            llm_client,
            facts,
            ticket.title,
            repo_root,
            model=config.strong_model,
            confidence_threshold=config.confidence_threshold,
            max_tool_turns=config.max_tool_turns,
        )
        print(
            f"[stage2] Done in {time.monotonic() - t0:.1f}s -- "
            f"overall_confidence={investigation.overall_confidence:.0%}, "
            f"{len(investigation.candidate_areas)} candidate area(s), "
            f"{len(investigation.fix_proposals)} fix proposal(s)"
            + (" [DEGRADED: tool budget exhausted]" if investigation.degraded else "")
        )

        # --- DECISION GATE: Enter Challenge Loop? ---
        should_challenge = (
            config.enable_challenge
            and investigation.overall_confidence < config.challenge_confidence_threshold
            and not investigation.degraded
        )

        if should_challenge:
            print(
                f"[stage1.5] Challenge loop triggered "
                f"(confidence {investigation.overall_confidence:.0%} < {config.challenge_confidence_threshold:.0%})"
            )
            t0 = time.monotonic()
            investigation, final_confidence, challenge_history, escalated_to_human = (
                stage1_5_challenge.run_challenge_loop(
                    llm_client,
                    facts,
                    ticket.title,
                    repo_root,
                    investigation,
                    investigation.overall_confidence,
                    model=config.strong_model,
                    max_loops=config.challenge_max_loops,
                )
            )
            was_challenged = True
            print(
                f"[stage1.5] Challenge loop done in {time.monotonic() - t0:.1f}s -- "
                f"final_confidence={final_confidence:.0%}, "
                f"escalated={escalated_to_human}"
            )

        # Deterministic story-point scoring -- plain Python, NOT an LLM call (§4).
        # Use final confidence (after challenge if applicable)
        if investigation.story_point_factors is not None:
            story_points = score_to_points(investigation.story_point_factors)
    else:
        print(f"[stage2] Skipped (verdict is '{classification.verdict}', not 'bug').")

    # --- Stage 3: Write-up ---
    print("[stage3] Writing up report (jira comment, next steps)...")
    t0 = time.monotonic()
    writeup = stage3_writeup.run(
        llm_client,
        ticket.ticket_id,
        facts,
        classification,
        investigation,
        story_points,
        model=config.cheap_model,
        challenge_history=challenge_history if was_challenged else None,
    )
    print(f"[stage3] Done in {time.monotonic() - t0:.1f}s")

    needs_human_review = (
        classification.verdict == "insufficient_info"
        or classification.confidence < 0.5
        or escalated_to_human
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
        confidence=final_confidence,  # Use final confidence (after challenge)
        needs_human_review=needs_human_review,
        jira_comment_md=writeup["jira_comment_md"],
        reporter_followup_md=writeup["reporter_followup_md"],
        suggested_next_steps=writeup["suggested_next_steps"],
        duplicate_of=classification.duplicate_of,
        duplicate_candidates_considered=classification.duplicate_candidates_considered,
        root_cause_hypothesis=investigation.root_cause_hypothesis if investigation else None,
        missing_info=facts.missing_info,
        ticket_title=ticket.title,
        symptoms=facts.symptoms,
        reported_priority=ticket.reported_priority,
        reported_labels=ticket.reported_labels,
        reasoning_short=classification.reasoning_short,
        was_challenged=was_challenged,
        challenge_history=challenge_history,
    )

