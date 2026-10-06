"""Tests for Attachment dataclass and NormalizedTicket integration.

Phase 1 (Schema): Verify Attachment dataclass, NormalizedTicket with attachments,
and fingerprinting logic.
"""

import pytest
from triage_pipeline.schemas import Attachment, NormalizedTicket


class TestAttachmentDataclass:
    """Basic Attachment dataclass tests."""

    def test_attachment_basic_fields(self):
        """Attachment has required fields: name, mime_type, content."""
        att = Attachment(
            name="error.log",
            mime_type="text/plain",
            content="Exception occurred"
        )
        assert att.name == "error.log"
        assert att.mime_type == "text/plain"
        assert att.content == "Exception occurred"
        assert att.is_redacted is False
        assert att.redaction_summary is None

    def test_attachment_with_redaction_metadata(self):
        """Attachment can store redaction information."""
        att = Attachment(
            name="data.csv",
            mime_type="text/csv",
            content="name,[REDACTED]\nJohn,[REDACTED]",
            is_redacted=True,
            redaction_summary="2 student names redacted"
        )
        assert att.is_redacted is True
        assert att.redaction_summary == "2 student names redacted"

    def test_attachment_with_size_bytes(self):
        """Attachment can optionally store original file size."""
        att = Attachment(
            name="large.log",
            mime_type="text/plain",
            content="truncated content for testing",
            size_bytes=1024*100  # 100KB original
        )
        assert att.size_bytes == 1024*100

    def test_attachment_different_mime_types(self):
        """Attachment supports various MIME types."""
        test_cases = [
            ("text/plain", ".log"),
            ("text/csv", ".csv"),
            ("application/json", ".json"),
            ("text/xml", ".xml"),
            ("image/png", ".png"),
        ]
        for mime_type, filename in test_cases:
            att = Attachment(
                name=filename,
                mime_type=mime_type,
                content="test content"
            )
            assert att.mime_type == mime_type


class TestNormalizedTicketWithAttachments:
    """Tests for NormalizedTicket with attachments field."""

    def test_normalized_ticket_without_attachments_backward_compatible(self):
        """NormalizedTicket works without attachments (backward compatibility)."""
        ticket = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Test description"
        )
        assert ticket.attachments == []

    def test_normalized_ticket_with_single_attachment(self):
        """NormalizedTicket can hold a single attachment."""
        att = Attachment(
            name="error.log",
            mime_type="text/plain",
            content="Error: NullPointerException"
        )
        ticket = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Test description",
            attachments=[att]
        )
        assert len(ticket.attachments) == 1
        assert ticket.attachments[0].name == "error.log"

    def test_normalized_ticket_with_multiple_attachments(self):
        """NormalizedTicket can hold multiple attachments."""
        att1 = Attachment(name="error.log", mime_type="text/plain", content="error")
        att2 = Attachment(name="data.csv", mime_type="text/csv", content="data")
        att3 = Attachment(name="screenshot.png", mime_type="image/png", content="image")
        
        ticket = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Test description",
            attachments=[att1, att2, att3]
        )
        assert len(ticket.attachments) == 3
        assert ticket.attachments[0].name == "error.log"
        assert ticket.attachments[1].name == "data.csv"
        assert ticket.attachments[2].name == "screenshot.png"


class TestFingerprintingWithAttachments:
    """Tests for content_fingerprint() with attachments."""

    def test_fingerprint_deterministic_without_attachments(self):
        """Same ticket produces same fingerprint consistently."""
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description"
        )
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description"
        )
        assert ticket1.content_fingerprint() == ticket2.content_fingerprint()

    def test_fingerprint_changes_with_attachment_added(self):
        """Adding attachment changes fingerprint."""
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description"
        )
        
        att = Attachment(name="error.log", mime_type="text/plain", content="error")
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att]
        )
        
        assert ticket1.content_fingerprint() != ticket2.content_fingerprint()

    def test_fingerprint_changes_with_attachment_content_changed(self):
        """Changing attachment content changes fingerprint."""
        att1 = Attachment(
            name="error.log",
            mime_type="text/plain",
            content="Original error message"
        )
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att1]
        )
        
        att2 = Attachment(
            name="error.log",
            mime_type="text/plain",
            content="Different error message"
        )
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att2]
        )
        
        assert ticket1.content_fingerprint() != ticket2.content_fingerprint()

    def test_fingerprint_changes_with_attachment_name_changed(self):
        """Changing attachment filename changes fingerprint."""
        att1 = Attachment(
            name="error1.log",
            mime_type="text/plain",
            content="Same content"
        )
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att1]
        )
        
        att2 = Attachment(
            name="error2.log",
            mime_type="text/plain",
            content="Same content"
        )
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att2]
        )
        
        assert ticket1.content_fingerprint() != ticket2.content_fingerprint()

    def test_fingerprint_changes_with_mime_type_changed(self):
        """Changing MIME type changes fingerprint."""
        att1 = Attachment(
            name="data",
            mime_type="text/plain",
            content="Same content"
        )
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att1]
        )
        
        att2 = Attachment(
            name="data",
            mime_type="text/csv",
            content="Same content"
        )
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att2]
        )
        
        assert ticket1.content_fingerprint() != ticket2.content_fingerprint()

    def test_fingerprint_consistent_with_redaction_metadata(self):
        """Fingerprints are consistent: redaction flag changes fingerprint."""
        att1 = Attachment(
            name="data.csv",
            mime_type="text/csv",
            content="[REDACTED]",
            is_redacted=False
        )
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att1]
        )
        
        att2 = Attachment(
            name="data.csv",
            mime_type="text/csv",
            content="[REDACTED]",
            is_redacted=True
        )
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att2]
        )
        
        assert ticket1.content_fingerprint() != ticket2.content_fingerprint()

    def test_fingerprint_ignores_redaction_summary_for_cache_invalidation(self):
        """Fingerprint only tracks is_redacted flag, not the summary text."""
        att1 = Attachment(
            name="data.csv",
            mime_type="text/csv",
            content="[REDACTED]",
            is_redacted=True,
            redaction_summary="1 student name redacted"
        )
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att1]
        )
        
        att2 = Attachment(
            name="data.csv",
            mime_type="text/csv",
            content="[REDACTED]",
            is_redacted=True,
            redaction_summary="2 student names redacted"  # Different summary
        )
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att2]
        )
        
        # Should be same fingerprint because is_redacted and content are the same
        assert ticket1.content_fingerprint() == ticket2.content_fingerprint()

    def test_fingerprint_with_multiple_attachments_order_matters(self):
        """Fingerprint reflects attachment order in list."""
        att1 = Attachment(name="a.log", mime_type="text/plain", content="a")
        att2 = Attachment(name="b.log", mime_type="text/plain", content="b")
        
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att1, att2]
        )
        
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[att2, att1]
        )
        
        assert ticket1.content_fingerprint() != ticket2.content_fingerprint()

    def test_fingerprint_empty_vs_no_attachments_are_same(self):
        """Ticket with no attachments is same as ticket with empty attachment list."""
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description"
        )
        
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            attachments=[]
        )
        
        assert ticket1.content_fingerprint() == ticket2.content_fingerprint()


class TestAttachmentIntegration:
    """Integration tests: Attachments with other NormalizedTicket fields."""

    def test_ticket_with_all_fields_and_attachments(self):
        """NormalizedTicket works with all fields + attachments."""
        from triage_pipeline.schemas import LinkedTicket
        
        att = Attachment(name="stack.txt", mime_type="text/plain", content="at line 42")
        linked = LinkedTicket(
            key="RELATED-1",
            link_type="relates to",
            title="Related issue",
            status="Open",
            description_md="Related"
        )
        
        ticket = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            comments=["Comment 1", "Comment 2"],
            reported_priority="High",
            reported_labels=["bug", "urgent"],
            reporter="john@example.com",
            linked_tickets=[linked],
            attachments=[att]
        )
        
        assert len(ticket.attachments) == 1
        assert len(ticket.linked_tickets) == 1
        assert len(ticket.comments) == 2
        assert ticket.reported_priority == "High"

    def test_fingerprint_includes_both_linked_tickets_and_attachments(self):
        """Fingerprint changes with both linked_tickets and attachments."""
        from triage_pipeline.schemas import LinkedTicket
        
        linked = LinkedTicket(
            key="RELATED-1",
            link_type="relates to",
            title="Related",
            status="Open",
            description_md="Related"
        )
        
        att = Attachment(name="data.log", mime_type="text/plain", content="data")
        
        ticket1 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description"
        )
        
        ticket2 = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test ticket",
            description_md="Description",
            linked_tickets=[linked],
            attachments=[att]
        )
        
        assert ticket1.content_fingerprint() != ticket2.content_fingerprint()


class TestAttachmentEdgeCases:
    """Edge case tests for Attachment handling."""

    def test_attachment_with_empty_content(self):
        """Attachment can have empty content (e.g., unreadable file)."""
        att = Attachment(
            name="unreadable.bin",
            mime_type="application/octet-stream",
            content=""
        )
        assert att.content == ""

    def test_attachment_with_very_large_content(self):
        """Attachment can store large text content."""
        large_content = "x" * 1_000_000  # 1MB of text
        att = Attachment(
            name="large.log",
            mime_type="text/plain",
            content=large_content
        )
        assert len(att.content) == 1_000_000

    def test_attachment_with_special_characters(self):
        """Attachment content can contain special characters and unicode."""
        att = Attachment(
            name="unicode.txt",
            mime_type="text/plain",
            content="Error: 日本語 テキスト\nSpecial: !@#$%^&*()\nNewlines:\nAnd\nMore"
        )
        assert "日本語" in att.content
        assert "\n" in att.content

    def test_attachment_with_null_bytes(self):
        """Attachment can contain null bytes (from binary data)."""
        att = Attachment(
            name="binary.dat",
            mime_type="application/octet-stream",
            content="text\x00with\x00nulls"
        )
        assert "\x00" in att.content

    def test_ticket_with_zero_attachments(self):
        """Ticket explicitly with zero attachments."""
        ticket = NormalizedTicket(
            ticket_id="TEST-1",
            source="jira_api",
            product="test",
            title="Test",
            description_md="Test",
            attachments=[]
        )
        assert len(ticket.attachments) == 0
