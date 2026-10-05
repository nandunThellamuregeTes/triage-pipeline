"""Rovo export adapter (§8): parses a loosely-structured Markdown export for
offline/no-Jira-access use.

Expected shape (reasonably tolerant of variation):

    # TICKET-123: Some title

    **Priority:** High
    **Labels:** backend, flaky
    **Reporter:** Jane Doe
    **Product:** billing-service

    ## Description

    Free text / markdown...

    ## Comments

    - First comment text
    - Second comment text
"""
from __future__ import annotations

import re

from ..schemas import NormalizedTicket
from .base import TicketSource

_HEADING_RE = re.compile(r"^#\s*([A-Z][A-Z0-9]+-\d+)?:?\s*(.*)$")
_FIELD_RE = re.compile(r"^\*\*([A-Za-z ]+):\*\*\s*(.*)$")


class RovoExportAdapter(TicketSource):
    def __init__(self, file_path: str):
        self.file_path = file_path

    def fetch(self) -> NormalizedTicket:
        with open(self.file_path, encoding="utf-8") as f:
            text = f.read()
        return self._parse(text)

    def _parse(self, text: str) -> NormalizedTicket:
        lines = text.splitlines()
        ticket_id = ""
        title = ""
        fields: dict[str, str] = {}
        sections: dict[str, list[str]] = {}
        current_section = None

        for line in lines:
            heading_match = _HEADING_RE.match(line) if line.startswith("# ") else None
            if heading_match:
                ticket_id = heading_match.group(1) or ""
                title = heading_match.group(2).strip()
                continue
            if line.startswith("## "):
                current_section = line[3:].strip().lower()
                sections[current_section] = []
                continue
            field_match = _FIELD_RE.match(line.strip())
            if field_match and current_section is None:
                fields[field_match.group(1).strip().lower()] = field_match.group(2).strip()
                continue
            if current_section is not None:
                sections[current_section].append(line)

        description_md = "\n".join(sections.get("description", [])).strip()
        comment_lines = [
            ln.lstrip("-* ").strip()
            for ln in sections.get("comments", [])
            if ln.strip()
        ]

        labels_raw = fields.get("labels", "")
        labels = [l.strip() for l in labels_raw.split(",") if l.strip()]

        return NormalizedTicket(
            ticket_id=ticket_id or fields.get("key", "UNKNOWN-0"),
            source="rovo_export",
            product=fields.get("product", ""),
            title=title,
            description_md=description_md,
            comments=comment_lines,
            reported_priority=fields.get("priority"),
            reported_labels=labels,
            reporter=fields.get("reporter"),
            linked_tickets=[],
        )
