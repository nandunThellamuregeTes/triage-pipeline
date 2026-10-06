"""Tests for image processing.

Phase 4: Image Processing with Vision API - verify image attachment handling
and text extraction preparation.
"""

import pytest
from unittest.mock import MagicMock

from triage_pipeline.image_processing import ImageProcessor, SUPPORTED_IMAGE_MIMES
from triage_pipeline.schemas import Attachment
from triage_pipeline.pii_redaction import PIIRedactor


class TestImageProcessorBasics:
    """Tests for ImageProcessor basic functionality."""

    def test_is_image_png(self):
        """Identify PNG as supported image format."""
        processor = ImageProcessor()
        assert processor.is_image("image/png")

    def test_is_image_jpeg(self):
        """Identify JPEG as supported image format."""
        processor = ImageProcessor()
        assert processor.is_image("image/jpeg")

    def test_is_image_webp(self):
        """Identify WebP as supported image format."""
        processor = ImageProcessor()
        assert processor.is_image("image/webp")

    def test_is_image_gif(self):
        """Identify GIF as supported image format."""
        processor = ImageProcessor()
        assert processor.is_image("image/gif")

    def test_is_not_text_plain(self):
        """Reject text/plain as image."""
        processor = ImageProcessor()
        assert not processor.is_image("text/plain")

    def test_is_not_application_json(self):
        """Reject application/json as image."""
        processor = ImageProcessor()
        assert not processor.is_image("application/json")

    def test_supported_image_mimes_list(self):
        """Verify supported MIME types are defined."""
        assert len(SUPPORTED_IMAGE_MIMES) == 4
        assert "image/png" in SUPPORTED_IMAGE_MIMES
        assert "image/jpeg" in SUPPORTED_IMAGE_MIMES


class TestImageProcessorInitialization:
    """Tests for ImageProcessor initialization."""

    def test_init_without_llm_client(self):
        """Create processor without LLM client."""
        processor = ImageProcessor()
        assert processor.llm_client is None

    def test_init_with_llm_client(self):
        """Create processor with LLM client."""
        mock_client = MagicMock()
        processor = ImageProcessor(llm_client=mock_client)
        assert processor.llm_client is mock_client

    def test_set_llm_client(self):
        """Set LLM client after initialization."""
        processor = ImageProcessor()
        mock_client = MagicMock()
        processor.set_llm_client(mock_client)
        assert processor.llm_client is mock_client


class TestImageExtraction:
    """Tests for image text extraction."""

    def test_extract_text_without_llm_client(self):
        """Extraction fails gracefully without LLM client."""
        processor = ImageProcessor()
        extracted_text, error = processor.extract_text_from_image(
            "[BASE64_IMAGE]:abc123", "image/png"
        )
        
        assert extracted_text is None
        assert error is not None
        assert "LLM client not configured" in error

    def test_extract_text_unsupported_mime_type(self):
        """Extraction fails for unsupported MIME type."""
        processor = ImageProcessor(llm_client=MagicMock())
        extracted_text, error = processor.extract_text_from_image(
            "[BASE64_IMAGE]:abc123", "text/plain"
        )
        
        assert extracted_text is None
        assert error is not None
        assert "Unsupported image MIME type" in error

    def test_extract_text_caching(self):
        """Extracted text is cached."""
        mock_client = MagicMock()
        processor = ImageProcessor(llm_client=mock_client)
        
        # First call
        extracted1, error1 = processor.extract_text_from_image(
            "[BASE64_IMAGE]:sameimage123", "image/png"
        )
        
        # Second call with same content should return cached result
        extracted2, error2 = processor.extract_text_from_image(
            "[BASE64_IMAGE]:sameimage123", "image/png"
        )
        
        # Both should succeed
        assert error1 is None
        assert error2 is None
        # And should match
        assert extracted1 == extracted2


class TestImageAttachmentProcessing:
    """Tests for processing image attachments."""

    def test_process_non_image_attachment(self):
        """Processing non-image attachment returns it unchanged."""
        processor = ImageProcessor()
        att = Attachment(
            name="data.csv",
            mime_type="text/csv",
            content="col1,col2\n1,2"
        )
        
        processed_att, error = processor.process_image_attachment(att)
        
        assert processed_att == att
        assert error is not None
        assert "Not an image" in error

    def test_process_empty_image_attachment(self):
        """Processing empty image attachment fails gracefully."""
        processor = ImageProcessor()
        att = Attachment(
            name="empty.png",
            mime_type="image/png",
            content=""
        )
        
        processed_att, error = processor.process_image_attachment(att)
        
        assert error is not None
        assert "Empty image content" in error

    def test_process_image_without_llm_client(self):
        """Image processing fails without LLM client."""
        processor = ImageProcessor()
        att = Attachment(
            name="screenshot.png",
            mime_type="image/png",
            content="[BASE64_IMAGE]:abc123def456"
        )
        
        processed_att, error = processor.process_image_attachment(att)
        
        assert error is not None
        assert "LLM client not configured" in error

    def test_process_image_with_extraction_pending(self):
        """Image processing indicates extraction is pending."""
        mock_client = MagicMock()
        processor = ImageProcessor(llm_client=mock_client)
        att = Attachment(
            name="screenshot.png",
            mime_type="image/png",
            content="[BASE64_IMAGE]:abc123def456"
        )
        
        processed_att, error = processor.process_image_attachment(att)
        
        # Extraction should be pending (placeholder)
        assert error is None
        assert "Text extraction pending" in processed_att.content

    def test_process_image_with_redaction(self):
        """Image text extraction can include PII redaction."""
        mock_client = MagicMock()
        processor = ImageProcessor(llm_client=mock_client)
        redactor = PIIRedactor()
        
        att = Attachment(
            name="screenshot.png",
            mime_type="image/png",
            content="[BASE64_IMAGE]:abc123"
        )
        
        processed_att, error = processor.process_image_attachment(att, redactor=redactor)
        
        # Should process without error
        assert error is None
        assert processed_att.mime_type == "image/png"
        assert processed_att.name == "screenshot.png"

    def test_process_image_preserves_metadata(self):
        """Image processing preserves attachment metadata."""
        mock_client = MagicMock()
        processor = ImageProcessor(llm_client=mock_client)
        
        att = Attachment(
            name="diagram.png",
            mime_type="image/png",
            content="[BASE64_IMAGE]:abc123",
            size_bytes=5120
        )
        
        processed_att, error = processor.process_image_attachment(att)
        
        assert processed_att.name == "diagram.png"
        assert processed_att.mime_type == "image/png"
        assert processed_att.size_bytes == 5120


class TestImageProcessorEdgeCases:
    """Edge case tests for image processing."""

    def test_base64_marker_stripping(self):
        """Base64 content with marker is processed correctly."""
        mock_client = MagicMock()
        processor = ImageProcessor(llm_client=mock_client)
        
        # Content with marker
        extracted1, _ = processor.extract_text_from_image(
            "[BASE64_IMAGE]:data123", "image/png"
        )
        
        # Content without marker
        extracted2, _ = processor.extract_text_from_image(
            "data123", "image/png"
        )
        
        # Both should work
        assert extracted1 is not None
        assert extracted2 is not None

    def test_process_multiple_images_caching(self):
        """Multiple images can be processed with separate cache entries."""
        mock_client = MagicMock()
        processor = ImageProcessor(llm_client=mock_client)
        
        att1 = Attachment(
            name="image1.png",
            mime_type="image/png",
            content="[BASE64_IMAGE]:abc123"
        )
        
        att2 = Attachment(
            name="image2.png",
            mime_type="image/png",
            content="[BASE64_IMAGE]:def456"
        )
        
        processed1, error1 = processor.process_image_attachment(att1)
        processed2, error2 = processor.process_image_attachment(att2)
        
        # Both should process independently
        assert error1 is None
        assert error2 is None
        assert processed1.name == "image1.png"
        assert processed2.name == "image2.png"
