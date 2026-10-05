"""LLM client abstraction (§7).

Structured output is enforced via a forced tool call (a single
``emit_result`` tool whose input schema *is* the stage's schema) rather than
asking the model to "please return JSON" in prose -- this avoids
parse-failure retries. Backends that cannot literally force a tool call
(e.g. a CLI-shelled-out agent) approximate this by instructing the model to
emit exactly one call to the documented ``emit_result`` tool/function and
validating the result against the schema; see ``copilot_cli.py`` for the
documented trade-off.

Whichever max_tool_turns/turn-budget configuration exists applies uniformly
to every backend and every stage that uses tools (§7) -- this was a
confirmed defect in a prior version of this pipeline (the default backend
crashed after only 3 tool turns with no way to raise it from the CLI).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

DEFAULT_MAX_TOOL_TURNS = 12


@dataclass
class ToolCall:
    name: str
    arguments: dict


ToolExecutor = Callable[[ToolCall], Any]


class ToolBudgetExhausted(Exception):
    """Raised internally when a structured_call's tool-turn budget runs out
    before a result was produced. Callers (stages) MUST catch this and
    degrade gracefully (§0.7) -- it must never propagate out of a stage and
    abort the CLI invocation."""

    def __init__(self, message: str, turns_used: int):
        super().__init__(message)
        self.turns_used = turns_used


class LLMClient(ABC):
    @abstractmethod
    def structured_call(
        self,
        stage: str,
        model: str,
        system_prompt: str,
        user_content: str,
        output_schema: dict,
        tools: Optional[list[dict]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        max_tool_turns: int = DEFAULT_MAX_TOOL_TURNS,
        cwd: Optional[str] = None,
    ) -> dict:
        """Make one structured call for ``stage``.

        Returns a dict matching ``output_schema``. If ``tools`` is given,
        the model may call them via ``tool_executor`` for up to
        ``max_tool_turns`` turns before it must call ``emit_result``. If the
        budget is exhausted first, implementations raise
        ``ToolBudgetExhausted`` rather than any other exception, so callers
        can degrade predictably.
        """
        ...
