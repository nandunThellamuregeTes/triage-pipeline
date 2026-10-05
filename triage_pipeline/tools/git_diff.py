"""Deterministic git diff tool (§9).

Stage 4 (Verify Fix) never reconstructs a diff itself -- this tool computes
"what changed" in plain Python/git, and the LLM only judges the meaning of
the diff it's handed.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_DIFF_CHARS = 60_000  # documented truncation threshold (§9)


@dataclass
class DiffResult:
    text: str
    changed_files: list[str] = field(default_factory=list)
    truncated: bool = False


class GitDiffTool:
    """Deterministic ``git diff <base_ref>`` against the current working
    tree (including uncommitted changes), optionally scoped to paths."""

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        if not (self.repo_root / ".git").exists():
            raise ValueError(f"not a git repository: {repo_root}")

    def current_head(self) -> str:
        return self._git(["rev-parse", "HEAD"]).strip()

    def diff(self, base_ref: str, paths: list[str] | None = None) -> DiffResult:
        args = ["diff", base_ref]
        if paths:
            args += ["--", *paths]
        text = self._git(args)
        changed_files = self._git(["diff", "--name-only", base_ref] + (["--", *paths] if paths else [])).splitlines()
        changed_files = [f for f in changed_files if f.strip()]
        truncated = False
        if len(text) > MAX_DIFF_CHARS:
            text = (
                text[:MAX_DIFF_CHARS]
                + f"\n\n[... diff truncated at {MAX_DIFF_CHARS} characters. "
                "Re-run with --paths to scope down to specific files/directories ...]"
            )
            truncated = True
        return DiffResult(text=text, changed_files=changed_files, truncated=truncated)

    def _git(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"git command timed out: git {' '.join(args)}") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )
        return proc.stdout
