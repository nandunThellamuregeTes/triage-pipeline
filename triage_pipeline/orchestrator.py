"""Deterministic orchestrator (§0.1).

Plain Python control flow -- never an LLM "supervisor"/"router". Reads
structured fields (verdict, confidence) and branches with ordinary `if`
statements.

New flow (§1.1): Stage 0 (attachments) -> Stage 1 (classify) -> Stage 1.5
challenge loop (ALWAYS runs >=1 iteration for a "bug" verdict when enabled;
NEVER gated by confidence) -> Stage 2 (investigate, its own confidence gate
only controls whether IT produces fix proposals/story points/blast radius,
never whether the challenge loop ran) -> Stage 3 (write-up, narrates the
pipeline's code-computed `final_confidence` verbatim, never its own number).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .confidence import ConfidenceInputs, compute_confidence
from .llm.base import LLMClient
from .rubrics import score_to_points
from .schemas import (
    InvestigationResult,
    NormalizedTicket,
    ServiceMatch,
    Stage0Result,
    TriageReport,
)
from .stages import stage0_attachments, stage1_classify, stage2_investigate, stage3_writeup
from .stages import stage1_5_challenge
from .workspace import Workspace, git_head, route_services

DEFAULT_CHEAP_MODEL = "cheap"  # placeholder model id, resolved by the chosen backend
DEFAULT_STRONG_MODEL = "strong"
DEFAULT_VISION_MODEL = "strong"  # placeholder; no separate vision API exists (§1.5)


@dataclass
class ModelConfig:
    cheap_model: str = DEFAULT_CHEAP_MODEL
    strong_model: str = DEFAULT_STRONG_MODEL
    vision_model: str = DEFAULT_VISION_MODEL
    confidence_threshold: float = stage2_investigate.DEFAULT_CONFIDENCE_THRESHOLD
    max_tool_turns: int = stage2_investigate.DEFAULT_MAX_TOOL_TURNS
    localization_samples: int = stage2_investigate.DEFAULT_LOCALIZATION_SAMPLES
    # Challenge loop config: opt-in flag only -- runs unconditionally (>=1
    # iteration, never confidence-gated) for every "bug" verdict ticket when
    # enabled, since it runs BEFORE Stage 2 and there is no Stage 2
    # confidence yet to gate on (§1.1).
    enable_challenge: bool = False
    challenge_max_loops: int = stage1_5_challenge.DEFAULT_MAX_LOOPS
    # Attachment processing config (§1.5)
    enable_attachments: bool = False
    max_attachment_size: int = stage0_attachments.DEFAULT_MAX_ATTACHMENT_SIZE
    video_fps: int = stage0_attachments.DEFAULT_VIDEO_FPS
    video_seconds: int = stage0_attachments.DEFAULT_VIDEO_SECONDS
    max_video_frames: int = stage0_attachments.DEFAULT_MAX_FRAMES


def run_pipeline(
    llm_client: LLMClient,
    ticket: NormalizedTicket,
    repo_root: str,
    recent_ticket_titles: Optional[list[str]] = None,
    config: Optional[ModelConfig] = None,
    workspace: Optional[Workspace] = None,
) -> TriageReport:
    config = config or ModelConfig()
    recent_ticket_titles = recent_ticket_titles or []

    # --- Stage 0: Attachment analysis (§1.5), ALWAYS first, before Stage 1.
    # `analyze_attachments([])` short-circuits to an empty, cheap
    # `Stage0Result` with no LLM calls at all when there is nothing to
    # inspect or attachments are disabled, so this is always safe to call.
    stage0_result: Stage0Result
    if config.enable_attachments and ticket.attachments:
        print(f"[stage0] Analyzing {len(ticket.attachments)} attachment(s)...")
        t0 = time.monotonic()
        stage0_result = stage0_attachments.analyze_attachments(
            llm_client,
            ticket.attachments,
            model=config.vision_model,
            max_attachment_size=config.max_attachment_size,
            video_fps=config.video_fps,
            video_seconds=config.video_seconds,
            max_frames=config.max_video_frames,
            ticket_context=f"{ticket.title}\n\n{ticket.description_md}",
        )
        print(
            f"[stage0] Done in {time.monotonic() - t0:.1f}s -- "
            f"attachments_inspected={stage0_result.attachments_inspected}"
        )
    else:
        stage0_result = Stage0Result()

    # --- Stage 1: Understand & Classify ---
    print(f"[stage1] Classifying ticket {ticket.ticket_id}...")
    t0 = time.monotonic()
    facts, classification = stage1_classify.run(
        llm_client,
        ticket,
        recent_ticket_titles,
        model=config.cheap_model,
        stage0_attachment_analysis=stage0_result.observed_in_attachments,
        stage0_attachments_inspected=stage0_result.attachments_inspected,
        stage0_observed_periodic_interval_ms=stage0_result.observed_periodic_interval_ms,
    )
    print(
        f"[stage1] Done in {time.monotonic() - t0:.1f}s -- verdict={classification.verdict}, "
        f"confidence={classification.confidence:.0%}, evidence_quality={classification.evidence_quality}"
    )

    investigation: Optional[InvestigationResult] = None
    story_points: Optional[int] = None
    challenge_history = []
    was_challenged = False
    agent1_initial_pass = None
    agent2_blind_pass = None
    final_hypotheses: list = []
    convergence_status: Optional[str] = None
    agreed_experiments: list[str] = []
    agreed_missing_evidence: list[str] = []
    challenge_symbols_not_found = []
    challenge_confidence = classification.confidence
    loop_iterations_run = 0

    # Multi-repo routing (deterministic, no LLM): pick which service repos
    # Stage 1.5/2 should search. No match means there is nothing to search
    # with, which is exactly what "insufficient_info" means.
    routed: Optional[list[ServiceMatch]] = None
    if workspace is not None and workspace.is_multi and classification.verdict == "bug":
        routing_text = "\n".join(
            [ticket.title, ticket.description_md, facts.stack_trace_or_error or "", *ticket.comments]
        )
        routed = route_services(workspace, facts.technical_signals, routing_text)
        if routed:
            print("[routing] Selected service(s): " + ", ".join(f"{m.service} (score {m.score})" for m in routed))
        else:
            print("[routing] No service matched the ticket's signals; downgrading verdict to insufficient_info.")
            classification.verdict = "insufficient_info"
            classification.reasoning_short = (
                classification.reasoning_short
                + " [Routing: none of the configured repositories contains the ticket's technical "
                "signals, so there is nothing to search with.]"
            )
            facts.missing_info.append(
                "Name the service/endpoint or component involved (no configured repository matched "
                "the signals in this ticket)."
            )

    # Deterministic branch: control flow is plain Python reading
    # `classification.verdict`, never an LLM decision (§0.1).
    if classification.verdict == "bug":
        # --- Stage 1.5: Challenge Loop (runs BEFORE Stage 2 investigation).
        # Unconditional for every "bug" verdict ticket when enabled -- ALWAYS
        # runs >=1 iteration, never gated by any confidence value (§1.1/§1.3).
        if config.enable_challenge:
            print("[stage1.5] Running challenge loop (blind passes + mechanism-check iteration)...")
            t0 = time.monotonic()
            loop_result = stage1_5_challenge.run_challenge_loop(
                llm_client,
                facts,
                ticket.title,
                repo_root,
                model=config.strong_model,
                evidence_gaps=[g.gap for g in classification.evidence_gaps],
                max_loops=config.challenge_max_loops,
                observed_in_attachments=stage0_result.observed_in_attachments,
                attachments_inspected=stage0_result.attachments_inspected,
                observed_periodic_interval_ms=stage0_result.observed_periodic_interval_ms,
                services=routed,
            )
            was_challenged = True
            agent1_initial_pass = loop_result.agent1_initial_pass
            agent2_blind_pass = loop_result.agent2_blind_pass
            final_hypotheses = loop_result.hypotheses
            challenge_confidence = loop_result.confidence
            convergence_status = loop_result.convergence_status
            challenge_history = loop_result.challenge_history
            agreed_experiments = loop_result.agreed_experiments
            agreed_missing_evidence = loop_result.agreed_missing_evidence
            challenge_symbols_not_found = loop_result.symbols_not_found
            loop_iterations_run = loop_result.loop_iterations_run

            # §1.3: LOOP_NOT_RUN must NEVER be a final state in production.
            # The loop body always executes >=1 iteration when it runs at
            # all (enforced in stage1_5_challenge.py), so this is a hard
            # safety net against a logic regression, not expected behavior.
            if loop_result.loop_iterations_run < 1 or convergence_status == "LOOP_NOT_RUN":
                raise RuntimeError(
                    "Challenge loop finished with LOOP_NOT_RUN (or zero iterations) as its "
                    "final convergence_status -- this must never happen once the loop is "
                    "enabled for a 'bug' verdict ticket (§1.3)."
                )

            print(
                f"[stage1.5] Challenge loop done in {time.monotonic() - t0:.1f}s -- "
                f"convergence_status={convergence_status}, "
                f"iterations_run={loop_result.loop_iterations_run}, "
                f"raw_confidence={challenge_confidence:.0%}"
            )

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
            attachments=ticket.attachments if config.enable_attachments else None,
            challenge_hypotheses=final_hypotheses or None,
            convergence_status=convergence_status,
            agreed_experiments=agreed_experiments or None,
            localization_samples=config.localization_samples,
            services=routed,
        )
        print(
            f"[stage2] Done in {time.monotonic() - t0:.1f}s -- "
            f"overall_confidence={investigation.overall_confidence:.0%}, "
            f"{len(investigation.candidate_areas)} candidate area(s), "
            f"{len(investigation.fix_proposals)} fix proposal(s)"
            + (" [DEGRADED: tool budget exhausted]" if investigation.degraded else "")
        )

        # Deterministic story-point scoring -- plain Python, NOT an LLM call (§4).
        if investigation.story_point_factors is not None:
            story_points = score_to_points(investigation.story_point_factors)
    else:
        print(f"[stage2] Skipped (verdict is '{classification.verdict}', not 'bug').")

    # --- §1.4: final confidence is computed HERE, in code, from Stage
    # 1/1.5/2 outputs -- never restated or recomputed by Stage 3. ---
    raw_confidence = investigation.overall_confidence if investigation is not None else (
        challenge_confidence if was_challenged else classification.confidence
    )
    # `executed_or_reproduced`: nothing is ever actually executed by this
    # pipeline (no code is run, no test suite is invoked) -- the only
    # evidence of a REAL reproduction is the reporter's/support's own
    # stated replication, captured in TicketFacts.is_reproducible_by_reporter
    # (see stage1_classify.compute_evidence_quality for the same judgement
    # call). This is a deliberate, documented reading of "executed" per
    # §1.4's instruction to use judgement where the pipeline has no repro
    # harness of its own.
    executed_or_reproduced = bool(facts.is_reproducible_by_reporter)
    explains_why_now = True
    if final_hypotheses:
        why_now = (final_hypotheses[0].explains_why_now or "").strip().lower()
        explains_why_now = bool(why_now) and not why_now.startswith(
            ("unknown", "cannot", "unable", "no ")
        )
    key_symbol_not_located = bool(challenge_symbols_not_found) or bool(
        investigation.symbols_not_found if investigation else []
    )
    final_confidence, confidence_caps_applied = compute_confidence(
        ConfidenceInputs(
            raw_confidence=raw_confidence,
            executed_or_reproduced=executed_or_reproduced,
            evidence_quality=classification.evidence_quality,
            convergence_status=convergence_status,
            explains_why_now=explains_why_now,
            localization_agreement=investigation.localization_agreement if investigation else None,
            key_symbol_not_located=key_symbol_not_located,
        )
    )

    # Merge symbols_not_found across every stage that tracks due diligence.
    symbols_not_found = list(challenge_symbols_not_found) + list(
        investigation.symbols_not_found if investigation else []
    )
    confidence_concerns = list(investigation.confidence_concerns) if investigation else []

    # --- Stage 3: Write-up. `final_confidence` is passed read-only; Stage 3
    # must only narrate it verbatim, never compute its own number (§1.4). ---
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
        final_confidence=final_confidence,
        confidence_caps_applied=confidence_caps_applied,
        challenge_history=challenge_history if was_challenged else None,
        hypotheses=final_hypotheses or None,
        convergence_status=convergence_status,
        agreed_experiments=agreed_experiments or None,
        agreed_missing_evidence=agreed_missing_evidence or None,
        stage0_result=stage0_result,
    )
    print(f"[stage3] Done in {time.monotonic() - t0:.1f}s")

    needs_human_review = (
        classification.verdict == "insufficient_info"
        or classification.confidence < 0.5
        or (convergence_status not in (None, "CONVERGED"))
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
        confidence=final_confidence,  # code-computed, §1.4 -- never restated downstream
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
        loop_iterations_run=loop_iterations_run,
        agent1_initial_pass=agent1_initial_pass,
        agent2_blind_pass=agent2_blind_pass,
        hypotheses=final_hypotheses,
        convergence_status=convergence_status,
        agreed_experiments=agreed_experiments,
        agreed_missing_evidence=agreed_missing_evidence,
        alternative_hypotheses=investigation.alternative_hypotheses if investigation else [],
        confidence_concerns=confidence_concerns,
        confidence_caps_applied=confidence_caps_applied,
        how_to_confirm=investigation.how_to_confirm if investigation else [],
        symbols_not_found=symbols_not_found,
        localization_agreement=investigation.localization_agreement if investigation else None,
        localization_samples=investigation.localization_samples if investigation else [],
        evidence_quality=classification.evidence_quality,
        evidence_gaps=classification.evidence_gaps,
        attachments_analyzed=stage0_result.attachments,
        observed_in_attachments=stage0_result.observed_in_attachments,
        observed_periodic_interval_ms=stage0_result.observed_periodic_interval_ms,
        attachments_inspected=stage0_result.attachments_inspected,
        service_routing=routed or [],
        repo_heads={s.name: git_head(s.path) for s in workspace.services} if workspace and workspace.is_multi else {},
    )
