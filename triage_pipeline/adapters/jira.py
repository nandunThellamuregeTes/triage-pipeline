"""Jira Cloud REST API v3 adapter (§8).

Auth via JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN. Converts ADF
descriptions/comments to lightweight Markdown (never a single flattened
line). Linked tickets (every issue-link type, subtasks, parent) are fetched
one level deep only, concurrently, capped by default, with individual
failures skipped (never fatal to the whole run).
"""
from __future__ import annotations

import os
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests

from ..schemas import LinkedTicket, NormalizedTicket
from .adf import adf_to_markdown
from .base import TicketSource
from .attachment_extractor import extract_attachments

DEFAULT_MAX_LINKED_TICKETS = 10
_API_PATH = "/rest/api/3/issue/{key}"


class JiraAuthError(RuntimeError):
    """Raised for actionable Jira auth/config problems (never a raw stack trace)."""


class JiraFetchError(RuntimeError):
    """Raised for actionable Jira fetch problems for the primary ticket."""


@dataclass
class JiraCredentials:
    base_url: str
    email: str
    api_token: str

    @classmethod
    def from_env(cls) -> "JiraCredentials":
        base_url = os.environ.get("JIRA_BASE_URL")
        email = os.environ.get("JIRA_EMAIL")
        api_token = os.environ.get("JIRA_API_TOKEN")
        missing = [
            name
            for name, val in [
                ("JIRA_BASE_URL", base_url),
                ("JIRA_EMAIL", email),
                ("JIRA_API_TOKEN", api_token),
            ]
            if not val
        ]
        if missing:
            raise JiraAuthError(
                "Missing required Jira environment variable(s): "
                + ", ".join(missing)
                + ". Set them, e.g.:\n"
                "  export JIRA_BASE_URL=https://yourcompany.atlassian.net\n"
                "  export JIRA_EMAIL=you@yourcompany.com\n"
                "  export JIRA_API_TOKEN=... (create one at "
                "id.atlassian.com/manage-profile/security/api-tokens)"
            )
        return cls(base_url=base_url.rstrip("/"), email=email, api_token=api_token)


class JiraRestAdapter(TicketSource):
    def __init__(
        self,
        ticket_key: str,
        credentials: JiraCredentials | None = None,
        include_comments: bool = True,
        include_linked_tickets: bool = True,
        include_attachments: bool = False,
        max_linked_tickets: int = DEFAULT_MAX_LINKED_TICKETS,
        session: requests.Session | None = None,
    ):
        self.ticket_key = ticket_key
        self.credentials = credentials or JiraCredentials.from_env()
        self.include_comments = include_comments
        self.include_linked_tickets = include_linked_tickets
        self.include_attachments = include_attachments
        self.max_linked_tickets = max_linked_tickets
        self._session = session or requests.Session()
        self._session.auth = (self.credentials.email, self.credentials.api_token)
        self._session.headers.update({"Accept": "application/json"})

    def _get_issue(self, key: str) -> dict:
        url = self.credentials.base_url + _API_PATH.format(key=key)
        try:
            resp = self._session.get(
                url,
                params={"expand": "renderedFields", "fields": "*all"},
                timeout=20,
            )
        except requests.exceptions.RequestException as exc:
            raise JiraFetchError(
                f"Network error contacting Jira at {url}: {exc}"
            ) from exc
        if resp.status_code == 401:
            raise JiraAuthError(
                "Jira rejected the credentials (401). Check JIRA_EMAIL/JIRA_API_TOKEN."
            )
        if resp.status_code == 403:
            raise JiraAuthError(
                f"Jira denied access to {key} (403). The account may lack "
                "permission for this project."
            )
        if resp.status_code == 404:
            raise JiraFetchError(f"Jira ticket {key} was not found (404).")
        if resp.status_code >= 400:
            raise JiraFetchError(
                f"Jira returned HTTP {resp.status_code} for {key}: {resp.text[:300]}"
            )
        return resp.json()

    def _comments_md(self, issue: dict, key: str) -> list[str]:
        if not self.include_comments:
            return []
        comment_field = issue.get("fields", {}).get("comment", {})
        comments = comment_field.get("comments", [])
        total = comment_field.get("total", len(comments))
        if total and total > len(comments):
            warnings.warn(
                f"{key}: Jira reported {total} comments but only {len(comments)} were "
                "returned in one page; pagination is not implemented, so later comments "
                "are missing."
            )
        return [adf_to_markdown(c.get("body")) for c in comments]

    def _collect_link_refs(self, issue: dict) -> list[tuple[str, str]]:
        """Returns (key, link_type) pairs, one level deep: issue links,
        subtasks, parent."""
        fields = issue.get("fields", {})
        refs: list[tuple[str, str]] = []
        for link in fields.get("issuelinks", []) or []:
            link_type = link.get("type", {})
            if "outwardIssue" in link:
                refs.append((link["outwardIssue"]["key"], link_type.get("outward", "relates to")))
            elif "inwardIssue" in link:
                refs.append((link["inwardIssue"]["key"], link_type.get("inward", "relates to")))
        for sub in fields.get("subtasks", []) or []:
            refs.append((sub["key"], "subtask"))
        parent = fields.get("parent")
        if parent:
            refs.append((parent["key"], "parent"))
        return refs

    def _fetch_linked_ticket(self, key: str, link_type: str) -> LinkedTicket | None:
        try:
            issue = self._get_issue(key)
        except (JiraAuthError, JiraFetchError) as exc:
            warnings.warn(f"Skipping linked ticket {key} ({link_type}): {exc}")
            return None
        fields = issue.get("fields", {})
        return LinkedTicket(
            key=issue.get("key", key),
            link_type=link_type,
            title=fields.get("summary", ""),
            status=(fields.get("status") or {}).get("name"),
            description_md=adf_to_markdown(fields.get("description")),
            comments=self._comments_md(issue, key),
        )

    def fetch(self) -> NormalizedTicket:
        issue = self._get_issue(self.ticket_key)
        fields = issue.get("fields", {})

        linked_tickets: list[LinkedTicket] = []
        if self.include_linked_tickets:
            refs = self._collect_link_refs(issue)
            # dedup by key, preserving first-seen link_type
            seen: dict[str, str] = {}
            for key, link_type in refs:
                seen.setdefault(key, link_type)
            if len(seen) > self.max_linked_tickets:
                warnings.warn(
                    f"{self.ticket_key}: {len(seen)} linked tickets found, capping at "
                    f"{self.max_linked_tickets} (use --max-linked-tickets to raise)."
                )
            capped = list(seen.items())[: self.max_linked_tickets]
            # Fetch concurrently -- a heavily-linked ticket should not serialize
            # N network round-trips.
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(capped)))) as pool:
                futures = {
                    pool.submit(self._fetch_linked_ticket, key, link_type): key
                    for key, link_type in capped
                }
                for fut in as_completed(futures):
                    result = fut.result()
                    if result is not None:
                        linked_tickets.append(result)

        # Extract attachments if enabled
        attachments = []
        if self.include_attachments:
            attachments = extract_attachments(issue, self._session)

        project = fields.get("project", {})
        priority = fields.get("priority")
        reporter = fields.get("reporter")

        return NormalizedTicket(
            ticket_id=issue.get("key", self.ticket_key),
            source="jira_api",
            product=project.get("name") or project.get("key", ""),
            title=fields.get("summary", ""),
            description_md=adf_to_markdown(fields.get("description")),
            comments=self._comments_md(issue, self.ticket_key),
            reported_priority=priority.get("name") if priority else None,
            reported_labels=list(fields.get("labels", []) or []),
            reporter=reporter.get("displayName") if reporter else None,
            linked_tickets=linked_tickets,
            attachments=attachments,
        )
