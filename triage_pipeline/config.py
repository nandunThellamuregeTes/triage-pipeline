"""Optional config file for credentials the pipeline (and the CLI harnesses
it spawns) read from the environment, so they don't need exporting in every
new shell.

Default location: ``~/.config/triage-pipeline/config.env`` (override with
``--config PATH``). Format is plain ``KEY=VALUE`` lines; ``#`` comments, blank
lines, an optional ``export`` prefix and single/double quotes are supported.

Values are placed into ``os.environ`` only when the variable is not already
set, so a real exported variable always wins. Because ``copilot``/``claude``
subprocesses inherit ``os.environ``, this is all that is needed for them to
see e.g. ``COPILOT_GITHUB_TOKEN``. Only an allowlist of variable prefixes is
loaded, so the file can't be used to override things like ``PATH``.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path("~/.config/triage-pipeline/config.env")
ALLOWED_PREFIXES = ("JIRA_", "COPILOT_", "GH_", "GITHUB_TOKEN", "ANTHROPIC_", "CLAUDE_")


class ConfigError(RuntimeError):
    pass


def _parse_line(line: str) -> Optional[tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key, value = key.strip(), value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key, value


def load_config_file(path: Optional[str] = None) -> list[str]:
    """Load ``path`` (or the default) into ``os.environ``. Returns the names
    of the variables that were set. A missing default file is not an error;
    a missing explicit ``path`` is."""
    explicit = path is not None
    config_path = Path(path).expanduser() if explicit else DEFAULT_CONFIG_PATH.expanduser()
    if not config_path.is_file():
        if explicit:
            raise ConfigError(f"Config file not found: {config_path}")
        return []

    if config_path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(
            f"[warning] {config_path} is readable by other users; run: chmod 600 {config_path}",
            file=sys.stderr,
        )

    loaded: list[str] = []
    for raw in config_path.read_text().splitlines():
        parsed = _parse_line(raw)
        if parsed is None:
            continue
        key, value = parsed
        if not key.startswith(ALLOWED_PREFIXES):
            print(f"[warning] Ignoring unsupported variable on a line in {config_path}", file=sys.stderr)
            continue
        if key not in os.environ and value:
            os.environ[key] = value
            loaded.append(key)
    return loaded
