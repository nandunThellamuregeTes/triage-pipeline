"""Image processing for attachments.

Extracts visible text/content from image attachments using the pipeline's
LLM backend's own vision-capable tool use, then applies PII redaction.

Design: Copilot CLI (the pipeline's only real backend, see llm/copilot_cli.py)
has no separate "vision API" -- its underlying model is natively multimodal
and its built-in file-viewing tool can open an image path directly. So
rather than inventing a bespoke vision API call, we reuse the existing
``LLMClient.structured_call`` contract: point the model at the image's file
path (written to disk by ``adapters/attachment_extractor.py``) and ask it,
via a tiny schema, to transcribe any visible text and describe the screen
content. This is the same mechanism (and the same tool-turn budget/caching
contract) every other stage already uses -- no new backend code required.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .llm.base import LLMClient

from .schemas import Attachment

logger = logging.getLogger(__name__)

# Supported image MIME types for vision processing
SUPPORTED_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}

_FILE_PATH_MARKER = "[IMAGE_FILE_PATH]:"

_IMAGE_SYSTEM_PROMPT = (
    "You are a vision-assisted evidence extractor for a bug triage pipeline. "
    "You will be given the file path to a single image attachment (a screenshot, "
    "error dialog, or stack trace capture from a bug report). Open/view that exact "
    "file, then report what you see.\n\n"
    "Be thorough and literal:\n"
    "- Transcribe ALL visible text verbatim, including any JSON, log lines, stack "
    "traces, IDs, timestamps, or error messages -- these are often the most "
    "important evidence in the whole ticket.\n"
    "- Describe the UI/application state shown (what screen/page/tab is visible, "
    "what action appears to have just happened).\n"
    "- Note anything that looks like a discrepancy or anomaly (e.g. mismatched "
    "IDs, unexpected values, content that doesn't belong on that screen).\n\n"
    "Respond by calling emit_result with the documented schema. If the image "
    "contains no legible text, set extracted_text to \"NO_TEXT_FOUND\" but still "
    "fill in visual_description."
)

_IMAGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "extracted_text": {
            "type": "string",
            "description": "All visible text in the image, transcribed verbatim.",
        },
        "visual_description": {
            "type": "string",
            "description": "A description of the UI/content/anomalies shown.",
        },
    },
    "required": ["extracted_text", "visual_description"],
}


def _parse_file_path(image_content: str) -> Optional[str]:
    """Extract the on-disk path from an attachment's marker content.

    Returns None if ``image_content`` doesn't carry the expected marker
    (e.g. it's already-processed text, or a legacy/unexpected format).
    """
    if image_content.startswith(_FILE_PATH_MARKER):
        return image_content[len(_FILE_PATH_MARKER):]
    return None


class ImageProcessor:
    """Processes image attachments into text evidence via the LLM backend."""

    def __init__(self, llm_client: Optional["LLMClient"] = None):
        """Initialize image processor.

        Args:
            llm_client: LLM client to use for vision tool-use calls (optional,
                       can be set later via set_llm_client).
        """
        self.llm_client = llm_client
        self._vision_cache: dict[str, tuple[str, str]] = {}

    def set_llm_client(self, llm_client: "LLMClient") -> None:
        """Set the LLM client to use for vision tool-use calls."""
        self.llm_client = llm_client

    def is_image(self, mime_type: str) -> bool:
        """Check if MIME type is a supported image format."""
        return mime_type in SUPPORTED_IMAGE_MIMES

    def extract_text_from_image(
        self,
        image_content: str,
        mime_type: str,
        model: str = "claude-haiku-4.5",
    ) -> tuple[Optional[str], Optional[str]]:
        """Extract text/description from an image via the LLM's vision tool use.

        Args:
            image_content: Attachment content -- expected to carry the
                ``[IMAGE_FILE_PATH]:`` marker produced by
                ``adapters/attachment_extractor.py`` (an on-disk path the
                model's own file-viewing tool can open).
            mime_type: Image MIME type (e.g., "image/png").
            model: Model id to use for the vision call.

        Returns:
            Tuple of (combined_text, error_message). ``combined_text`` joins
            the transcribed text and the visual description; error_message
            is set (and combined_text is None) on failure.
        """
        if not self.llm_client:
            error_msg = "LLM client not configured for image processing"
            logger.warning(error_msg)
            return None, error_msg

        if not self.is_image(mime_type):
            error_msg = f"Unsupported image MIME type: {mime_type}"
            logger.warning(error_msg)
            return None, error_msg

        image_path = _parse_file_path(image_content)
        if image_path is None:
            error_msg = (
                "Image content did not carry an [IMAGE_FILE_PATH]: marker -- "
                "cannot locate the file to view"
            )
            logger.warning(error_msg)
            return None, error_msg

        if not Path(image_path).is_file():
            error_msg = f"Image file not found on disk: {image_path}"
            logger.warning(error_msg)
            return None, error_msg

        cache_key = image_path
        if cache_key in self._vision_cache:
            extracted_text, visual_description = self._vision_cache[cache_key]
            return f"{extracted_text}\n\n{visual_description}", None

        try:
            result = self.llm_client.structured_call(
                stage=f"image_extract::{Path(image_path).name}",
                model=model,
                system_prompt=_IMAGE_SYSTEM_PROMPT,
                user_content=f"Image file path: {image_path}\nMIME type: {mime_type}",
                output_schema=_IMAGE_OUTPUT_SCHEMA,
                tools=None,
                tool_executor=None,
                max_tool_turns=4,
                cwd=str(Path(image_path).parent),
            )
        except Exception as exc:
            error_msg = f"Failed to extract text from image: {exc}"
            logger.warning(error_msg)
            return None, error_msg

        extracted_text = result.get("extracted_text", "") or "NO_TEXT_FOUND"
        visual_description = result.get("visual_description", "") or ""
        self._vision_cache[cache_key] = (extracted_text, visual_description)

        combined = f"{extracted_text}\n\n{visual_description}".strip()
        return combined, None

    def process_image_attachment(
        self,
        attachment: Attachment,
        model: str = "claude-haiku-4.5",
        redactor=None,  # Optional PII redactor for redacting extracted text
    ) -> tuple[Attachment, Optional[str]]:
        """Process image attachment: extract text/description and redact PII.

        Args:
            attachment: Image attachment to process (content must carry the
                ``[IMAGE_FILE_PATH]:`` marker).
            model: Model id to use for the vision call.
            redactor: PIIRedactor instance for redacting extracted text (optional)

        Returns:
            Tuple of (processed_attachment, error_message).
            - processed_attachment: new Attachment with extracted text/description
              in the content field (unchanged, with original content, on error).
            - error_message: set if processing failed, else None.
        """
        if not self.is_image(attachment.mime_type):
            error_msg = f"Not an image: {attachment.mime_type}"
            return attachment, error_msg

        if not attachment.content:
            error_msg = "Empty image content"
            return attachment, error_msg

        extracted_text, extraction_error = self.extract_text_from_image(
            attachment.content, attachment.mime_type, model=model
        )

        if extraction_error:
            return attachment, extraction_error

        redacted_text = extracted_text
        redaction_summary = None
        was_redacted = False

        if redactor and extracted_text:
            try:
                redacted_text, redaction_summary, was_redacted = redactor.redact_text(
                    extracted_text, detect_school_names=True
                )
            except Exception as exc:
                logger.warning(f"Failed to redact extracted text: {exc}")
                # Don't fail on redaction error; use the extracted text as-is

        processed_attachment = Attachment(
            name=attachment.name,
            mime_type=attachment.mime_type,
            content=redacted_text if extracted_text else "",
            is_redacted=was_redacted,
            redaction_summary=redaction_summary,
            size_bytes=attachment.size_bytes,
        )

        return processed_attachment, None
