"""Jira adapter tests (§12): linked-ticket collection/dedup, failing linked
ticket is skipped with a warning (not fatal), --max-linked-tickets caps and
warns, --no-linked-tickets disables fetching entirely. No network access --
the requests.Session is faked.
"""
import warnings

import pytest

from triage_pipeline.adapters.jira import JiraCredentials, JiraRestAdapter

CREDS = JiraCredentials(base_url="https://example.atlassian.net", email="a@b.com", api_token="tok")


def _issue(key, summary="Title", links=None, subtasks=None, parent=None, comments=None):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "desc"}]}]},
            "comment": {"comments": comments or [], "total": len(comments or [])},
            "issuelinks": links or [],
            "subtasks": subtasks or [],
            "parent": parent,
            "project": {"name": "Demo", "key": "DEMO"},
            "priority": {"name": "High"},
            "labels": ["x"],
            "reporter": {"displayName": "Jane"},
            "status": {"name": "Open"},
        },
    }


class FakeResponse:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, issues: dict):
        self.issues = issues
        self.auth = None
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        key = url.rsplit("/", 1)[-1]
        if key not in self.issues:
            return FakeResponse(404, text="not found")
        entry = self.issues[key]
        if entry == "forbidden":
            return FakeResponse(403, text="forbidden")
        return FakeResponse(200, data=entry)


def _adapter(issues, **kwargs):
    session = FakeSession(issues)
    return JiraRestAdapter(ticket_key="MAIN-1", credentials=CREDS, session=session, **kwargs)


def test_linked_tickets_collected_and_deduped():
    issues = {
        "MAIN-1": _issue(
            "MAIN-1",
            links=[
                {"type": {"outward": "blocks", "inward": "is blocked by"}, "outwardIssue": {"key": "LINK-1"}},
                {"type": {"outward": "duplicates", "inward": "is duplicated by"}, "inwardIssue": {"key": "LINK-2"}},
            ],
            subtasks=[{"key": "LINK-1"}],  # duplicate of an issuelink -- should dedup
            parent={"key": "LINK-3"},
        ),
        "LINK-1": _issue("LINK-1", summary="Blocked thing"),
        "LINK-2": _issue("LINK-2", summary="Dup thing"),
        "LINK-3": _issue("LINK-3", summary="Parent thing"),
    }
    ticket = _adapter(issues).fetch()
    keys = {lt.key for lt in ticket.linked_tickets}
    assert keys == {"LINK-1", "LINK-2", "LINK-3"}
    assert len(ticket.linked_tickets) == 3  # deduped, not 4


def test_failing_linked_ticket_is_skipped_not_fatal():
    issues = {
        "MAIN-1": _issue(
            "MAIN-1",
            links=[{"type": {"outward": "relates to"}, "outwardIssue": {"key": "RESTRICTED-1"}}],
        ),
        "RESTRICTED-1": "forbidden",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ticket = _adapter(issues).fetch()
    assert ticket.linked_tickets == []
    assert any("RESTRICTED-1" in str(w.message) for w in caught)


def test_max_linked_tickets_caps_and_warns():
    links = [{"type": {"outward": "relates to"}, "outwardIssue": {"key": f"L-{i}"}} for i in range(15)]
    issues = {"MAIN-1": _issue("MAIN-1", links=links)}
    for i in range(15):
        issues[f"L-{i}"] = _issue(f"L-{i}")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ticket = _adapter(issues, max_linked_tickets=5).fetch()
    assert len(ticket.linked_tickets) == 5
    assert any("capping at 5" in str(w.message) for w in caught)


def test_no_linked_tickets_disables_fetching():
    links = [{"type": {"outward": "relates to"}, "outwardIssue": {"key": "L-1"}}]
    issues = {"MAIN-1": _issue("MAIN-1", links=links), "L-1": _issue("L-1")}
    ticket = _adapter(issues, include_linked_tickets=False).fetch()
    assert ticket.linked_tickets == []


def test_custom_field_matching_pattern_is_extracted():
    issue = _issue("MAIN-1")
    issue["names"] = {
        "customfield_11578": "Steps to reproduce issue",
        "customfield_99999": "Sprint",
    }
    issue["fields"]["customfield_11578"] = "Do X then Y then Z"
    issue["fields"]["customfield_99999"] = {"value": "Sprint 42"}
    issues = {"MAIN-1": issue}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ticket = _adapter(issues).fetch()
    assert ticket.custom_fields == {"Steps to reproduce issue": "Do X then Y then Z"}
    assert any("Sprint" in str(w.message) for w in caught)


def test_custom_fields_disabled_via_include_custom_fields_false():
    issue = _issue("MAIN-1")
    issue["names"] = {"customfield_11578": "Steps to reproduce issue"}
    issue["fields"]["customfield_11578"] = "Do X then Y then Z"
    issues = {"MAIN-1": issue}
    ticket = _adapter(issues, include_custom_fields=False).fetch()
    assert ticket.custom_fields == {}


def test_null_and_empty_custom_fields_are_ignored():
    issue = _issue("MAIN-1")
    issue["names"] = {"customfield_11578": "Steps to reproduce issue"}
    issue["fields"]["customfield_11578"] = None
    issues = {"MAIN-1": issue}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ticket = _adapter(issues).fetch()
    assert ticket.custom_fields == {}
    assert not caught  # nothing non-null to warn about either
