"""Thin LLMClient wrapper that scopes every call to a multi-repo workspace:
it grants the extra repository directories and appends a note to the system
prompt, so stages with many call sites don't each have to repeat that."""
from __future__ import annotations

from typing import Optional

from .base import LLMClient, ToolExecutor


class RepoScopedClient(LLMClient):
    def __init__(self, inner: LLMClient, extra_dirs: list[str], system_note: str):
        self.inner = inner
        self.extra_dirs = extra_dirs or None
        self.system_note = system_note

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
        dirs = extra_dirs or self.extra_dirs
        return self.inner.structured_call(
            stage=stage,
            model=model,
            system_prompt=system_prompt + self.system_note,
            user_content=user_content,
            output_schema=output_schema,
            tools=tools,
            tool_executor=tool_executor,
            max_tool_turns=max_tool_turns,
            cwd=cwd,
            **({"extra_dirs": dirs} if dirs else {}),
        )

    def __getattr__(self, name):
        return getattr(self.inner, name)
