"""Harness backends (copilot / claude) and the side-by-side comparison mode."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from triage_pipeline import main as cli
from triage_pipeline.adapters.rovo import RovoExportAdapter
from triage_pipeline.compare import BackendRun, build_comparison, render_comparison
from triage_pipeline.demo import demo_stub_client
from triage_pipeline.llm.base import ToolBudgetExhausted
from triage_pipeline.llm.claude_cli import ClaudeCLIClient
from triage_pipeline.llm.copilot_cli import CopilotCLIClient
from triage_pipeline.orchestrator import run_pipeline

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}}


def _fake_binary(tmp_path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


CLAUDE_OK = r'''
import json, sys
open(__import__("os").environ["ARGS_OUT"], "w").write(json.dumps(sys.argv[1:]))
print(json.dumps({"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use", "name": "Read", "input": {}}]}}))
print(json.dumps({"type": "assistant", "message": {"id": "m2", "content": [{"type": "text", "text": "```json\n{\"x\": 1}\n```"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "result": "```json\n{\"x\": 1}\n```", "total_cost_usd": 0.01,
                  "usage": {"input_tokens": 3, "cache_read_input_tokens": 7, "output_tokens": 5}}))
'''

CLAUDE_MAX_TURNS = r'''
import json
print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": None}))
'''

COPILOT_OK = r'''
import json, sys
open(__import__("os").environ["ARGS_OUT"], "w").write(json.dumps(sys.argv[1:]))
print(json.dumps({"type": "assistant.turn_start", "data": {"turnId": "0"}}))
print(json.dumps({"type": "assistant.message", "data": {"content": "```json\n{\"x\": 2}\n```"}}))
'''


def test_claude_client_parses_result_and_records_stats(tmp_path, monkeypatch):
    args_out = tmp_path / "args.json"
    monkeypatch.setenv("ARGS_OUT", str(args_out))
    client = ClaudeCLIClient(claude_binary=_fake_binary(tmp_path, "claude", CLAUDE_OK))

    result = client.structured_call("stage1_classify", "claude-haiku-4-5", "SYS", "USER", SCHEMA,
                                    max_tool_turns=7, cwd=str(tmp_path))

    assert result == {"x": 1}
    argv = json.loads(args_out.read_text())
    assert argv[argv.index("--max-turns") + 1] == "7"
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    stat_ = client.stats[0]
    assert (stat_["turns"], stat_["tool_calls"], stat_["retries"]) == (2, 1, 0)
    assert stat_["cost_usd"] == 0.01
    assert stat_["input_tokens"] == 10 and stat_["output_tokens"] == 5


COPILOT_USAGE = r'''
import json
print(json.dumps({"type": "assistant.turn_start", "data": {"turnId": "0"}}))
print(json.dumps({"type": "assistant.message", "data": {"content": "```json\n{\"x\": 3}\n```"}}))
print(json.dumps({"type": "session.usage_checkpoint", "data": {"accountingSnapshot": {
    "totalPremiumRequestCost": 0.33, "totalNanoAiu": 900,
    "modelMetrics": {"claude-haiku-4.5": {"usage": {"inputTokens": 32057, "outputTokens": 36}}}}}}))
'''


def test_copilot_client_records_usage(tmp_path):
    client = CopilotCLIClient(copilot_binary=_fake_binary(tmp_path, "copilot", COPILOT_USAGE))
    assert client.structured_call("s", "m", "SYS", "USER", SCHEMA) == {"x": 3}
    st = client.stats[0]
    assert st["input_tokens"] == 32057 and st["output_tokens"] == 36
    assert st["premium_requests"] == 0.33 and st["nano_aiu"] == 900


def test_claude_max_turns_raises_tool_budget_exhausted(tmp_path):
    client = ClaudeCLIClient(claude_binary=_fake_binary(tmp_path, "claude", CLAUDE_MAX_TURNS))
    with pytest.raises(ToolBudgetExhausted):
        client.structured_call("stage2_investigate", "m", "SYS", "USER", SCHEMA, max_tool_turns=1)


def test_missing_claude_binary_is_clear_error():
    with pytest.raises(RuntimeError, match="Claude Code"):
        ClaudeCLIClient(claude_binary="definitely-not-a-real-binary")


def test_both_harnesses_receive_identical_prompt(tmp_path, monkeypatch):
    claude_out, copilot_out = tmp_path / "c.json", tmp_path / "p.json"
    claude = ClaudeCLIClient(claude_binary=_fake_binary(tmp_path, "claude", CLAUDE_OK))
    copilot = CopilotCLIClient(copilot_binary=_fake_binary(tmp_path, "copilot", COPILOT_OK))

    monkeypatch.setenv("ARGS_OUT", str(claude_out))
    claude.structured_call("s", "m", "SYSTEM PROMPT", "USER CONTENT", SCHEMA)
    monkeypatch.setenv("ARGS_OUT", str(copilot_out))
    assert copilot.structured_call("s", "m", "SYSTEM PROMPT", "USER CONTENT", SCHEMA) == {"x": 2}

    claude_prompt = json.loads(claude_out.read_text())[json.loads(claude_out.read_text()).index("-p") + 1]
    copilot_prompt = json.loads(copilot_out.read_text())[json.loads(copilot_out.read_text()).index("-p") + 1]
    assert claude_prompt == copilot_prompt
    assert copilot.stats[0]["turns"] == 1


def test_backend_models_use_same_models_per_harness():
    assert cli.BACKEND_MODELS["copilot"] == {"cheap": "claude-haiku-4.5", "strong": "claude-sonnet-5.5"}
    assert cli.BACKEND_MODELS["claude"] == {"cheap": "claude-haiku-4-5", "strong": "claude-sonnet-5-5"}


def test_checkpoint_paths_are_per_backend():
    assert cli._default_checkpoint_path("T-1", "copilot") == str(Path(".triage_checkpoints") / "T-1.json")
    assert cli._default_checkpoint_path("T-1", "claude") == str(Path(".triage_checkpoints") / "T-1.claude.json")
    assert cli._backend_path("out/cp.json", "claude") == str(Path("out") / "cp.claude.json")


def test_verify_fix_rejects_both(capsys):
    rc = cli.main(["--verify-fix", "--ticket-id", "T-1", "--repo", ".", "--base", "main", "--backend", "both"])
    assert rc == 1
    assert "both" in capsys.readouterr().err


def _demo_report():
    ticket = RovoExportAdapter(str(PACKAGE_ROOT / "sample_data" / "demo_ticket.md")).fetch()
    return ticket, run_pipeline(demo_stub_client(), ticket, str(PACKAGE_ROOT / "sample_data" / "demo_repo"))


def test_render_comparison_shows_both_backends():
    _, report = _demo_report()
    stats = [{"stage": "stage1_classify", "turns": 2, "tool_calls": 1, "retries": 0, "seconds": 1.5,
              "cost_usd": 0.02, "input_tokens": 10, "output_tokens": 5}]
    runs = [BackendRun("copilot", report, 3.0, stats), BackendRun("claude", report, 2.0, stats)]
    md = render_comparison("DEMO-101", runs)
    assert "copilot" in md and "claude" in md and "stage1_classify" in md
    assert build_comparison(runs)["agreement"]["verdict"] is True
    # Copilot reports no cost/tokens; Claude does.
    summaries = {s["backend"]: s for s in build_comparison(runs)["runs"]}
    assert summaries["copilot"]["cost_usd"] is None and summaries["claude"]["cost_usd"] == 0.02
    assert summaries["claude"]["premium_requests"] is None
    assert summaries["copilot"]["input_tokens"] == 10 and summaries["claude"]["input_tokens"] == 10


@pytest.mark.parametrize("parallel", [False, True])
def test_backend_both_runs_each_harness_and_writes_outputs(tmp_path, monkeypatch, parallel):
    ticket, report = _demo_report()
    used = []

    def fake_execute(args, tkt, repo_root, backend_name, checkpoint_path):
        used.append((backend_name, checkpoint_path))
        if backend_name == "claude":
            raise RuntimeError("boom")
        return report, 1.0, [{"stage": "s", "turns": 1, "tool_calls": 0, "retries": 0, "seconds": 1.0}]

    monkeypatch.setattr(cli, "_execute_pipeline", fake_execute)
    monkeypatch.setattr(cli, "_make_llm_client", lambda *a, **k: object())
    out = tmp_path / "r.md"
    argv = ["--source", "rovo", "--file", str(PACKAGE_ROOT / "sample_data" / "demo_ticket.md"),
            "--repo", str(tmp_path), "--backend", "both", "--out", str(out)]
    rc = cli.main(argv + (["--parallel"] if parallel else []))

    assert rc == 1  # one harness failed
    assert sorted(b for b, _ in used) == ["claude", "copilot"]
    assert (tmp_path / "r.copilot.md").exists()
    comparison = (tmp_path / "r.comparison.md").read_text()
    assert "boom" in comparison


def test_agreement_is_na_when_neither_run_has_a_value():
    _, report = _demo_report()
    empty = type(report)(**{**report.__dict__, "affected_areas": [], "story_points": None})
    ag = build_comparison([BackendRun("copilot", empty, 1.0), BackendRun("claude", empty, 1.0)])["agreement"]
    assert ag["top_location"] is None and ag["story_points"] is None and ag["verdict"] is True
    assert "n/a (neither run produced a value)" in render_comparison("T", [
        BackendRun("copilot", empty, 1.0), BackendRun("claude", empty, 1.0)])


def test_repeat_summarises_verdict_flips_and_writes_files(tmp_path, monkeypatch):
    _, report = _demo_report()
    flipped = type(report)(**{**report.__dict__, "verdict": "insufficient_info", "confidence": 0.5})
    calls = {"copilot": 0, "claude": 0}
    seen_args = []

    def fake_execute(args, tkt, repo_root, backend_name, checkpoint_path):
        seen_args.append((args.fresh, args.no_checkpoint, checkpoint_path))
        calls[backend_name] += 1
        r = flipped if (backend_name == "copilot" and calls[backend_name] == 2) else report
        return r, 2.0, [{"stage": "s", "turns": 1, "tool_calls": 1, "retries": 0, "seconds": 2.0,
                         "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.5, "premium_requests": 0.33}]

    monkeypatch.setattr(cli, "_execute_pipeline", fake_execute)
    monkeypatch.setattr(cli, "_make_llm_client", lambda *a, **k: object())
    out = tmp_path / "r.md"
    rc = cli.main(["--source", "rovo", "--file", str(PACKAGE_ROOT / "sample_data" / "demo_ticket.md"),
                   "--repo", str(tmp_path), "--backend", "both", "--repeat", "3", "--out", str(out)])

    assert rc == 0
    assert calls == {"copilot": 3, "claude": 3}
    assert all(fresh and no_cp and cp is None for fresh, no_cp, cp in seen_args)
    for backend in ("copilot", "claude"):
        for i in (1, 2, 3):
            assert (tmp_path / f"r.{backend}.run{i}.md").exists()
    summary = (tmp_path / "r.repeat-summary.md").read_text()
    assert "3 runs per backend" in summary
    assert "insufficient_info x1" in summary and "YES" in summary
    assert "Same verdict in 2 of 3 paired runs" in summary


def test_repeat_must_be_positive():
    assert cli.main(["--demo", "--repeat", "0"]) == 1
