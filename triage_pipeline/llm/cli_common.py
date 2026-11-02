"""Prompt assembly and JSON extraction shared by every CLI-harness backend.

Both ``CopilotCLIClient`` and ``ClaudeCLIClient`` build their prompt through
``build_prompt`` so the instructions sent to each harness are byte-identical;
only the transport (process, flags, event stream) differs. This is what makes
a harness-vs-harness comparison fair.
"""
from __future__ import annotations

import json
import re
from typing import Optional

_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)

RETRY_SUFFIX = (
    "\n\nIMPORTANT: your previous response did not contain a single valid "
    "```json fenced block matching the schema. Respond again with ONLY that "
    "fenced JSON block."
)


def build_prompt(system_prompt: str, user_content: str, output_schema: dict) -> str:
    schema_str = json.dumps(output_schema, indent=2)
    return (
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


def extract_json(text: str) -> Optional[dict]:
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else text
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
