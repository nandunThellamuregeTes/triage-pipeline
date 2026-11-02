"""Claude Code CLI backend: shells out to an already-authenticated local
``claude`` CLI in non-interactive (``-p``) mode.

It uses the same prompt (``cli_common.build_prompt``) and the same
fenced-JSON extraction + single corrective retry as ``CopilotCLIClient``, so
the only thing that differs between the two backends is the harness itself.

Mapping to the shared contract:

* Read-only posture: ``--tools Read,Grep,Glob`` removes every other built-in
  tool (Bash/Edit/Write never exist in the session). ``--permission-mode
  bypassPermissions`` is then safe, and is needed so ``Read`` can open absolute
  paths outside ``cwd`` (e.g. Stage 0 image temp files).
* Tool-turn budget: ``--max-turns`` is enforced by the CLI; a run that ends
  with ``subtype == "error_max_turns"`` raises ``ToolBudgetExhausted``.
  Assistant turns are also counted from the stream for stats/verbose output.
* Working directory: Claude Code has no ``-C`` flag, so ``cwd`` is applied to
  the subprocess.
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
_ALLOWED_TOOLS = "Read,Grep,Glob"


class ClaudeCLIClient(LLMClient):
    def __init__(self, claude_binary: str = "claude", verbose: bool = False):
        if shutil.which(claude_binary) is None:
            raise RuntimeError(
                f"'{claude_binary}' was not found on PATH. Install/auth the Claude "
                "Code CLI, or pick a different --backend."
            )
        self.claude_binary = claude_binary
        self.verbose = verbose
        # One dict per structured_call, used by the side-by-side comparison report.
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
            "seconds": 0.0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
        }
        self.stats.append(stat)
        try:
            result = self._run_claude(prompt, model, cwd, max_tool_turns, stage, stat, extra_dirs)
            parsed = extract_json(result)
            if parsed is not None:
                return parsed

            stat["retries"] += 1
            result = self._run_claude(prompt + RETRY_SUFFIX, model, cwd, max_tool_turns, stage, stat, extra_dirs)
            parsed = extract_json(result)
            if parsed is not None:
                return parsed
        finally:
            stat["seconds"] = round(time.monotonic() - t0, 2)
        raise ValueError(
            f"[{stage}] ClaudeCLIClient could not extract a valid JSON result from "
            "the CLI's response after one retry."
        )

    def _run_claude(
        self,
        prompt: str,
        model: str,
        cwd: Optional[str],
        max_tool_turns: int,
        stage: str,
        stat: dict,
        extra_dirs: Optional[list[str]] = None,
    ) -> str:
        args = [
            self.claude_binary,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            _ALLOWED_TOOLS,
            "--permission-mode",
            "bypassPermissions",
            "--no-session-persistence",
            "--max-turns",
            str(max_tool_turns),
        ]
        if model:
            args += ["--model", model]
        for extra in extra_dirs or []:
            args += [f"--add-dir={extra}"]

        message_ids: set[str] = set()
        last_text: list[str] = []
        final: dict = {}

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
                    if etype == "assistant":
                        message = event.get("message", {})
                        mid = message.get("id")
                        if mid and mid not in message_ids:
                            message_ids.add(mid)
                            if self.verbose:
                                print(f"  [{stage}] turn {len(message_ids)}/{max_tool_turns}...", file=_sys.stderr)
                        for block in message.get("content") or []:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_use":
                                stat["tool_calls"] += 1
                                if self.verbose:
                                    print(
                                        f"  [{stage}] tool call: {block.get('name')}({block.get('input')})",
                                        file=_sys.stderr,
                                    )
                            elif block.get("type") == "text":
                                last_text.append(block.get("text", ""))
                    elif etype == "result":
                        final.update(event)
            except Exception:
                pass

        with subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=cwd
        ) as proc:
            reader_thread = threading.Thread(target=_reader, args=(proc,), daemon=True)
            reader_thread.start()
            try:
                proc.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.terminate()
                reader_thread.join(timeout=5)
                raise ToolBudgetExhausted(
                    "claude CLI process timed out without producing a result", len(message_ids)
                )
            reader_thread.join(timeout=5)

        stat["turns"] += len(message_ids)
        usage = final.get("usage") or {}
        stat["cost_usd"] = round(stat["cost_usd"] + float(final.get("total_cost_usd") or 0.0), 6)
        stat["input_tokens"] += int(usage.get("input_tokens") or 0) + int(
            usage.get("cache_read_input_tokens") or 0
        ) + int(usage.get("cache_creation_input_tokens") or 0)
        stat["output_tokens"] += int(usage.get("output_tokens") or 0)

        if final.get("subtype") == "error_max_turns":
            raise ToolBudgetExhausted(
                f"Exceeded max_tool_turns={max_tool_turns} without a final result",
                len(message_ids),
            )

        result_text = final.get("result")
        if final.get("is_error"):
            raise RuntimeError(
                f"[{stage}] claude CLI reported an error: {result_text or final.get('subtype')}. "
                "If this is an authentication error, run `claude` interactively and `/login`."
            )
        if isinstance(result_text, str) and result_text:
            return result_text
        return last_text[-1] if last_text else ""
