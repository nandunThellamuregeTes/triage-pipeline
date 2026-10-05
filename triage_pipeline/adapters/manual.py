"""Manual adapter (§8): a pasted/hand-authored JSON ticket, also used for
local testing."""
from __future__ import annotations

import json

from ..schemas import LinkedTicket, NormalizedTicket
from .base import TicketSource


class ManualAdapter(TicketSource):
    def __init__(self, file_path: str | None = None, data: dict | None = None):
        if file_path is None and data is None:
            raise ValueError("ManualAdapter requires either file_path or data")
        self.file_path = file_path
        self._data = data

    def fetch(self) -> NormalizedTicket:
        data = self._data
        if data is None:
            with open(self.file_path, encoding="utf-8") as f:
                data = json.load(f)
        linked = [
            LinkedTicket(
                key=lt["key"],
                link_type=lt.get("link_type", "relates to"),
                title=lt.get("title", ""),
                status=lt.get("status"),
                description_md=lt.get("description_md", ""),
                comments=list(lt.get("comments", [])),
            )
            for lt in data.get("linked_tickets", [])
        ]
        return NormalizedTicket(
            ticket_id=data["ticket_id"],
            source="manual",
            product=data.get("product", ""),
            title=data.get("title", ""),
            description_md=data.get("description_md", ""),
            comments=list(data.get("comments", [])),
            reported_priority=data.get("reported_priority"),
            reported_labels=list(data.get("reported_labels", [])),
            reporter=data.get("reporter"),
            linked_tickets=linked,
        )
