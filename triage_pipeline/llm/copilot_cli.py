"""Second LLM backend (§7): shells out to an already-authenticated local
``copilot`` CLI in non-interactive mode, so a user with a GitHub Copilot
subscription (rather than metered per-token API credits) can run the whole
pipeline.

Trade-off, documented per spec: no per-token billing, but each
``structured_call`` spawns a fresh ``copilot`` process, so there is no
cross-call prompt caching the way a direct Anthropic Messages API client
could get via ``cache_control``.

Structured-output approximation: the Copilot CLI has no API for forcing a
custom tool call with an arbitrary JSON schema. Instead, we instruct the
model (in the system prompt) to call its own built-in read-only tools as
needed, then emit its final answer as a single fenced ```json block
matching the given schema, and nothing else. We parse that fenced block
from the final assistant message. If parsing fails, we retry once with a
stricter corrective follow-up instruction before giving up.

Tool-turn budget: the CLI does not expose a raw "max tool turns" flag, so we
approximate the same contract by streaming ``--output-format json`` events
and counting ``assistant.turn_start`` events ourselves; once the configured
``max_tool_turns`` is exceeded without a final answer, we terminate the
subprocess and raise ``ToolBudgetExhausted`` -- exactly like the Anthropic
backend would -- so this enforces the SAME budget contract across backends
(§7's mandatory requirement), not an unconfigurable, backend-specific limit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys as _sys
import threading
import time
from typing import Optional

from .base import LLMClient, ToolBudgetExhausted, ToolExecutor
from .cli_common import RETRY_SUFFIX, build_prompt, extract_json

_PROCESS_TIMEOUT_SECONDS = 600


class CopilotCLIClient(LLMClient):
    def __init__(self, copilot_binary: str = "copilot", verbose: bool = False):
        if shutil.which(copilot_binary) is None:
            raise RuntimeError(
                f"'{copilot_binary}' was not found on PATH. Install/auth the GitHub "
                "Copilot CLI, or pick a different --backend."
            )
        self.copilot_binary = copilot_binary
        # When True, stream a human-readable line per tool call/turn to stderr
        # as the CLI's underlying session progresses, instead of staying silent
        # until the whole structured_call returns.
        self.verbose = verbose
        # One dict per structured_call (turns, tool_calls, retries, seconds),
        # used by the side-by-side comparison report.
        self.stats: list[dict] = []

    def structured_call(
        self,
        stage: str,
        model: str,
        system_prompt: str,
        user_content: str,
        output_schema: dict,
        tools: Optional[list[dict]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        max_tool_turns: int = 12,
        cwd: Optional[str] = None,
        extra_dirs: Optional[list[str]] = None,
    ) -> dict:
        prompt = build_prompt(system_prompt, user_content, output_schema)

        t0 = time.monotonic()
        stat = {
            "stage": stage, "model": model, "turns": 0, "tool_calls": 0, "retries": 0,
            "seconds": 0.0, "input_tokens": 0, "output_tokens": 0,
            "premium_requests": 0.0, "nano_aiu": 0,
        }
        self.stats.append(stat)
        try:
            result = self._run_copilot(prompt, model, cwd, max_tool_turns, stage, stat, extra_dirs)
            parsed = extract_json(result)
            if parsed is not None:
                return parsed

            # One corrective retry: the model answered but didn't follow the fence
            # convention. Ask it to reformat, in the same (fresh) process.
            stat["retries"] += 1
            result = self._run_copilot(prompt + RETRY_SUFFIX, model, cwd, max_tool_turns, stage, stat, extra_dirs)
            parsed = extract_json(result)
            if parsed is not None:
                return parsed
        finally:
            stat["seconds"] = round(time.monotonic() - t0, 2)
        raise ValueError(
            f"[{stage}] CopilotCLIClient could not extract a valid JSON result from "
            "the CLI's response after one retry."
        )

    def _run_copilot(
        self,
        prompt: str,
        model: str,
        cwd: Optional[str],
        max_tool_turns: int,
        stage: str = "",
        stat: Optional[dict] = None,
        extra_dirs: Optional[list[str]] = None,
    ) -> str:
        args = [
            self.copilot_binary,
            "-p",
            prompt,
            "--allow-all-tools",
            "--deny-tool",
            "write",
            "--deny-tool",
            "shell",
            "--output-format",
            "json",
            "--log-level",
            "none",
        ]
        if model:
            args += ["--model", model]
        if cwd:
            args += ["-C", cwd]
        for extra in extra_dirs or []:
            args += ["--add-dir", extra]

        turn_ids: set[str] = set()
        final_text_parts: list[str] = []
        budget_exceeded = threading.Event()
        usage_snapshot: dict = {}

        def _reader(proc: subprocess.Popen):
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "assistant.turn_start":
                        turn_ids.add(event.get("data", {}).get("turnId"))
                        if self.verbose:
                            print(f"  [{stage}] turn {len(turn_ids)}/{max_tool_turns}...", file=_sys.stderr)
                        if len(turn_ids) > max_tool_turns:
                            budget_exceeded.set()
                            proc.terminate()
                            return
                    elif etype == "tool.execution_start":
                        if stat is not None:
                            stat["tool_calls"] += 1
                        if not self.verbose:
                            continue
                        data = event.get("data", {})
                        tool_name = data.get("toolName", "?")
                        tool_args = data.get("arguments", {})
                        print(f"  [{stage}] tool call: {tool_name}({tool_args})", file=_sys.stderr)
                    elif etype == "assistant.message":
                        content = event.get("data", {}).get("content")
                        if isinstance(content, str):
                            final_text_parts.append(content)
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and "text" in block:
                                    final_text_parts.append(block["text"])
                    elif etype == "session.usage_checkpoint":
                        # Cumulative for this (fresh) session; keep the latest.
                        usage_snapshot.clear()
                        usage_snapshot.update(event.get("data", {}).get("accountingSnapshot") or {})
            except Exception:
                pass

        # Using Popen as a context manager ensures stdout/stderr pipe handles
        # are always closed on exit (normal, timeout, or terminate), avoiding
        # the "unclosed file" ResourceWarning a previous version of this
        # backend left behind. stderr is discarded (DEVNULL): the CLI is run
        # with --log-level none and we only need stdout's JSON event stream.
        with subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        ) as proc:
            reader_thread = threading.Thread(target=_reader, args=(proc,), daemon=True)
            reader_thread.start()
            try:
                proc.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.terminate()
                reader_thread.join(timeout=5)
                raise ToolBudgetExhausted(
                    "copilot CLI process timed out without producing a result", len(turn_ids)
                )
            reader_thread.join(timeout=5)

        if stat is not None:
            self._record_usage(stat, usage_snapshot)

        if budget_exceeded.is_set():
            raise ToolBudgetExhausted(
                f"Exceeded max_tool_turns={max_tool_turns} without a final result",
                len(turn_ids),
            )

        if stat is not None:
            stat["turns"] += len(turn_ids)
        return "\n".join(final_text_parts)

    @staticmethod
    def _record_usage(stat: dict, snapshot: dict) -> None:
        """Add one session's usage (from ``session.usage_checkpoint``) to
        ``stat``. ``input_tokens`` includes cache reads/writes, matching how the
        Claude backend reports it. Missing fields are treated as zero."""
        for metrics in (snapshot.get("modelMetrics") or {}).values():
            usage = metrics.get("usage") or {}
            stat["input_tokens"] += int(usage.get("inputTokens") or 0)
            stat["output_tokens"] += int(usage.get("outputTokens") or 0)
        stat["premium_requests"] = round(
            stat["premium_requests"] + float(snapshot.get("totalPremiumRequestCost") or 0.0), 4
        )
        stat["nano_aiu"] += int(snapshot.get("totalNanoAiu") or 0)
