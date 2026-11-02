"""Read-only code search tools, scoped to a configured repo root (§9).

Kept as a narrow, swappable interface -- Stage 2 depends only on this public
interface, documented here as the planned swap point for a future
language-aware search tool.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EXCERPT_CONTEXT_LINES = 15
MAX_HITS_DEFAULT = 20

# Directories treated as "build/bundled output" -- searched only as a
# second tier, after source search returns nothing (§1.7).
BUILD_OUTPUT_DIR_NAMES = {"buildcache", "build", "dist", "out", "bundle", "bundles", ".output"}
BUILD_OUTPUT_GLOB_HINTS = ("*.bundle.js", "*.min.js", "*.bundle.css")


@dataclass
class SearchHit:
    path: str
    line: int
    snippet: str


@dataclass
class DirEntry:
    name: str
    is_dir: bool


class CodeSearchTool:
    """ripgrep-backed keyword/regex search, scoped to ``repo_root``.

    Never reads a whole file or the whole repo -- ``read_excerpt`` returns a
    bounded window only. Every lookup method returns an explicit "not
    found"/empty-but-labelled result rather than a bare empty list, so an
    agent can tell "searched, absent" from "didn't search" (§1.7).
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.is_dir():
            raise ValueError(f"repo root does not exist or is not a directory: {repo_root}")
        if shutil.which("rg") is None:
            raise RuntimeError(
                "ripgrep ('rg') was not found on PATH -- CodeSearchTool requires it"
            )

    def _rg_search(self, pattern: str, extra_args: list[str], max_hits: int) -> list[SearchHit]:
        try:
            proc = subprocess.run(
                ["rg", "--line-number", "--no-heading", "--max-count", "5", "--max-filesize", "1M"]
                + extra_args
                + ["-e", pattern, str(self.repo_root)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return []
        if proc.returncode not in (0, 1):  # 1 == "no matches", not an error
            return []
        hits: list[SearchHit] = []
        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            path, line_no, snippet = parts
            try:
                rel_path = str(Path(path).resolve().relative_to(self.repo_root))
            except ValueError:
                rel_path = path
            try:
                line_num = int(line_no)
            except ValueError:
                continue
            hits.append(SearchHit(path=rel_path, line=line_num, snippet=snippet.strip()))
            if len(hits) >= max_hits:
                break
        return hits

    def search(self, query: str, max_hits: int = MAX_HITS_DEFAULT) -> list[SearchHit]:
        """Keyword/regex search via ripgrep, scoped to the repo root.

        Returns ``[]`` when genuinely no matches were found anywhere
        searched -- callers (prompts) are instructed to treat an empty list
        here as "searched source, found nothing" and to then try
        ``grep_files(..., include_build_output=True)`` before reporting a
        symbol as undefined (§1.7)."""
        return self._rg_search(query, [], max_hits)

    def grep_files(
        self,
        pattern: str,
        glob: Optional[str] = None,
        include_build_output: bool = True,
        max_hits: int = MAX_HITS_DEFAULT,
    ) -> list[SearchHit]:
        """Regex search optionally scoped to a glob, with an explicit
        second tier over built/bundled output (§1.7) -- e.g. ``buildcache/``
        or ``*.bundle.js`` -- when ``include_build_output`` is True and
        ``glob`` doesn't already target one of those."""
        extra_args: list[str] = []
        if glob:
            extra_args += ["--glob", glob]
        hits = self._rg_search(pattern, extra_args, max_hits)
        if hits or not include_build_output:
            return hits
        # Second tier: search known build-output directories/patterns too.
        build_args: list[str] = []
        for dirname in BUILD_OUTPUT_DIR_NAMES:
            build_args += ["--glob", f"{dirname}/**"]
        for hint in BUILD_OUTPUT_GLOB_HINTS:
            build_args += ["--glob", f"**/{hint}"]
        return self._rg_search(pattern, build_args, max_hits)

    def list_dir(self, path: str = ".") -> list[DirEntry]:
        """List immediate entries of ``path`` (relative to repo root).
        Returns ``[]`` for a path that exists but is empty, and raises
        nothing for a missing path -- callers should check with
        ``read_excerpt``/``search`` style "[not found]" conventions; here we
        return a single-element sentinel list instead, since DirEntry has no
        string variant: see ``list_dir_or_error`` for a text-friendly form."""
        full_path = (self.repo_root / path).resolve()
        try:
            full_path.relative_to(self.repo_root)
        except ValueError:
            return []
        if not full_path.is_dir():
            return []
        entries = sorted(full_path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        return [DirEntry(name=p.name, is_dir=p.is_dir()) for p in entries]

    def list_dir_or_error(self, path: str = ".") -> list[dict]:
        """Tool-executor-friendly form of ``list_dir``: returns a list of
        ``{"name", "is_dir"}`` dicts, or a single explicit "not found"
        marker dict if ``path`` doesn't exist/isn't a directory (§1.7)."""
        full_path = (self.repo_root / path).resolve()
        try:
            full_path.relative_to(self.repo_root)
        except ValueError:
            return [{"error": f"refused: path escapes repo root: {path}"}]
        if not full_path.is_dir():
            return [{"error": f"not found or not a directory: {path}"}]
        return [{"name": e.name, "is_dir": e.is_dir} for e in self.list_dir(path)]

    def read_excerpt(self, path: str, line: int, context: int = EXCERPT_CONTEXT_LINES) -> str:
        """Bounded excerpt (+/- ``context`` lines) around ``line``. Never
        reads the whole file."""
        full_path = (self.repo_root / path).resolve()
        try:
            full_path.relative_to(self.repo_root)
        except ValueError:
            return f"[refused: path escapes repo root: {path}]"
        if not full_path.is_file():
            return f"[not found: {path}]"
        try:
            lines = full_path.read_text(errors="replace").splitlines()
        except OSError as exc:
            return f"[error reading {path}: {exc}]"
        start = max(0, line - 1 - context)
        end = min(len(lines), line + context)
        numbered = [f"{i + 1:>6}: {lines[i]}" for i in range(start, end)]
        return "\n".join(numbered)


# --- secret redaction (§5: Stage 2 must never echo credentials verbatim) ---

_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*\S+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-.]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
]


def redact_secrets(text: str) -> str:
    """Replace anything resembling a credential/API key/secret with
    ``[redacted secret]``. Used on any field that might echo repo/ticket
    content verbatim (reason, diff_sketch, etc.) -- see §5."""
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[redacted secret]", out)
    return out
