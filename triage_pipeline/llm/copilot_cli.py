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
import re
import shutil
import subprocess
import threading
from typing import Optional

from .base import LLMClient, ToolBudgetExhausted, ToolExecutor

_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
_PROCESS_TIMEOUT_SECONDS = 600


class CopilotCLIClient(LLMClient):
    def __init__(self, copilot_binary: str = "copilot"):
        if shutil.which(copilot_binary) is None:
            raise RuntimeError(
                f"'{copilot_binary}' was not found on PATH. Install/auth the GitHub "
                "Copilot CLI, or pick a different --backend."
            )
        self.copilot_binary = copilot_binary

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
    ) -> dict:
        schema_str = json.dumps(output_schema, indent=2)
        prompt = (
            f"{system_prompt}\n\n"
            "---\n"
            f"{user_content}\n"
            "---\n\n"
            "You may use your available read-only tools (file viewing, code search) "
            "as needed to investigate, within reason. When you are done, respond with "
            "EXACTLY ONE fenced code block, language `json`, containing a single JSON "
            "object that strictly matches this schema and nothing else "
            "(no prose before or after the fence):\n\n"
            f"```json\n{schema_str}\n```\n"
        )

        result, turns_used = self._run_copilot(prompt, model, cwd, max_tool_turns)
        if turns_used is None:
            # Budget tracking unavailable (shouldn't normally happen) -- treat as fine.
            pass
        parsed = self._extract_json(result)
        if parsed is not None:
            return parsed

        # One corrective retry: the model answered but didn't follow the fence
        # convention. Ask it to reformat, in the same (fresh) process.
        retry_prompt = (
            prompt
            + "\n\nIMPORTANT: your previous response did not contain a single valid "
            "```json fenced block matching the schema. Respond again with ONLY that "
            "fenced JSON block."
        )
        result, _ = self._run_copilot(retry_prompt, model, cwd, max_tool_turns)
        parsed = self._extract_json(result)
        if parsed is not None:
            return parsed
        raise ValueError(
            f"[{stage}] CopilotCLIClient could not extract a valid JSON result from "
            "the CLI's response after one retry."
        )

    def _extract_json(self, text: str) -> Optional[dict]:
        match = _JSON_FENCE_RE.search(text)
        candidate = match.group(1) if match else text
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None

    def _run_copilot(
        self, prompt: str, model: str, cwd: Optional[str], max_tool_turns: int
    ) -> tuple[str, Optional[int]]:
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

        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        turn_ids: set[str] = set()
        final_text_parts: list[str] = []
        budget_exceeded = threading.Event()

        def _reader():
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
                        if len(turn_ids) > max_tool_turns:
                            budget_exceeded.set()
                            proc.terminate()
                            return
                    elif etype == "assistant.message":
                        content = event.get("data", {}).get("content")
                        if isinstance(content, str):
                            final_text_parts.append(content)
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and "text" in block:
                                    final_text_parts.append(block["text"])
            except Exception:
                pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
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

        if budget_exceeded.is_set():
            raise ToolBudgetExhausted(
                f"Exceeded max_tool_turns={max_tool_turns} without a final result",
                len(turn_ids),
            )

        return ("\n".join(final_text_parts), len(turn_ids))
