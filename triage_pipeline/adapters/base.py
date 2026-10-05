"""Ticket source adapters (§8)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import NormalizedTicket


class TicketSource(ABC):
    @abstractmethod
    def fetch(self) -> NormalizedTicket: ...
