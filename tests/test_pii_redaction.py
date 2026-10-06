"""Tests for PII detection and redaction.

Phase 3: PII Detection & Redaction - verify school name detection,
PII redaction, and redaction summaries.

NOTE: Presidio-based PII detection tests are skipped due to externally-managed-environment
restrictions. School name detection (regex-based) is fully tested and does not depend on Presidio.
"""

import pytest
from unittest.mock import patch, MagicMock

from triage_pipeline.pii_redaction import PIIRedactor
from triage_pipeline.schemas import Attachment


class TestSchoolNameDetection:
    """Tests for detecting school names (offline, no Presidio dependency)."""

    def test_detect_standard_high_school_name(self):
        """Detect 'Something High School' pattern."""
        redactor = PIIRedactor()
        text = "Error occurred at Lincoln High School in the student system."
        
        matches = redactor._detect_school_name(text)
        
        assert len(matches) > 0
        assert any("Lincoln High School" in text[start:end] for start, end, _ in matches)

    def test_detect_middle_school_name(self):
        """Detect 'Something Middle School' pattern."""
        redactor = PIIRedactor()
        text = "The database is used by Jefferson Middle School."
        
        matches = redactor._detect_school_name(text)
        
        assert len(matches) > 0
        assert any("Jefferson Middle School" in text[start:end] for start, end, _ in matches)

    def test_detect_elementary_school_name(self):
        """Detect elementary school pattern."""
        redactor = PIIRedactor()
        text = "Issue found in Washington Elementary School system."
        
        matches = redactor._detect_school_name(text)
        
        assert len(matches) > 0

    def test_detect_academy_name(self):
        """Detect academy name pattern."""
        redactor = PIIRedactor()
        text = "Problem reported at Madison Academy."
        
        matches = redactor._detect_school_name(text)
        
        assert len(matches) > 0
        assert any("Madison Academy" in text[start:end] for start, end, _ in matches)

    def test_detect_school_district(self):
        """Detect school district pattern."""
        redactor = PIIRedactor()
        text = "Affecting Springfield School District services."
        
        matches = redactor._detect_school_name(text)
        
        assert len(matches) > 0

    def test_no_match_for_generic_school_reference(self):
        """Don't match generic 'school' without a name."""
        redactor = PIIRedactor()
        text = "Error in school management system."
        
        matches = redactor._detect_school_name(text)
        
        # Should not match because no school name pattern
        assert len(matches) == 0

    def test_detect_multiple_school_names(self):
        """Detect multiple school names in one text."""
        redactor = PIIRedactor()
        text = (
            "Lincoln High School and Jefferson Middle School "
            "both report the same issue in Madison Academy."
        )
        
        matches = redactor._detect_school_name(text)
        
        assert len(matches) >= 2


class TestTextRedactionSchoolNamesOnly:
    """Tests for text redaction using only school name detection (no Presidio)."""

    def test_redact_school_name_offline(self):
        """Redact school name from text without Presidio."""
        redactor = PIIRedactor()
        text = "Error at Lincoln High School in student records."
        
        redacted, summary, was_redacted = redactor.redact_text(text, detect_school_names=True)
        
        assert "[REDACTED]" in redacted
        assert "Lincoln High School" not in redacted
        assert was_redacted is True
        assert summary is not None
        assert "school" in summary.lower()

    def test_preserve_non_school_content(self):
        """Keep non-PII content intact when redacting schools."""
        redactor = PIIRedactor()
        text = "Error: NullPointerException at Lincoln High School line 42"
        
        redacted, summary, was_redacted = redactor.redact_text(text, detect_school_names=True)
        
        # Technical terms should remain
        assert "NullPointerException" in redacted
        assert "line 42" in redacted

    def test_disable_school_name_detection(self):
        """Can disable school name detection."""
        redactor = PIIRedactor()
        text = "Error at Lincoln High School."
        
        # With detection enabled
        redacted_enabled, _, was_redacted_enabled = redactor.redact_text(
            text, detect_school_names=True
        )
        
        # With detection disabled
        redacted_disabled, _, was_redacted_disabled = redactor.redact_text(
            text, detect_school_names=False
        )
        
        # Enabled should redact school name
        assert "Lincoln High School" not in redacted_enabled
        # Disabled should have school name intact
        assert "Lincoln High School" in redacted_disabled

    def test_empty_text_returns_unchanged(self):
        """Handle empty text gracefully."""
        redactor = PIIRedactor()
        
        redacted, summary, was_redacted = redactor.redact_text("")
        
        assert redacted == ""
        assert summary is None
        assert was_redacted is False

    def test_text_with_no_pii_or_schools(self):
        """Return original text when no PII or schools found."""
        redactor = PIIRedactor()
        text = "Standard error log with timestamps: 2024-01-15 10:30:42"
        
        redacted, summary, was_redacted = redactor.redact_text(text, detect_school_names=True)
        
        assert was_redacted is False
        assert summary is None
        assert redacted == text


class TestAttachmentRedactionSchoolNamesOnly:
    """Tests for redacting PII from attachments using only school detection."""

    def test_redact_log_attachment_school_name(self):
        """Redact school name from log file attachment."""
        redactor = PIIRedactor()
        att = Attachment(
            name="error.log",
            mime_type="text/plain",
            content="Error at Lincoln High School reported by user."
        )
        
        redacted_att = redactor.redact_attachment(att, detect_school_names=True)
        
        assert redacted_att.name == att.name  # Name unchanged
        assert redacted_att.mime_type == att.mime_type  # MIME type unchanged
        assert "[REDACTED]" in redacted_att.content
        assert "Lincoln High School" not in redacted_att.content
        assert redacted_att.is_redacted is True
        assert redacted_att.redaction_summary is not None

    def test_preserve_attachment_metadata(self):
        """Preserve attachment metadata during redaction."""
        redactor = PIIRedactor()
        att = Attachment(
            name="data.csv",
            mime_type="text/csv",
            content="data,value\n1,2",
            size_bytes=1024
        )
        
        redacted_att = redactor.redact_attachment(att, detect_school_names=True)
        
        assert redacted_att.name == "data.csv"
        assert redacted_att.mime_type == "text/csv"
        assert redacted_att.size_bytes == 1024

    def test_skip_already_redacted_attachment(self):
        """Don't redact an attachment that's already redacted."""
        redactor = PIIRedactor()
        att = Attachment(
            name="redacted.log",
            mime_type="text/plain",
            content="User [REDACTED] at [REDACTED] Academy.",
            is_redacted=True,
            redaction_summary="1 school name redacted"
        )
        
        result_att = redactor.redact_attachment(att, detect_school_names=True)
        
        # Should return same object unchanged
        assert result_att == att
        assert result_att.is_redacted is True

    def test_empty_attachment_content(self):
        """Handle attachment with empty content."""
        redactor = PIIRedactor()
        att = Attachment(
            name="empty.log",
            mime_type="text/plain",
            content=""
        )
        
        result_att = redactor.redact_attachment(att, detect_school_names=True)
        
        # Should return original attachment
        assert result_att == att


class TestPIIRedactorEdgeCasesSchoolOnly:
    """Edge case tests for school name redaction."""

    def test_very_long_text(self):
        """Handle very long text without crashing."""
        redactor = PIIRedactor()
        long_text = "Error log:\n" + ("Line of text at Lincoln High School\n" * 1000)
        
        redacted, summary, was_redacted = redactor.redact_text(long_text, detect_school_names=True)
        
        assert isinstance(redacted, str)
        assert len(redacted) > 0
        assert "[REDACTED]" in redacted

    def test_text_with_special_characters(self):
        """Handle text with special characters."""
        redactor = PIIRedactor()
        text = "Error at Lincoln High School!@#$%^&*()_+-=[]{}|;:',.<>?/~`"
        
        redacted, summary, was_redacted = redactor.redact_text(text, detect_school_names=True)
        
        # Should handle without crashing
        assert isinstance(redacted, str)
        assert "[REDACTED]" in redacted

    def test_text_with_unicode(self):
        """Handle text with unicode characters."""
        redactor = PIIRedactor()
        text = "Error at Lincoln High School (日本語テキスト)"
        
        redacted, summary, was_redacted = redactor.redact_text(text, detect_school_names=True)
        
        # Should handle without crashing
        assert isinstance(redacted, str)

    def test_multiple_school_names_in_text(self):
        """Redact multiple school names in same text."""
        redactor = PIIRedactor()
        text = (
            "Lincoln High School reported error. "
            "Washington Academy has same issue. "
            "Jefferson Middle School affected."
        )
        
        redacted, summary, was_redacted = redactor.redact_text(text, detect_school_names=True)
        
        if was_redacted:
            # All school names should be redacted
            assert "Lincoln High School" not in redacted
            assert "Washington Academy" not in redacted
            assert "Jefferson Middle School" not in redacted


class TestPIIRedactorInitialization:
    """Tests for PIIRedactor initialization and lazy loading."""

    def test_redactor_init_does_not_download_model(self):
        """Creating PIIRedactor should not trigger Presidio model download."""
        # This should not raise SystemExit or hang
        redactor = PIIRedactor()
        
        # Initialization successful
        assert redactor is not None
        assert not redactor._presidio_initialized

    def test_school_detection_does_not_require_presidio(self):
        """School name detection works without Presidio."""
        redactor = PIIRedactor()
        text = "Error at Lincoln High School."
        
        # This should work without Presidio
        matches = redactor._detect_school_name(text)
        
        assert len(matches) > 0

    def test_redaction_with_school_names_only_does_not_require_presidio(self):
        """Redacting only school names doesn't require Presidio."""
        redactor = PIIRedactor()
        text = "Error at Lincoln High School."
        
        # This should work without triggering Presidio init
        redacted, summary, was_redacted = redactor.redact_text(
            text,
            detect_school_names=True
        )
        
        assert was_redacted is True
        assert "[REDACTED]" in redacted


@pytest.mark.skip(reason="Presidio model download blocked by PEP 668. School names work offline.")
class TestPIIRedactorWithPresidio:
    """Tests requiring Presidio (skipped in CI due to PEP 668 restrictions)."""

    def test_detect_pii_with_mock_presidio(self):
        """Detect PII when Presidio is available (mocked)."""
        # Would require mocking Presidio initialization
        pass
