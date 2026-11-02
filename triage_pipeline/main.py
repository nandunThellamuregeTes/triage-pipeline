"""CLI entry point (§11).

    python -m triage_pipeline.main --demo
    python -m triage_pipeline.main --source {jira,rovo,manual} ... --repo PATH --backend ...
    python -m triage_pipeline.main --verify-fix --ticket-id KEY --repo PATH --base REF ...
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .adapters.jira import JiraAuthError, JiraFetchError, JiraRestAdapter
from .adapters.manual import ManualAdapter
from .adapters.rovo import RovoExportAdapter
from .compare import (
    BackendRun,
    build_comparison,
    build_repeat_summary,
    render_comparison,
    render_repeat_summary,
)
from .config import ConfigError, load_config_file
from .demo import demo_stub_client
from .image_processing import ImageProcessor
from .llm.base import LLMClient
from .llm.caching import CachingLLMClient
from .llm.claude_cli import ClaudeCLIClient
from .llm.copilot_cli import CopilotCLIClient
from .orchestrator import ModelConfig, run_pipeline
from .pii_redaction import PIIRedactor
from .render import render_fix_verification, render_triage_report
from .schemas import NormalizedTicket
from .stages import stage4_verify
from .tools.git_diff import GitDiffTool
from .workspace import Workspace, WorkspaceError, parse_repo_specs

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEMO_TICKET_PATH = PACKAGE_ROOT / "sample_data" / "demo_ticket.md"
DEMO_REPO_PATH = PACKAGE_ROOT / "sample_data" / "demo_repo"

# Same underlying models on every harness; only the model-ID spelling differs
# (Copilot uses dots, the Claude CLI uses dashes).
BACKEND_MODELS = {
    "copilot": {"cheap": "claude-haiku-4.5", "strong": "claude-sonnet-5.5"},
    "claude": {"cheap": "claude-haiku-4-5", "strong": "claude-sonnet-5-5"},
    "stub": {"cheap": "stub", "strong": "stub"},
}
COMPARE_BACKENDS = ["copilot", "claude"]


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

    p.add_argument(
        "--repo",
        action="append",
        metavar="[NAME=]PATH",
        help="Repository Stage 2 should search / Stage 4 should diff. Repeat for several services "
        "(--repo messaging=~/code/messaging --repo staff-app=~/code/staff-app): the pipeline then routes "
        "the ticket to the matching repo(s) from its technical signals. Multi-repo is not yet supported "
        "with --verify-fix.",
    )
    p.add_argument(
        "--backend",
        choices=["copilot", "claude", "both", "stub"],
        default="copilot",
        help="LLM backend/harness. 'both' runs copilot and claude on the same ticket and writes "
        "a side-by-side comparison (not supported with --verify-fix).",
    )
    p.add_argument(
        "--config",
        help="Credentials file of KEY=VALUE lines (COPILOT_GITHUB_TOKEN, JIRA_*, ...). "
        "Default: ~/.config/triage-pipeline/config.env if it exists. Exported variables take precedence.",
    )
    p.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="Run the pipeline N times per backend (always fresh, no checkpoints) and write a "
        "stability summary: verdict flips, confidence spread, mean effort. Per-run reports go to "
        "<out>.<backend>.runK.md when --out is given.",
    )
    p.add_argument(
        "--parallel",
        action="store_true",
        help="With --backend both, run the two harnesses concurrently instead of one after the other "
        "(sequential gives cleaner timing comparisons).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Stream a line per tool call/turn from the CLI backend as it runs, "
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
        help="Enable the Stage 1.5 challenge loop: for every 'bug' verdict ticket, runs "
        "independent blind-pass hypothesis generation (Agent 1 + Agent 2) followed by "
        "iterative mechanism-check challenge/regen loops, BEFORE Stage 2 investigation. "
        "Always runs in full when enabled -- there is no confidence-based gate.",
    )
    p.add_argument(
        "--challenge-max-loops",
        type=int,
        default=3,
        help="Maximum number of challenge/regen loop iterations (default 3). Only used with "
        "--challenge. If neither terminal exit criterion is met after this many loops, "
        "settles on NEEDS_EVIDENCE or MECHANISM_CONVERGED_TRIGGER_OPEN as appropriate -- "
        "both are valid, honest end states, not failures.",
    )

    # Attachment processing
    p.add_argument(
        "--enable-attachments",
        action="store_true",
        help="Fetch and process attachments from Jira tickets (images, videos, logs, files). "
        "Images go through ImageProcessor; videos go through Stage 0 (frame extraction + "
        "vision analysis). Applies PII redaction (school names, entities) before sending to LLM.",
    )
    p.add_argument(
        "--max-attachment-size",
        type=int,
        default=None,
        help="Max size in bytes for a Stage 0 attachment to be inspected (default 50MB). "
        "Larger attachments are recorded as not-inspected, never silently dropped.",
    )
    p.add_argument(
        "--video-fps",
        type=int,
        default=None,
        help="Fixed-rate frame sampling rate for Stage 0 video extraction (default 4fps).",
    )
    p.add_argument(
        "--video-seconds",
        type=int,
        default=None,
        help="Number of leading seconds of a video to fixed-rate sample in Stage 0 (default 15).",
    )
    p.add_argument(
        "--max-video-frames",
        type=int,
        default=None,
        help="Cap on frames extracted per video in Stage 0 (default 60).",
    )
    p.add_argument(
        "--localization-samples",
        type=int,
        default=None,
        help="Number of independent Stage 2 localization samples to run for the cross-sample "
        "agreement check (default 3). The primary sample's own candidate_areas/fix proposals "
        "are never affected by the others -- they are only compared for agreement.",
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
    try:
        if backend == "copilot":
            return CopilotCLIClient(verbose=verbose)
        if backend == "claude":
            return ClaudeCLIClient(verbose=verbose)
    except RuntimeError as exc:
        raise CliError(str(exc)) from exc
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


def _default_checkpoint_path(ticket_id: str, backend: str = "copilot") -> str:
    # Copilot/stub keep the historical name; claude gets its own file so one
    # harness never silently reuses another's cached stages.
    suffix = f".{backend}" if backend == "claude" else ""
    return str(Path(".triage_checkpoints") / f"{ticket_id}{suffix}.json")


def _backend_path(path: str, backend: str) -> str:
    p = Path(path)
    return str(p.with_name(f"{p.stem}.{backend}{p.suffix}"))


def _process_image_attachments(ticket: NormalizedTicket, llm_client: LLMClient, model: str) -> None:
    """Replace each image attachment's content with real vision-extracted
    text/description, in place on ``ticket``. Runs through ``llm_client`` so
    results are cached per-stage (see CachingLLMClient) like every other
    stage, and won't be re-extracted on a rerun unless the ticket changed.
    Non-image attachments are left untouched. Failures are reported as a
    warning and the original (unprocessed) attachment is kept, never crash
    the run over one bad image.
    """
    processor = ImageProcessor(llm_client=llm_client)
    redactor = PIIRedactor()
    new_attachments = []
    for att in ticket.attachments:
        if not processor.is_image(att.mime_type):
            new_attachments.append(att)
            continue
        processed, error = processor.process_image_attachment(att, model=model, redactor=redactor)
        if error:
            print(f"[warning] Could not extract content from image attachment {att.name!r}: {error}")
            new_attachments.append(att)
        else:
            print(f"[attachments] Extracted content from image {att.name!r}.")
            new_attachments.append(processed)
    ticket.attachments = new_attachments


def _execute_pipeline(
    args, ticket: NormalizedTicket, workspace: Workspace, backend_name: str, checkpoint_path: Optional[str]
) -> tuple:
    """Run the full pipeline for one backend. Returns (report, elapsed_seconds, stats)."""
    start = time.monotonic()
    llm_client = _make_llm_client(backend_name, verbose=args.verbose)
    raw_client = llm_client

    if checkpoint_path:
        llm_client = CachingLLMClient(
            llm_client,
            checkpoint_path=checkpoint_path,
            ticket_fingerprint=ticket.content_fingerprint(),
            fresh=args.fresh,
            redo_from_stage=args.redo_from,
        )

    models = BACKEND_MODELS[backend_name]
    if args.enable_attachments and ticket.attachments:
        _process_image_attachments(ticket, llm_client, models["cheap"])

    default_config = ModelConfig()
    config = ModelConfig(
        cheap_model=models["cheap"],
        strong_model=models["strong"],
        vision_model=models["strong"],
        confidence_threshold=args.confidence_threshold,
        max_tool_turns=args.max_tool_turns or default_config.max_tool_turns,
        localization_samples=args.localization_samples or default_config.localization_samples,
        enable_challenge=args.challenge,
        challenge_max_loops=args.challenge_max_loops,
        enable_attachments=args.enable_attachments,
        max_attachment_size=args.max_attachment_size or default_config.max_attachment_size,
        video_fps=args.video_fps or default_config.video_fps,
        video_seconds=args.video_seconds or default_config.video_seconds,
        max_video_frames=args.max_video_frames or default_config.max_video_frames,
    )

    report = run_pipeline(
        llm_client, ticket, workspace.primary.path, config=config, workspace=workspace
    )
    return report, time.monotonic() - start, list(getattr(raw_client, "stats", []))


def _checkpoint_for(args, ticket: NormalizedTicket, backend_name: str, per_backend: bool) -> Optional[str]:
    if args.no_checkpoint:
        return None
    if args.checkpoint:
        return _backend_path(args.checkpoint, backend_name) if per_backend else args.checkpoint
    return _default_checkpoint_path(ticket.ticket_id, backend_name)


def _emit(markdown: str, out: Optional[str]) -> None:
    if out:
        Path(out).write_text(markdown)
        print(f"Report written to {out}")
    else:
        print(markdown)


def _run_repeat(args, ticket: NormalizedTicket, workspace: Workspace, backend_name: str, start: float) -> int:
    """Run the pipeline ``args.repeat`` times per backend, always fresh and
    without checkpoints, and write a stability summary."""
    backends = COMPARE_BACKENDS if backend_name == "both" else [backend_name]
    for name in backends:
        _make_llm_client(name)  # fail fast on a missing CLI

    run_args = copy.copy(args)
    run_args.fresh = True
    run_args.no_checkpoint = True

    def _one(name: str) -> BackendRun:
        try:
            report, elapsed, stats = _execute_pipeline(run_args, copy.deepcopy(ticket), workspace, name, None)
            return BackendRun(name, report, elapsed, stats)
        except Exception as exc:
            print(f"[repeat] {name} failed: {exc}", file=sys.stderr)
            return BackendRun(name, None, 0.0, [], error=f"{type(exc).__name__}: {exc}")

    runs_by_backend: dict[str, list[BackendRun]] = {name: [] for name in backends}
    for i in range(1, args.repeat + 1):
        print(f"[repeat] Run {i}/{args.repeat}...")
        if args.parallel and len(backends) > 1:
            with ThreadPoolExecutor(max_workers=len(backends)) as pool:
                results = list(pool.map(_one, backends))
        else:
            results = [_one(name) for name in backends]
        for run in results:
            runs_by_backend[run.backend].append(run)
            if args.out and run.report is not None:
                label = f".{run.backend}" if len(backends) > 1 else ""
                out = Path(args.out)
                path = out.with_name(f"{out.stem}{label}.run{i}{out.suffix}")
                path.write_text(render_triage_report(run.report))
            if run.report is not None:
                print(f"[repeat] run {i} {run.backend}: verdict={run.report.verdict}, confidence={run.report.confidence:.0%}")

    summary_md = render_repeat_summary(ticket.ticket_id, runs_by_backend)
    summary_out = str(Path(args.out).with_name(f"{Path(args.out).stem}.repeat-summary.md")) if args.out else None
    _emit(summary_md, summary_out)
    if args.json:
        print(json.dumps(build_repeat_summary(runs_by_backend), indent=2))
    print(f"\n[PIPELINE SUMMARY] Total end-to-end time: {time.monotonic() - start:.1f}s")
    return 0 if all(r.report is not None for runs in runs_by_backend.values() for r in runs) else 1


def _run_diagnostic(args) -> int:
    pipeline_start_time = time.monotonic()

    ticket = _load_ticket(
        args,
        enable_attachments=args.enable_attachments,
        include_custom_fields=not args.no_custom_fields,
    )

    if args.demo:
        workspace = parse_repo_specs([str(DEMO_REPO_PATH)])
    elif args.repo:
        try:
            workspace = parse_repo_specs(args.repo)
        except WorkspaceError as exc:
            raise CliError(str(exc)) from exc
    else:
        raise CliError("--repo PATH is required (Stage 2 needs a repository to search).")

    backend_name = "stub" if args.demo else args.backend
    if args.repeat < 1:
        raise CliError("--repeat must be at least 1.")
    if args.repeat > 1:
        return _run_repeat(args, ticket, workspace, backend_name, pipeline_start_time)
    if backend_name == "both":
        return _run_comparison(args, ticket, workspace, pipeline_start_time)

    checkpoint_path = _checkpoint_for(args, ticket, backend_name, per_backend=False)
    report, _, _ = _execute_pipeline(args, ticket, workspace, backend_name, checkpoint_path)
    pipeline_total_time = time.monotonic() - pipeline_start_time

    _emit(render_triage_report(report), args.out)

    if args.json:
        print(json.dumps(asdict(report), indent=2))

    # Print total pipeline timing summary
    print(f"\n{'='*60}")
    print(f"[PIPELINE SUMMARY] Total end-to-end time: {pipeline_total_time:.1f}s")
    print(f"{'='*60}")

    return 0


def _run_comparison(args, ticket: NormalizedTicket, workspace: Workspace, pipeline_start_time: float) -> int:
    """Run every harness in COMPARE_BACKENDS on the same ticket and write a
    per-backend report plus a side-by-side comparison. Each backend gets its
    own deep copy of the ticket and its own checkpoint file."""
    # Fail fast if a harness is missing, before spending any time on the other.
    for name in COMPARE_BACKENDS:
        _make_llm_client(name)

    def _one(name: str) -> BackendRun:
        try:
            report, elapsed, stats = _execute_pipeline(
                args,
                copy.deepcopy(ticket),
                workspace,
                name,
                _checkpoint_for(args, ticket, name, per_backend=True),
            )
            return BackendRun(name, report, elapsed, stats)
        except Exception as exc:  # one harness failing must not discard the other's result
            print(f"[compare] {name} failed: {exc}", file=sys.stderr)
            return BackendRun(name, None, 0.0, [], error=f"{type(exc).__name__}: {exc}")

    if args.parallel:
        with ThreadPoolExecutor(max_workers=len(COMPARE_BACKENDS)) as pool:
            runs = list(pool.map(_one, COMPARE_BACKENDS))
    else:
        runs = [_one(name) for name in COMPARE_BACKENDS]

    for run in runs:
        if run.report is None:
            continue
        print(f"\n{'#'*60}\n# Backend: {run.backend}\n{'#'*60}")
        _emit(render_triage_report(run.report), _backend_path(args.out, run.backend) if args.out else None)
        if args.json:
            print(json.dumps(asdict(run.report), indent=2))

    comparison_md = render_comparison(ticket.ticket_id, runs)
    comparison_out = str(Path(args.out).with_name(f"{Path(args.out).stem}.comparison.md")) if args.out else None
    print(f"\n{'#'*60}\n# Comparison\n{'#'*60}")
    _emit(comparison_md, comparison_out)
    if args.json:
        print(json.dumps(build_comparison(runs), indent=2))

    print(f"\n[PIPELINE SUMMARY] Total end-to-end time: {time.monotonic() - pipeline_start_time:.1f}s")
    return 0 if all(r.report is not None for r in runs) else 1


def _run_verify_fix(args) -> int:
    if not args.ticket_id:
        raise CliError("--verify-fix requires --ticket-id KEY.")
    if not args.repo:
        raise CliError("--verify-fix requires --repo PATH.")
    if len(args.repo) > 1:
        raise CliError("--verify-fix supports a single --repo; multi-repo verification is not implemented yet.")
    verify_repo = args.repo[0].partition("=")[2] or args.repo[0]
    if not args.base:
        raise CliError("--verify-fix requires --base REF.")

    if args.backend == "both":
        raise CliError("--verify-fix does not support --backend both; pick copilot or claude.")

    checkpoint_path = Path(args.checkpoint or _default_checkpoint_path(args.ticket_id, args.backend))
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
        git_tool = GitDiffTool(verify_repo)
        print(f"[stage4] Computing git diff against {args.base}...")
        diff_result = git_tool.diff(args.base, paths=args.paths)
    except (ValueError, RuntimeError) as exc:
        raise CliError(str(exc)) from exc

    backend_name = args.backend
    llm_client = _make_llm_client(backend_name, verbose=args.verbose)
    model = BACKEND_MODELS[backend_name]["strong"]

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
            try:
                load_config_file(args.config)
            except ConfigError as exc:
                raise CliError(str(exc)) from exc
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
