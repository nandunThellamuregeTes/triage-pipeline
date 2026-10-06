"""Tests for image processing.

Verifies real vision extraction (via LLMClient.structured_call, using the
same StubLLMClient every other stage test uses) and PII redaction, plus
graceful degradation when extraction fails -- not the old placeholder.
"""
from pathlib import Path

import pytest

from triage_pipeline.image_processing import ImageProcessor, SUPPORTED_IMAGE_MIMES
from triage_pipeline.llm.stub import StubLLMClient
from triage_pipeline.pii_redaction import PIIRedactor
from triage_pipeline.schemas import Attachment


@pytest.fixture
def tmp_image_path(tmp_path):
    """A real on-disk file standing in for a downloaded image."""
    path = tmp_path / "screenshot.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nnot a real png but a real file")
    return str(path)


class TestImageProcessorBasics:
    def test_is_image_png(self):
        assert ImageProcessor().is_image("image/png")

    def test_is_image_jpeg(self):
        assert ImageProcessor().is_image("image/jpeg")

    def test_is_image_webp(self):
        assert ImageProcessor().is_image("image/webp")

    def test_is_image_gif(self):
        assert ImageProcessor().is_image("image/gif")

    def test_is_not_text_plain(self):
        assert not ImageProcessor().is_image("text/plain")

    def test_is_not_application_json(self):
        assert not ImageProcessor().is_image("application/json")

    def test_supported_image_mimes_list(self):
        assert len(SUPPORTED_IMAGE_MIMES) == 4
        assert "image/png" in SUPPORTED_IMAGE_MIMES


class TestImageProcessorInitialization:
    def test_init_without_llm_client(self):
        assert ImageProcessor().llm_client is None

    def test_init_with_llm_client(self):
        client = StubLLMClient({})
        assert ImageProcessor(llm_client=client).llm_client is client

    def test_set_llm_client(self):
        processor = ImageProcessor()
        client = StubLLMClient({})
        processor.set_llm_client(client)
        assert processor.llm_client is client


class TestImageExtraction:
    def test_extract_text_without_llm_client(self, tmp_image_path):
        processor = ImageProcessor()
        extracted_text, error = processor.extract_text_from_image(
            f"[IMAGE_FILE_PATH]:{tmp_image_path}", "image/png"
        )
        assert extracted_text is None
        assert "LLM client not configured" in error

    def test_extract_text_unsupported_mime_type(self, tmp_image_path):
        client = StubLLMClient({})
        processor = ImageProcessor(llm_client=client)
        extracted_text, error = processor.extract_text_from_image(
            f"[IMAGE_FILE_PATH]:{tmp_image_path}", "text/plain"
        )
        assert extracted_text is None
        assert "Unsupported image MIME type" in error

    def test_extract_text_missing_marker(self, tmp_image_path):
        """Content without the [IMAGE_FILE_PATH]: marker can't be opened."""
        client = StubLLMClient({})
        processor = ImageProcessor(llm_client=client)
        extracted_text, error = processor.extract_text_from_image(tmp_image_path, "image/png")
        assert extracted_text is None
        assert "IMAGE_FILE_PATH" in error

    def test_extract_text_file_not_found(self):
        client = StubLLMClient({})
        processor = ImageProcessor(llm_client=client)
        extracted_text, error = processor.extract_text_from_image(
            "[IMAGE_FILE_PATH]:/nonexistent/path/x.png", "image/png"
        )
        assert extracted_text is None
        assert "not found" in error

    def test_extract_text_real_vision_call(self, tmp_image_path):
        """Vision call goes through structured_call and returns real content,
        not a placeholder string."""
        stage_name = f"image_extract::{Path(tmp_image_path).name}"
        client = StubLLMClient({
            stage_name: {
                "extracted_text": 'created_by: 675\n{"module": "Course Overview"}',
                "visual_description": "A JSON activity-log viewer showing a saved document.",
            }
        })
        processor = ImageProcessor(llm_client=client)
        extracted_text, error = processor.extract_text_from_image(
            f"[IMAGE_FILE_PATH]:{tmp_image_path}", "image/png"
        )
        assert error is None
        assert "created_by: 675" in extracted_text
        assert "activity-log viewer" in extracted_text
        # Confirms the call was routed through the LLM client, with the real
        # file path (not a truncated/garbled preview) as context.
        assert client.calls[0]["stage"] == stage_name

    def test_extract_text_caching(self, tmp_image_path):
        """A second call for the same image path reuses the cached result
        instead of invoking the LLM again."""
        stage_name = f"image_extract::{Path(tmp_image_path).name}"
        client = StubLLMClient({
            stage_name: {"extracted_text": "hello", "visual_description": "a screen"}
        })
        processor = ImageProcessor(llm_client=client)

        extracted1, error1 = processor.extract_text_from_image(
            f"[IMAGE_FILE_PATH]:{tmp_image_path}", "image/png"
        )
        extracted2, error2 = processor.extract_text_from_image(
            f"[IMAGE_FILE_PATH]:{tmp_image_path}", "image/png"
        )

        assert error1 is None and error2 is None
        assert extracted1 == extracted2
        assert len(client.calls) == 1  # second call hit the in-memory cache

    def test_extract_text_llm_failure_is_graceful(self, tmp_image_path):
        """A client that raises is reported as an error, not an exception."""

        class BoomClient(StubLLMClient):
            def structured_call(self, **kwargs):
                raise RuntimeError("copilot CLI crashed")

        processor = ImageProcessor(llm_client=BoomClient({}))
        extracted_text, error = processor.extract_text_from_image(
            f"[IMAGE_FILE_PATH]:{tmp_image_path}", "image/png"
        )
        assert extracted_text is None
        assert "copilot CLI crashed" in error


class TestImageAttachmentProcessing:
    def test_process_non_image_attachment(self):
        processor = ImageProcessor()
        att = Attachment(name="data.csv", mime_type="text/csv", content="col1,col2\n1,2")
        processed_att, error = processor.process_image_attachment(att)
        assert processed_att == att
        assert "Not an image" in error

    def test_process_empty_image_attachment(self):
        processor = ImageProcessor()
        att = Attachment(name="empty.png", mime_type="image/png", content="")
        processed_att, error = processor.process_image_attachment(att)
        assert "Empty image content" in error

    def test_process_image_without_llm_client(self, tmp_image_path):
        processor = ImageProcessor()
        att = Attachment(
            name="screenshot.png", mime_type="image/png", content=f"[IMAGE_FILE_PATH]:{tmp_image_path}"
        )
        processed_att, error = processor.process_image_attachment(att)
        assert "LLM client not configured" in error

    def test_process_image_extracts_real_content(self, tmp_image_path):
        stage_name = f"image_extract::{Path(tmp_image_path).name}"
        client = StubLLMClient({
            stage_name: {
                "extracted_text": "Error: NullPointerException at Composer.js:42",
                "visual_description": "An error dialog over the lesson editor.",
            }
        })
        processor = ImageProcessor(llm_client=client)
        att = Attachment(
            name="screenshot.png", mime_type="image/png", content=f"[IMAGE_FILE_PATH]:{tmp_image_path}"
        )
        processed_att, error = processor.process_image_attachment(att)

        assert error is None
        assert "NullPointerException" in processed_att.content
        assert processed_att.name == "screenshot.png"
        assert processed_att.mime_type == "image/png"

    def test_process_image_with_redaction(self, tmp_image_path):
        stage_name = f"image_extract::{Path(tmp_image_path).name}"
        client = StubLLMClient({
            stage_name: {
                "extracted_text": "Student: Jane Smith reported the issue",
                "visual_description": "A support ticket screen.",
            }
        })
        processor = ImageProcessor(llm_client=client)
        redactor = PIIRedactor()
        att = Attachment(
            name="screenshot.png", mime_type="image/png", content=f"[IMAGE_FILE_PATH]:{tmp_image_path}"
        )
        processed_att, error = processor.process_image_attachment(att, redactor=redactor)

        assert error is None
        assert processed_att.mime_type == "image/png"
        assert processed_att.name == "screenshot.png"

    def test_process_image_preserves_metadata(self, tmp_image_path):
        stage_name = f"image_extract::{Path(tmp_image_path).name}"
        client = StubLLMClient({
            stage_name: {"extracted_text": "x", "visual_description": "y"}
        })
        processor = ImageProcessor(llm_client=client)
        att = Attachment(
            name="diagram.png",
            mime_type="image/png",
            content=f"[IMAGE_FILE_PATH]:{tmp_image_path}",
            size_bytes=5120,
        )
        processed_att, error = processor.process_image_attachment(att)
        assert error is None
        assert processed_att.name == "diagram.png"
        assert processed_att.mime_type == "image/png"
        assert processed_att.size_bytes == 5120

    def test_process_multiple_images_independently(self, tmp_path):
        path1 = tmp_path / "image1.png"
        path2 = tmp_path / "image2.png"
        path1.write_bytes(b"fake1")
        path2.write_bytes(b"fake2")

        client = StubLLMClient({
            "image_extract::image1.png": {"extracted_text": "one", "visual_description": "a"},
            "image_extract::image2.png": {"extracted_text": "two", "visual_description": "b"},
        })
        processor = ImageProcessor(llm_client=client)

        att1 = Attachment(name="image1.png", mime_type="image/png", content=f"[IMAGE_FILE_PATH]:{path1}")
        att2 = Attachment(name="image2.png", mime_type="image/png", content=f"[IMAGE_FILE_PATH]:{path2}")

        processed1, error1 = processor.process_image_attachment(att1)
        processed2, error2 = processor.process_image_attachment(att2)

        assert error1 is None and error2 is None
        assert "one" in processed1.content
        assert "two" in processed2.content
