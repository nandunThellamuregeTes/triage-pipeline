"""CLI entry point (§11).

    python -m triage_pipeline.main --demo
    python -m triage_pipeline.main --source {jira,rovo,manual} ... --repo PATH --backend ...
    python -m triage_pipeline.main --verify-fix --ticket-id KEY --repo PATH --base REF ...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .adapters.jira import JiraAuthError, JiraFetchError, JiraRestAdapter
from .adapters.manual import ManualAdapter
from .adapters.rovo import RovoExportAdapter
from .demo import demo_stub_client
from .llm.base import LLMClient
from .llm.caching import CachingLLMClient
from .llm.copilot_cli import CopilotCLIClient
from .orchestrator import ModelConfig, run_pipeline
from .render import render_fix_verification, render_triage_report
from .schemas import NormalizedTicket
from .stages import stage4_verify
from .tools.git_diff import GitDiffTool

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEMO_TICKET_PATH = PACKAGE_ROOT / "sample_data" / "demo_ticket.md"
DEMO_REPO_PATH = PACKAGE_ROOT / "sample_data" / "demo_repo"

COPILOT_CHEAP_MODEL = "claude-haiku-4.5"
COPILOT_STRONG_MODEL = "claude-sonnet-5"


class CliError(RuntimeError):
    """Actionable, user-facing CLI error -- printed cleanly, never a raw traceback."""


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m triage_pipeline.main")
    p.add_argument("--demo", action="store_true", help="Run fully offline against bundled sample data.")

    # Diagnostic pipeline input selection
    p.add_argument("--source", choices=["jira", "rovo", "manual"], help="Ticket source adapter.")
    p.add_argument("--file", help="Path to a Rovo-export Markdown file or a manual JSON ticket.")
    p.add_argument("--ticket-key", help="Jira ticket key. Implies --source jira.")
    p.add_argument("--no-comments", action="store_true", help="Do not fetch ticket comments.")
    p.add_argument("--no-linked-tickets", action="store_true", help="Disable linked-ticket fetching entirely.")
    p.add_argument("--max-linked-tickets", type=int, default=10, help="Cap on linked tickets fetched (default 10).")

    p.add_argument("--repo", help="Path to the repository Stage 2 should search / Stage 4 should diff.")
    p.add_argument("--backend", choices=["copilot", "stub"], default="copilot", help="LLM backend.")
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Stream a line per tool call/turn from the copilot CLI backend as it runs, "
        "instead of staying silent until a stage finishes.",
    )
    p.add_argument("--out", help="Write rendered Markdown report to this path (default: stdout).")
    p.add_argument("--json", action="store_true", help="Also/instead print the raw structured report as JSON.")

    p.add_argument("--fresh", action="store_true", help="Ignore the checkpoint cache entirely.")
    p.add_argument("--redo-from", type=int, metavar="N", help="Force stage N (1-4) onward to recompute.")
    p.add_argument("--no-checkpoint", action="store_true", help="Disable checkpointing entirely.")
    p.add_argument("--checkpoint", help="Checkpoint file path (default: .triage_checkpoints/<ticket_id>.json).")
    p.add_argument("--max-tool-turns", type=int, help="Tool-turn budget for Stage 2 (and Stage 4 has none).")
    p.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.55,
        help="Stage 2 localization confidence threshold for gating fix generation (default 0.55).",
    )

    # Challenge loop (Stage 1.5)
    p.add_argument(
        "--challenge",
        action="store_true",
        help="Enable the iterative challenge loop (Agent 2 tries to disprove Agent 1's hypothesis).",
    )
    p.add_argument(
        "--challenge-confidence-threshold",
        type=float,
        default=0.75,
        help="Challenge loop triggers if hypothesis confidence is below this (default 0.75). "
        "Only used with --challenge.",
    )
    p.add_argument(
        "--challenge-max-loops",
        type=int,
        default=3,
        help="Maximum number of challenge loop iterations (default 3). Only used with --challenge.",
    )

    # Attachment processing
    p.add_argument(
        "--enable-attachments",
        action="store_true",
        help="Fetch and process attachments from Jira tickets (images, logs, files). "
        "Applies PII redaction (school names, entities) before sending to LLM.",
    )

    # Custom field extraction (enabled by default -- cheap text, high value;
    # see CUSTOM_FIELD_NAME_PATTERNS in adapters/jira.py)
    p.add_argument(
        "--no-custom-fields",
        action="store_true",
        help="Disable extraction of Jira custom fields matching triage-relevant name "
        "patterns (e.g. 'Steps to reproduce issue', 'Environment'). Enabled by "
        "default since this is often where the most authoritative repro/environment "
        "detail lives, separate from the main description.",
    )

    # Stage 4
    p.add_argument("--verify-fix", action="store_true", help="Run Stage 4 (Verify Fix) instead of the main pipeline.")
    p.add_argument("--ticket-id", help="Ticket ID whose earlier diagnostic checkpoint to verify against.")
    p.add_argument("--base", help="Git base ref to diff against (Stage 4).")
    p.add_argument("--paths", nargs="*", help="Scope the diff to these paths (Stage 4).")

    return p


def _make_llm_client(backend: str, verbose: bool = False) -> LLMClient:
    if backend == "stub":
        return demo_stub_client()
    if backend == "copilot":
        return CopilotCLIClient(verbose=verbose)
    raise CliError(f"Unknown backend: {backend}")


def _load_ticket(
    args, enable_attachments: bool = False, include_custom_fields: bool = True
) -> NormalizedTicket:
    if args.demo:
        return RovoExportAdapter(str(DEMO_TICKET_PATH)).fetch()

    source = args.source
    if args.ticket_key and not source:
        source = "jira"
    if not source:
        raise CliError(
            "No ticket source specified. Use --source {jira,rovo,manual} (or --ticket-key for Jira), "
            "or --demo for an offline run."
        )

    if source == "jira":
        if not args.ticket_key:
            raise CliError("--source jira requires --ticket-key KEY.")
        try:
            adapter = JiraRestAdapter(
                ticket_key=args.ticket_key,
                include_comments=not args.no_comments,
                include_linked_tickets=not args.no_linked_tickets,
                max_linked_tickets=args.max_linked_tickets,
                include_attachments=enable_attachments,
                include_custom_fields=include_custom_fields,
            )
            return adapter.fetch()
        except (JiraAuthError, JiraFetchError) as exc:
            raise CliError(str(exc)) from exc
    if source == "rovo":
        if not args.file:
            raise CliError("--source rovo requires --file PATH.")
        return RovoExportAdapter(args.file).fetch()
    if source == "manual":
        if not args.file:
            raise CliError("--source manual requires --file PATH.")
        return ManualAdapter(file_path=args.file).fetch()
    raise CliError(f"Unknown source: {source}")


def _default_checkpoint_path(ticket_id: str) -> str:
    return str(Path(".triage_checkpoints") / f"{ticket_id}.json")


def _run_diagnostic(args) -> int:
    pipeline_start_time = time.monotonic()
    
    ticket = _load_ticket(
        args,
        enable_attachments=args.enable_attachments,
        include_custom_fields=not args.no_custom_fields,
    )

    repo_root = str(DEMO_REPO_PATH) if args.demo else args.repo
    if not repo_root:
        raise CliError("--repo PATH is required (Stage 2 needs a repository to search).")

    backend_name = "stub" if args.demo else args.backend
    llm_client = _make_llm_client(backend_name, verbose=args.verbose)

    if not args.no_checkpoint:
        checkpoint_path = args.checkpoint or _default_checkpoint_path(ticket.ticket_id)
        llm_client = CachingLLMClient(
            llm_client,
            checkpoint_path=checkpoint_path,
            ticket_fingerprint=ticket.content_fingerprint(),
            fresh=args.fresh,
            redo_from_stage=args.redo_from,
        )

    config = ModelConfig(
        cheap_model="stub" if backend_name == "stub" else COPILOT_CHEAP_MODEL,
        strong_model="stub" if backend_name == "stub" else COPILOT_STRONG_MODEL,
        confidence_threshold=args.confidence_threshold,
        max_tool_turns=args.max_tool_turns or ModelConfig().max_tool_turns,
        enable_challenge=args.challenge,
        challenge_confidence_threshold=args.challenge_confidence_threshold,
        challenge_max_loops=args.challenge_max_loops,
        enable_attachments=args.enable_attachments,
    )

    report = run_pipeline(llm_client, ticket, repo_root, config=config)
    pipeline_total_time = time.monotonic() - pipeline_start_time
    
    markdown = render_triage_report(report)

    if args.out:
        Path(args.out).write_text(markdown)
        print(f"Report written to {args.out}")
    else:
        print(markdown)

    if args.json:
        print(json.dumps(asdict(report), indent=2))

    # Print total pipeline timing summary
    print(f"\n{'='*60}")
    print(f"[PIPELINE SUMMARY] Total end-to-end time: {pipeline_total_time:.1f}s")
    print(f"{'='*60}")

    return 0


def _run_verify_fix(args) -> int:
    if not args.ticket_id:
        raise CliError("--verify-fix requires --ticket-id KEY.")
    if not args.repo:
        raise CliError("--verify-fix requires --repo PATH.")
    if not args.base:
        raise CliError("--verify-fix requires --base REF.")

    checkpoint_path = Path(args.checkpoint or _default_checkpoint_path(args.ticket_id))
    if not checkpoint_path.exists():
        raise CliError(
            f"No earlier diagnostic checkpoint found for {args.ticket_id} at "
            f"{checkpoint_path}. Run the main pipeline for this ticket first."
        )
    data = json.loads(checkpoint_path.read_text())
    stage1_entry = data.get("stages", {}).get("stage1_classify")
    stage2_entry = data.get("stages", {}).get("stage2_investigate")
    if stage1_entry is None or stage1_entry["output"]["classification"]["verdict"] != "bug":
        raise CliError(
            f"{args.ticket_id} was never classified as a 'bug' in its checkpoint "
            "(or no checkpoint exists) -- Stage 4 requires an earlier 'bug' diagnosis."
        )
    if stage2_entry is None:
        raise CliError(f"No Stage 2 investigation found in the checkpoint for {args.ticket_id}.")

    from .schemas import CodeHit, FixProposal, InvestigationResult, StoryPointFactors

    inv_raw = stage2_entry["output"]
    investigation = InvestigationResult(
        candidate_areas=[CodeHit(**c) for c in inv_raw.get("candidate_areas", [])],
        root_cause_hypothesis=inv_raw.get("root_cause_hypothesis", ""),
        overall_confidence=float(inv_raw.get("overall_confidence", 0.0)),
        fix_proposals=[FixProposal(**fp) for fp in inv_raw.get("fix_proposals", [])],
        blast_radius=inv_raw.get("blast_radius"),
        affected_areas=list(inv_raw.get("affected_areas", [])),
        impact_notes=inv_raw.get("impact_notes", ""),
        story_point_factors=(
            StoryPointFactors(**inv_raw["story_point_factors"])
            if inv_raw.get("story_point_factors")
            else None
        ),
    )

    try:
        git_tool = GitDiffTool(args.repo)
        print(f"[stage4] Computing git diff against {args.base}...")
        diff_result = git_tool.diff(args.base, paths=args.paths)
    except (ValueError, RuntimeError) as exc:
        raise CliError(str(exc)) from exc

    backend_name = args.backend if args.backend != "stub" else "stub"
    llm_client = _make_llm_client(backend_name, verbose=args.verbose)
    model = COPILOT_STRONG_MODEL if backend_name == "copilot" else "stub"

    print(f"[stage4] Verifying fix against original diagnosis for {args.ticket_id}...")
    t0 = time.monotonic()
    verification = stage4_verify.run(
        llm_client,
        args.ticket_id,
        investigation,
        diff_result.text,
        diff_result.changed_files,
        diff_result.truncated,
        model=model,
    )
    print(f"[stage4] Done in {time.monotonic() - t0:.1f}s")
    markdown = render_fix_verification(args.ticket_id, verification)

    if args.out:
        Path(args.out).write_text(markdown)
        print(f"Report written to {args.out}")
    else:
        print(markdown)

    if args.json:
        print(json.dumps(asdict(verification), indent=2))

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            if args.verify_fix:
                result = _run_verify_fix(args)
            else:
                result = _run_diagnostic(args)
        except CliError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        finally:
            for w in caught:
                print(f"[warning] {w.message}", file=sys.stderr)
    return result


if __name__ == "__main__":
    sys.exit(main())
