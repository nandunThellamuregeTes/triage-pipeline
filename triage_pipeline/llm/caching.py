"""Fingerprinted checkpoint cache (§7, mandatory per §0.4).

Wraps any ``LLMClient`` and persists each stage's structured output to a
per-ticket JSON file, so a rerun doesn't redo expensive stages. Every cached
entry records:

  * a content hash of the ``NormalizedTicket`` that produced it, and
  * for any stage that used ``cwd`` (touched a repo), the repo's
    ``git rev-parse HEAD`` at the time of that call.

On load these fingerprints are compared against current state. On any
mismatch the stale entry -- and everything downstream of it -- is ignored
with a printed warning; it is never silently reused. ``--fresh`` and
``--redo-from N`` are manual overrides layered on top of this automatic
check, not substitutes for it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .base import LLMClient, ToolExecutor

# Stage ordering, used to know what "downstream of stage N" and
# "--redo-from N" mean. Index matches the pipeline diagram in §1.
STAGE_ORDER = ["stage1_classify", "stage2_investigate", "stage3_writeup", "stage4_verify"]


def _stage_index(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return len(STAGE_ORDER)  # unknown stage name: treat as "last"


def _git_head(cwd: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def _heads(cwd: Optional[str], extra_dirs: Optional[list[str]]):
    """HEAD of the working repo; with extra repos, a dict keyed by path so a
    change to any searched repo invalidates the cache. Single-repo entries keep
    the original plain-string format."""
    if not cwd:
        return None
    if not extra_dirs:
        return _git_head(cwd)
    return {p: _git_head(p) for p in [cwd, *extra_dirs]}


class CachingLLMClient(LLMClient):
    def __init__(
        self,
        inner: LLMClient,
        checkpoint_path: str,
        ticket_fingerprint: str,
        fresh: bool = False,
        redo_from_stage: Optional[int] = None,  # 1-based stage number, per CLI --redo-from
    ):
        self.inner = inner
        self.checkpoint_path = Path(checkpoint_path)
        self.ticket_fingerprint = ticket_fingerprint
        self.fresh = fresh
        self.redo_from_index = (redo_from_stage - 1) if redo_from_stage else None
        self._data = self._load()

    def _load(self) -> dict:
        if self.fresh or not self.checkpoint_path.exists():
            return {"stages": {}}
        try:
            return json.loads(self.checkpoint_path.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"[checkpoint] Warning: could not read {self.checkpoint_path}, starting fresh.")
            return {"stages": {}}

    def _save(self) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(json.dumps(self._data, indent=2))

    def _invalidate_from(self, stage_index: int) -> None:
        for name in STAGE_ORDER[stage_index:]:
            self._data["stages"].pop(name, None)

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
        idx = _stage_index(stage)
        forced_redo = self.redo_from_index is not None and idx >= self.redo_from_index

        if not self.fresh and not forced_redo:
            entry = self._data.get("stages", {}).get(stage)
            if entry is not None:
                stale_reason = self._stale_reason(entry, cwd, extra_dirs)
                if stale_reason is None:
                    print(f"[checkpoint] Reusing cached {stage} result.")
                    return entry["output"]
                print(
                    f"[checkpoint] Cache for {stage} is stale ({stale_reason}); "
                    "recomputing this stage and everything downstream."
                )
                self._invalidate_from(idx)
                self._save()

        result = self.inner.structured_call(
            stage=stage,
            model=model,
            system_prompt=system_prompt,
            user_content=user_content,
            output_schema=output_schema,
            tools=tools,
            tool_executor=tool_executor,
            max_tool_turns=max_tool_turns,
            cwd=cwd,
            **({"extra_dirs": extra_dirs} if extra_dirs else {}),
        )

        entry = {
            "output": result,
            "ticket_fingerprint": self.ticket_fingerprint,
            "repo_head": _heads(cwd, extra_dirs),
        }
        self._data.setdefault("stages", {})[stage] = entry
        self._save()
        print(f"[checkpoint] Computed and cached {stage} result.")
        return result

    def _stale_reason(
        self, entry: dict, cwd: Optional[str], extra_dirs: Optional[list[str]] = None
    ) -> Optional[str]:
        if entry.get("ticket_fingerprint") != self.ticket_fingerprint:
            return "ticket content changed"
        if cwd:
            current_head = _heads(cwd, extra_dirs)
            if entry.get("repo_head") != current_head:
                return f"repo HEAD advanced ({entry.get('repo_head')} -> {current_head})"
        return None
