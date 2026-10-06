"""Tests for attachment extraction from Jira.

Phase 2: Jira Adapter Enhancement - verify attachment download,
extraction, and size limits.
"""

import json
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from triage_pipeline.adapters.attachment_extractor import (
    MAX_FILE_SIZE,
    MAX_TOTAL_SIZE,
    download_and_extract_attachment,
    extract_attachments,
)
from triage_pipeline.schemas import Attachment


class TestDownloadAndExtractAttachment:
    """Tests for downloading and extracting individual attachments."""

    def test_extract_text_log_file(self):
        """Extract .log file attachment."""
        session = Mock(spec=requests.Session)
        session.get.return_value = Mock(
            status_code=200,
            content=b"ERROR: Exception at line 42\nStack trace here"
        )
        
        att_info = {
            "filename": "error.log",
            "content": "https://jira.example.com/download/error.log",
            "size": 1024,
        }
        
        att = download_and_extract_attachment(att_info, session)
        
        assert att is not None
        assert att.name == "error.log"
        assert att.mime_type == "text/plain"
        assert "ERROR" in att.content
        assert att.is_redacted is False
        assert att.size_bytes == 1024

    def test_extract_json_file_with_formatting(self):
        """Extract .json file and format as pretty-print."""
        session = Mock(spec=requests.Session)
        json_data = {"error": "null pointer", "line": 42}
        session.get.return_value = Mock(
            status_code=200,
            content=json.dumps(json_data).encode("utf-8")
        )
        
        att_info = {
            "filename": "response.json",
            "content": "https://jira.example.com/download/response.json",
            "size": 256,
        }
        
        att = download_and_extract_attachment(att_info, session)
        
        assert att is not None
        assert att.name == "response.json"
        assert att.mime_type == "application/json"
        # Should be pretty-printed
        assert "error" in att.content
        assert "null pointer" in att.content

    def test_extract_csv_file(self):
        """Extract .csv file attachment."""
        session = Mock(spec=requests.Session)
        csv_data = b"name,status,count\nerror,critical,5\nwarning,high,12"
        session.get.return_value = Mock(
            status_code=200,
            content=csv_data
        )
        
        att_info = {
            "filename": "report.csv",
            "content": "https://jira.example.com/download/report.csv",
            "size": 512,
        }
        
        att = download_and_extract_attachment(att_info, session)
        
        assert att is not None
        assert att.name == "report.csv"
        assert "error,critical" in att.content

    def test_extract_sql_file(self):
        """Extract .sql file attachment."""
        session = Mock(spec=requests.Session)
        sql_data = b"SELECT * FROM users WHERE id = 42;"
        session.get.return_value = Mock(
            status_code=200,
            content=sql_data
        )
        
        att_info = {
            "filename": "query.sql",
            "content": "https://jira.example.com/download/query.sql",
            "size": 128,
        }
        
        att = download_and_extract_attachment(att_info, session)
        
        assert att is not None
        assert att.name == "query.sql"
        assert "SELECT" in att.content

    def test_skip_unsupported_file_type(self):
        """Skip files that are not text or image."""
        session = Mock(spec=requests.Session)
        session.get.return_value = Mock(status_code=200, content=b"binary data")
        
        att_info = {
            "filename": "archive.zip",
            "content": "https://jira.example.com/download/archive.zip",
            "size": 10000,
        }
        
        att = download_and_extract_attachment(att_info, session)
        assert att is None  # Should skip unsupported type

    def test_skip_file_exceeding_size_limit(self):
        """Skip files exceeding MAX_FILE_SIZE."""
        session = Mock(spec=requests.Session)
        
        att_info = {
            "filename": "huge.log",
            "content": "https://jira.example.com/download/huge.log",
            "size": MAX_FILE_SIZE + 1,
        }
        
        att = download_and_extract_attachment(att_info, session)
        assert att is None  # Should skip oversized file

    def test_skip_file_with_no_download_url(self):
        """Skip attachment with no download URL."""
        session = Mock(spec=requests.Session)
        
        att_info = {
            "filename": "missing.log",
            # No 'content' field
            "size": 100,
        }
        
        att = download_and_extract_attachment(att_info, session)
        assert att is None

    def test_handle_download_failure(self):
        """Handle network errors during download gracefully."""
        session = Mock(spec=requests.Session)
        session.get.side_effect = requests.exceptions.ConnectionError("Network timeout")
        
        att_info = {
            "filename": "unreachable.log",
            "content": "https://jira.example.com/download/unreachable.log",
            "size": 1024,
        }
        
        att = download_and_extract_attachment(att_info, session)
        assert att is None  # Should handle error gracefully

    def test_handle_invalid_encoding(self):
        """Handle files with invalid UTF-8 encoding."""
        session = Mock(spec=requests.Session)
        # Binary data that's not valid UTF-8
        session.get.return_value = Mock(
            status_code=200,
            content=b"\xff\xfe\x00\x01 invalid utf-8"
        )
        
        att_info = {
            "filename": "garbled.log",
            "content": "https://jira.example.com/download/garbled.log",
            "size": 256,
        }
        
        # Should handle encoding errors with replacement characters
        att = download_and_extract_attachment(att_info, session)
        assert att is not None  # Should still succeed with encoding replacement
        assert att.name == "garbled.log"


class TestExtractAttachments:
    """Tests for extracting all attachments from a Jira issue."""

    def test_issue_with_no_attachments(self):
        """Handle issue with no attachments."""
        session = Mock(spec=requests.Session)
        issue = {"fields": {}}
        
        result = extract_attachments(issue, session)
        assert result == []

    def test_issue_with_multiple_attachments(self):
        """Extract multiple attachments from issue."""
        session = Mock(spec=requests.Session)
        
        # Mock responses for each attachment
        responses = [
            Mock(status_code=200, content=b"error log content"),
            Mock(status_code=200, content=json.dumps({"error": "test"}).encode()),
            Mock(status_code=200, content=b"name,value\na,1"),
        ]
        session.get.side_effect = responses
        
        issue = {
            "fields": {
                "attachment": [
                    {
                        "filename": "error.log",
                        "content": "https://jira.example.com/dl/1",
                        "size": 1024,
                    },
                    {
                        "filename": "response.json",
                        "content": "https://jira.example.com/dl/2",
                        "size": 512,
                    },
                    {
                        "filename": "data.csv",
                        "content": "https://jira.example.com/dl/3",
                        "size": 256,
                    },
                ]
            }
        }
        
        result = extract_attachments(issue, session)
        
        assert len(result) == 3
        assert result[0].name == "error.log"
        assert result[1].name == "response.json"
        assert result[2].name == "data.csv"

    def test_stop_at_total_size_limit(self):
        """Stop extracting when total size would exceed MAX_TOTAL_SIZE."""
        session = Mock(spec=requests.Session)
        
        # Use small sizes that fit individual limit but will exceed total
        small_size = 1024 * 1024  # 1MB per file (well under MAX_FILE_SIZE)
        large_size = 35 * 1024 * 1024  # 35MB would exceed total when added
        
        responses = [
            Mock(status_code=200, content=b"a" * small_size),
            Mock(status_code=200, content=b"b" * large_size),
        ]
        session.get.side_effect = responses
        
        issue = {
            "fields": {
                "attachment": [
                    {
                        "filename": "small.log",
                        "content": "https://jira.example.com/dl/1",
                        "size": small_size,
                    },
                    {
                        "filename": "toolarge.log",
                        "content": "https://jira.example.com/dl/2",
                        "size": large_size,  # Would exceed when added
                    },
                ]
            }
        }
        
        result = extract_attachments(issue, session)
        
        # Should only extract the first one (small) and skip the second
        assert len(result) == 1
        assert result[0].name == "small.log"

    def test_skip_failed_attachment_continue_others(self):
        """Skip failed attachment but continue with others."""
        session = Mock(spec=requests.Session)
        
        # First fails, second succeeds
        responses = [
            requests.exceptions.ConnectionError("timeout"),
            Mock(status_code=200, content=b"success"),
        ]
        session.get.side_effect = responses
        
        issue = {
            "fields": {
                "attachment": [
                    {
                        "filename": "failing.log",
                        "content": "https://jira.example.com/dl/1",
                        "size": 1024,
                    },
                    {
                        "filename": "success.log",
                        "content": "https://jira.example.com/dl/2",
                        "size": 512,
                    },
                ]
            }
        }
        
        result = extract_attachments(issue, session)
        
        # Should skip first but get second
        assert len(result) == 1
        assert result[0].name == "success.log"

    def test_empty_attachment_list(self):
        """Handle empty attachment list from Jira."""
        session = Mock(spec=requests.Session)
        
        issue = {"fields": {"attachment": []}}
        result = extract_attachments(issue, session)
        assert result == []

    def test_attachment_field_missing(self):
        """Handle missing attachment field in issue."""
        session = Mock(spec=requests.Session)
        
        issue = {"fields": {}}
        result = extract_attachments(issue, session)
        assert result == []

    def test_attachment_field_is_none(self):
        """Handle None attachment field."""
        session = Mock(spec=requests.Session)
        
        issue = {"fields": {"attachment": None}}
        result = extract_attachments(issue, session)
        assert result == []


class TestJiraAdapterWithAttachments:
    """Integration tests for JiraRestAdapter with attachments."""

    @patch("triage_pipeline.adapters.jira.JiraRestAdapter._get_issue")
    def test_fetch_without_attachments_backward_compatible(self, mock_get_issue):
        """Fetching without attachments still works (backward compatible)."""
        from triage_pipeline.adapters.jira import JiraRestAdapter, JiraCredentials
        
        mock_get_issue.return_value = {
            "key": "TEST-1",
            "fields": {
                "project": {"name": "Test Project"},
                "summary": "Test ticket",
                "description": None,
                "comment": {"comments": []},
                "labels": [],
                "priority": None,
                "reporter": None,
                "issuelinks": [],
                "subtasks": [],
            }
        }
        
        creds = JiraCredentials(
            base_url="https://test.atlassian.net",
            email="test@test.com",
            api_token="token"
        )
        adapter = JiraRestAdapter(
            ticket_key="TEST-1",
            credentials=creds,
            include_attachments=False,  # Disabled
        )
        
        ticket = adapter.fetch()
        
        assert ticket.ticket_id == "TEST-1"
        assert ticket.attachments == []  # Empty when disabled

    @patch("triage_pipeline.adapters.jira.extract_attachments")
    @patch("triage_pipeline.adapters.jira.JiraRestAdapter._get_issue")
    def test_fetch_with_attachments_enabled(self, mock_get_issue, mock_extract):
        """Fetching with attachments enabled includes attachments."""
        from triage_pipeline.adapters.jira import JiraRestAdapter, JiraCredentials
        
        # Mock attachment extraction to return test attachment
        test_att = Attachment(
            name="error.log",
            mime_type="text/plain",
            content="error content"
        )
        mock_extract.return_value = [test_att]
        
        mock_get_issue.return_value = {
            "key": "TEST-1",
            "fields": {
                "project": {"name": "Test Project"},
                "summary": "Test ticket",
                "description": None,
                "comment": {"comments": []},
                "labels": [],
                "priority": None,
                "reporter": None,
                "issuelinks": [],
                "subtasks": [],
                "attachment": [{"filename": "error.log"}],
            }
        }
        
        creds = JiraCredentials(
            base_url="https://test.atlassian.net",
            email="test@test.com",
            api_token="token"
        )
        adapter = JiraRestAdapter(
            ticket_key="TEST-1",
            credentials=creds,
            include_attachments=True,  # Enabled
        )
        
        ticket = adapter.fetch()
        
        assert ticket.ticket_id == "TEST-1"
        assert len(ticket.attachments) == 1
        assert ticket.attachments[0].name == "error.log"
        # Verify extract_attachments was called with the issue
        mock_extract.assert_called_once()
