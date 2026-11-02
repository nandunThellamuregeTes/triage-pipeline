"""Side-by-side comparison of two harness runs of the same ticket."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .schemas import TriageReport


@dataclass
class BackendRun:
    backend: str
    report: Optional[TriageReport]
    elapsed_seconds: float
    stats: list[dict] = field(default_factory=list)
    error: Optional[str] = None


def _sum(stats: list[dict], key: str) -> float:
    return sum(float(s.get(key) or 0) for s in stats)


def _top_paths(report: Optional[TriageReport], n: int = 3) -> list[str]:
    if report is None:
        return []
    return [
        (f"{h.service}:" if h.service else "") + (f"{h.path}:{h.line}" if h.line else h.path)
        for h in report.affected_areas[:n]
    ]


def summarize(run: BackendRun) -> dict:
    r = run.report
    return {
        "backend": run.backend,
        "error": run.error,
        "elapsed_seconds": round(run.elapsed_seconds, 1),
        "llm_calls": len(run.stats),
        "turns": int(_sum(run.stats, "turns")),
        "tool_calls": int(_sum(run.stats, "tool_calls")),
        "json_retries": int(_sum(run.stats, "retries")),
        "input_tokens": int(_sum(run.stats, "input_tokens")),
        "output_tokens": int(_sum(run.stats, "output_tokens")),
        # Billing units differ: USD from the Claude CLI, premium requests and
        # AI credit units from the Copilot CLI.
        "cost_usd": round(_sum(run.stats, "cost_usd"), 4) if run.backend == "claude" else None,
        "premium_requests": round(_sum(run.stats, "premium_requests"), 2) if run.backend == "copilot" else None,
        "nano_aiu": int(_sum(run.stats, "nano_aiu")) if run.backend == "copilot" else None,
        "verdict": str(r.verdict.value if hasattr(r.verdict, "value") else r.verdict) if r else None,
        "validated_severity": (
            str(r.validated_severity.value if hasattr(r.validated_severity, "value") else r.validated_severity)
            if r and r.validated_severity is not None
            else None
        ),
        "confidence": r.confidence if r else None,
        "story_points": r.story_points if r else None,
        "needs_human_review": r.needs_human_review if r else None,
        "duplicate_of": r.duplicate_of if r else None,
        "top_paths": _top_paths(r),
    }


def _agree(a, b) -> Optional[bool]:
    """None when neither side has a value (nothing to agree or disagree on)."""
    if a is None and b is None:
        return None
    return a == b


def _first(paths: list[str]) -> Optional[str]:
    return paths[0] if paths else None


def build_comparison(runs: list[BackendRun]) -> dict:
    summaries = [summarize(r) for r in runs]
    agreement: dict = {}
    if len(summaries) == 2 and all(s["error"] is None for s in summaries):
        a, b = summaries
        agreement = {
            "verdict": _agree(a["verdict"], b["verdict"]),
            "validated_severity": _agree(a["validated_severity"], b["validated_severity"]),
            "story_points": _agree(a["story_points"], b["story_points"]),
            "top_location": _agree(_first(a["top_paths"]), _first(b["top_paths"])),
            "confidence_delta": round(abs((a["confidence"] or 0) - (b["confidence"] or 0)), 3),
        }
    return {"runs": summaries, "agreement": agreement}


def _cell(value, spec: str = "") -> str:
    if value is None:
        return "n/a"
    return format(value, spec)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def render_comparison(ticket_id: str, runs: list[BackendRun]) -> str:
    data = build_comparison(runs)
    sums = data["runs"]
    header = ["Metric"] + [s["backend"] for s in sums]
    lines = [f"# Harness comparison: {ticket_id}", "", "## Outcome", ""]

    outcome = [
        ("Error", "error", ""),
        ("Verdict", "verdict", ""),
        ("Validated severity", "validated_severity", ""),
        ("Confidence", "confidence", ".2f"),
        ("Story points", "story_points", ""),
        ("Needs human review", "needs_human_review", ""),
        ("Duplicate of", "duplicate_of", ""),
    ]
    rows = [[label] + [_cell(s[key], spec) for s in sums] for label, key, spec in outcome]
    rows.append(["Top locations"] + ["<br>".join(s["top_paths"]) or "n/a" for s in sums])
    lines += _table(header, rows)

    effort = [
        ("Wall-clock (s)", "elapsed_seconds", ".1f"),
        ("LLM calls", "llm_calls", ""),
        ("Turns", "turns", ""),
        ("Tool calls", "tool_calls", ""),
        ("JSON retries", "json_retries", ""),
        ("Input tokens (incl. cache)", "input_tokens", ""),
        ("Output tokens", "output_tokens", ""),
        ("Cost (USD)", "cost_usd", ".4f"),
        ("Premium requests", "premium_requests", ".2f"),
        ("Usage units (nano AIU)", "nano_aiu", ""),
    ]
    lines += ["", "## Cost and effort", ""]
    lines += _table(header, [[label] + [_cell(s[key], spec) for s in sums] for label, key, spec in effort])
    lines += [
        "",
        "_Tokens are reported by both CLIs. Billing differs: the Claude CLI reports USD, the Copilot "
        "CLI reports premium requests and AI credit units, so those rows are not comparable. "
        "Stages served from a checkpoint have no stats; use `--fresh` for a clean comparison._",
        "",
        "## Per-stage",
        "",
    ]
    stage_rows = [
        [s["stage"], run.backend, f"{s['seconds']:.1f}", str(s["turns"]), str(s["tool_calls"]), str(s["retries"])]
        for run in runs
        for s in run.stats
    ]
    lines += _table(["Stage", "Backend", "Seconds", "Turns", "Tool calls", "Retries"], stage_rows)

    ag = data["agreement"]
    if ag:
        def m(flag: Optional[bool]) -> str:
            if flag is None:
                return "n/a (neither run produced a value)"
            return "match" if flag else "DIFFERENT"

        lines += [
            "",
            "## Agreement",
            "",
            f"- Verdict: {m(ag['verdict'])}",
            f"- Validated severity: {m(ag['validated_severity'])}",
            f"- Story points: {m(ag['story_points'])}",
            f"- Top code location: {m(ag['top_location'])}",
            f"- Confidence delta: {ag['confidence_delta']}",
        ]
    return "\n".join(lines) + "\n"


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def build_repeat_summary(runs_by_backend: dict[str, list[BackendRun]]) -> dict:
    """Summarise N repeated runs per backend: verdict stability, spread and
    average effort. Failed runs are counted but excluded from the spread."""
    out: dict = {"backends": {}, "cross_harness": {}}
    for backend, runs in runs_by_backend.items():
        sums = [summarize(r) for r in runs]
        ok = [s for s in sums if s["error"] is None]

        def counts(key: str) -> dict:
            c: dict = {}
            for s in ok:
                v = s[key] if key != "top_location" else _first(s["top_paths"])
                c[str(v) if v is not None else "n/a"] = c.get(str(v) if v is not None else "n/a", 0) + 1
            return c

        confidences = [s["confidence"] for s in ok if s["confidence"] is not None]
        out["backends"][backend] = {
            "runs": len(sums),
            "failed": len(sums) - len(ok),
            "verdicts": counts("verdict"),
            "verdict_changed": len(counts("verdict")) > 1,
            "severities": counts("validated_severity"),
            "top_locations": counts("top_location"),
            "story_points": [s["story_points"] for s in ok],
            "confidence_min": min(confidences) if confidences else None,
            "confidence_max": max(confidences) if confidences else None,
            "confidence_mean": _mean(confidences),
            "mean_seconds": _mean([s["elapsed_seconds"] for s in ok]),
            "mean_turns": _mean([s["turns"] for s in ok]),
            "mean_tool_calls": _mean([s["tool_calls"] for s in ok]),
            "mean_input_tokens": _mean([s["input_tokens"] for s in ok]),
            "mean_output_tokens": _mean([s["output_tokens"] for s in ok]),
            "mean_cost_usd": _mean([s["cost_usd"] for s in ok if s["cost_usd"] is not None]),
            "mean_premium_requests": _mean(
                [s["premium_requests"] for s in ok if s["premium_requests"] is not None]
            ),
            "per_run_verdicts": [s["verdict"] or "failed" for s in sums],
        }

    names = list(runs_by_backend)
    if len(names) == 2:
        a_runs, b_runs = (runs_by_backend[n] for n in names)
        pairs = [
            (summarize(a), summarize(b))
            for a, b in zip(a_runs, b_runs)
            if a.report is not None and b.report is not None
        ]
        out["cross_harness"] = {
            "paired_runs": len(pairs),
            "verdict_agreement": sum(1 for a, b in pairs if a["verdict"] == b["verdict"]),
        }
    return out


def render_repeat_summary(ticket_id: str, runs_by_backend: dict[str, list[BackendRun]]) -> str:
    data = build_repeat_summary(runs_by_backend)
    names = list(data["backends"])
    b = data["backends"]
    n = max(v["runs"] for v in b.values())
    header = ["Metric"] + names

    def f(v, spec=""):
        return "n/a" if v is None else format(v, spec)

    def dist(d: dict) -> str:
        return ", ".join(f"{k} x{c}" for k, c in sorted(d.items(), key=lambda kv: -kv[1])) or "n/a"

    rows = [
        ["Runs (failed)"] + [f"{b[x]['runs']} ({b[x]['failed']})" for x in names],
        ["Verdicts"] + [dist(b[x]["verdicts"]) for x in names],
        ["Verdict changed between runs"] + ["YES" if b[x]["verdict_changed"] else "no" for x in names],
        ["Validated severity"] + [dist(b[x]["severities"]) for x in names],
        ["Confidence min / mean / max"]
        + [f"{f(b[x]['confidence_min'], '.2f')} / {f(b[x]['confidence_mean'], '.2f')} / {f(b[x]['confidence_max'], '.2f')}" for x in names],
        ["Story points"] + [", ".join(f(p) for p in b[x]["story_points"]) or "n/a" for x in names],
        ["Top location"] + [dist(b[x]["top_locations"]) for x in names],
        ["Mean wall-clock (s)"] + [f(b[x]["mean_seconds"], ".1f") for x in names],
        ["Mean turns"] + [f(b[x]["mean_turns"], ".1f") for x in names],
        ["Mean tool calls"] + [f(b[x]["mean_tool_calls"], ".1f") for x in names],
        ["Mean input tokens (incl. cache)"] + [f(b[x]["mean_input_tokens"], ".0f") for x in names],
        ["Mean output tokens"] + [f(b[x]["mean_output_tokens"], ".0f") for x in names],
        ["Mean cost (USD)"] + [f(b[x]["mean_cost_usd"], ".4f") for x in names],
        ["Mean premium requests"] + [f(b[x]["mean_premium_requests"], ".2f") for x in names],
    ]
    lines = [f"# Repeat summary: {ticket_id} ({n} runs per backend)", "", "## Stability", ""]
    lines += _table(header, rows)

    lines += ["", "## Verdict per run", ""]
    lines += _table(
        ["Run"] + names,
        [[str(i + 1)] + [b[x]["per_run_verdicts"][i] if i < len(b[x]["per_run_verdicts"]) else "-" for x in names] for i in range(n)],
    )

    cross = data["cross_harness"]
    if cross:
        lines += [
            "",
            "## Cross-harness",
            "",
            f"- Same verdict in {cross['verdict_agreement']} of {cross['paired_runs']} paired runs.",
        ]
    lines += [
        "",
        "_A backend whose verdict changes between identical runs is showing run-to-run variability "
        "(sampling), not a harness difference. Billing rows are only comparable within one backend._",
    ]
    return "\n".join(lines) + "\n"
