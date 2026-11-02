"""Multi-repo workspace and deterministic service routing.

A ``Workspace`` is the set of local repositories (one per service) the
pipeline may search. Routing decides which of them Stage 1.5/2 should look
at for a given ticket, using only ripgrep over the ticket's technical
signals -- no LLM call -- so both harnesses always see the same routing.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schemas import ServiceMatch

MAX_SERVICES = 3
RELATIVE_CUTOFF = 0.4  # keep services scoring >= this fraction of the top score
MIN_SCORE = 2
MAX_SIGNALS = 25
MIN_SIGNAL_LEN = 4
RG_TIMEOUT_S = 20
_EXCLUDE_GLOBS = ["!*.lock", "!package-lock.json", "!*.min.js", "!*.map", "!*.snap", "!*.md"]

_URL_RE = re.compile(r"(?:https?://[^\s/\"'`<>]+)?(/[A-Za-z0-9_\-.{}:~%]+(?:/[A-Za-z0-9_\-.{}:~%]+)+)")
_ID_SEGMENT_RE = re.compile(r"^(?:\d+|[0-9a-fA-F-]{16,}|\{[^}]*\}|:[A-Za-z_]\w*)$")
_IDENT_RE = re.compile(r"^(?:[A-Za-z_][\w$]*[a-z0-9][A-Z][\w$]*|[A-Za-z]+_[A-Za-z_0-9]+|[\w$]+\.[\w$.]+)$")


@dataclass
class Service:
    name: str
    path: str


class WorkspaceError(ValueError):
    pass


class Workspace:
    def __init__(self, services: list[Service]):
        if not services:
            raise WorkspaceError("workspace needs at least one repository")
        names = [s.name for s in services]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise WorkspaceError(f"duplicate service name(s): {', '.join(sorted(dupes))}")
        self.services = services

    @property
    def is_multi(self) -> bool:
        return len(self.services) > 1

    @property
    def primary(self) -> Service:
        return self.services[0]

    def get(self, name: str) -> Optional[Service]:
        return next((s for s in self.services if s.name == name), None)


def parse_repo_specs(specs: list[str]) -> Workspace:
    """Parse repeated ``--repo`` values: ``PATH`` or ``NAME=PATH``.

    A bare PATH is named after its directory. With a single repo this is the
    classic single-repo mode.
    """
    services: list[Service] = []
    for spec in specs:
        name, sep, raw_path = spec.partition("=")
        if not sep:
            raw_path, name = spec, ""
        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            raise WorkspaceError(f"repo path does not exist or is not a directory: {raw_path}")
        services.append(Service(name=name.strip() or path.name, path=str(path)))
    return Workspace(services)


def git_head(path: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, timeout=10
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    except Exception:
        return None


def _weighted_signals(signals: list[str], text: str) -> list[tuple[str, int, str]]:
    """Return (needle, weight, kind) tuples. Routes weigh most, then
    identifiers, then plain terms."""
    out: list[tuple[str, int, str]] = []
    seen: set[str] = set()

    def add(needle: str, weight: int, kind: str) -> None:
        if len(needle) >= MIN_SIGNAL_LEN and needle not in seen:
            seen.add(needle)
            out.append((needle, weight, kind))

    for raw in list(signals) + [text]:
        for m in _URL_RE.finditer(raw or ""):
            segments = [s for s in m.group(1).split("/") if s]
            segments = [s for s in segments if not _ID_SEGMENT_RE.match(s)]
            # Full path first, then progressively shorter tails, down to two segments.
            for start in range(0, max(1, len(segments) - 1)):
                tail = segments[start:]
                if len(tail) < 2:
                    break
                add("/" + "/".join(tail), 3 if start < 2 else 2, "route")
    for raw in signals:
        raw = raw.strip()
        if not raw or "/" in raw and " " not in raw:
            continue  # routes handled above
        if _IDENT_RE.match(raw):
            add(raw, 2, "identifier")
        elif " " not in raw:
            add(raw, 1, "term")
        else:
            add(raw, 2, "phrase")
    return out[:MAX_SIGNALS]


def _rg_files(needle: str, root: str) -> list[str]:
    cmd = ["rg", "-F", "-l", "--max-count", "1"]
    for g in _EXCLUDE_GLOBS:
        cmd += ["--glob", g]
    cmd += ["-e", needle, root]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=RG_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in proc.stdout.splitlines() if line][:50]


def route_services(
    workspace: Workspace, technical_signals: list[str], ticket_text: str = ""
) -> list[ServiceMatch]:
    """Rank services by how strongly the ticket's signals appear in their code.

    Each distinct signal counts once per service (weight by kind); a route
    variant's best weight wins. A service whose name is a segment of a route in
    the ticket gets a bonus. Only services with at least one real code hit and
    ``MIN_SCORE`` are returned, at most ``MAX_SERVICES``.
    """
    signals = _weighted_signals(technical_signals, ticket_text)
    matches: list[ServiceMatch] = []
    for svc in workspace.services:
        score = 0
        evidence: list[str] = []
        route_credited = False
        for needle, weight, kind in signals:
            if kind == "route" and route_credited:
                continue
            files = _rg_files(needle, svc.path)
            if not files:
                continue
            score += weight
            if kind == "route":
                route_credited = True
            example = str(Path(files[0]).relative_to(svc.path)) if files[0].startswith(svc.path) else files[0]
            evidence.append(f"{kind} `{needle}` found in {len(files)} file(s), e.g. {example}")
        if not evidence:
            continue
        name_tokens = {t for t in re.split(r"[-_.]", svc.name.lower()) if len(t) >= 4}
        route_segments = {
            seg.lower() for needle, _, kind in signals if kind == "route" for seg in needle.split("/")
        }
        if name_tokens & route_segments:
            score += 2
            evidence.append(f"service name appears in a ticket route ({', '.join(sorted(name_tokens & route_segments))})")
        if score >= MIN_SCORE:
            matches.append(ServiceMatch(service=svc.name, path=svc.path, score=score, evidence=evidence))

    matches.sort(key=lambda m: m.score, reverse=True)
    if not matches:
        return []
    cutoff = matches[0].score * RELATIVE_CUTOFF
    return [m for m in matches if m.score >= cutoff][:MAX_SERVICES]
