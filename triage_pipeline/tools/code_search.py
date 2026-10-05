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

EXCERPT_CONTEXT_LINES = 15
MAX_HITS_DEFAULT = 20


@dataclass
class SearchHit:
    path: str
    line: int
    snippet: str


class CodeSearchTool:
    """ripgrep-backed keyword/regex search, scoped to ``repo_root``.

    Never reads a whole file or the whole repo -- ``read_excerpt`` returns a
    bounded window only.
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.is_dir():
            raise ValueError(f"repo root does not exist or is not a directory: {repo_root}")
        if shutil.which("rg") is None:
            raise RuntimeError(
                "ripgrep ('rg') was not found on PATH -- CodeSearchTool requires it"
            )

    def search(self, query: str, max_hits: int = MAX_HITS_DEFAULT) -> list[SearchHit]:
        """Keyword/regex search via ripgrep, scoped to the repo root."""
        try:
            proc = subprocess.run(
                [
                    "rg",
                    "--line-number",
                    "--no-heading",
                    "--max-count",
                    "5",
                    "--max-filesize",
                    "1M",
                    "-e",
                    query,
                    str(self.repo_root),
                ],
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
