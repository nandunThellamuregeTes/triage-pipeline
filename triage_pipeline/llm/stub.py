"""Deterministic, offline, zero-cost LLM client (§7).

Driven by a fixture dict keyed by stage name, used for ``--demo`` and for
tests. A fixture entry may be:

  * a plain dict -- returned as-is (validated against the output schema's
    required top-level keys where practical), or
  * a callable ``(system_prompt, user_content, tool_executor) -> dict`` --
    used by stages that have tools, so the demo fixture can still drive a
    real tool-use loop against real ripgrep (this is what exercises Stage
    2's tool loop in the end-to-end demo test, §12).
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Union

from .base import LLMClient, ToolExecutor

FixtureValue = Union[dict, Callable[..., dict]]


class StubLLMClient(LLMClient):
    def __init__(self, fixtures: dict[str, FixtureValue]):
        self.fixtures = fixtures
        self.calls: list[dict] = []  # recorded for test assertions

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
        self.calls.append({"stage": stage, "model": model, "cwd": cwd})
        if stage not in self.fixtures:
            raise KeyError(f"StubLLMClient has no fixture registered for stage '{stage}'")
        fixture = self.fixtures[stage]
        if callable(fixture):
            return fixture(system_prompt, user_content, tool_executor)
        return fixture
