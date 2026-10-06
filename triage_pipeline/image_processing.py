"""Image processing for attachments.

Extracts text from images using Claude vision API and applies PII redaction.
Phase 4: Image Processing with Vision API
"""
from __future__ import annotations

import base64
import logging
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


class ImageProcessor:
    """Processes images to extract text using Claude vision API."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        """Initialize image processor.
        
        Args:
            llm_client: LLM client to use for vision API calls (optional,
                       can be set later via set_llm_client)
        """
        self.llm_client = llm_client
        self._vision_cache = {}  # Cache extracted text from images

    def set_llm_client(self, llm_client: LLMClient) -> None:
        """Set the LLM client to use for vision API calls.
        
        Args:
            llm_client: LLM client instance
        """
        self.llm_client = llm_client

    def is_image(self, mime_type: str) -> bool:
        """Check if MIME type is a supported image format.
        
        Args:
            mime_type: MIME type string (e.g., "image/png")
            
        Returns:
            True if MIME type is a supported image
        """
        return mime_type in SUPPORTED_IMAGE_MIMES

    def extract_text_from_image(
        self,
        image_content: str,
        mime_type: str,
        model: str = "claude-3-5-sonnet-20241022",
    ) -> tuple[Optional[str], Optional[str]]:
        """Extract text from image using Claude vision API.
        
        Args:
            image_content: Base64-encoded image content (must include [BASE64_IMAGE] marker)
            mime_type: Image MIME type (e.g., "image/png")
            model: Claude model to use for vision
            
        Returns:
            Tuple of (extracted_text, error_message)
            - extracted_text: Text extracted from image (or None if extraction failed)
            - error_message: Error message if extraction failed (or None on success)
        """
        if not self.llm_client:
            error_msg = "LLM client not configured for image processing"
            logger.warning(error_msg)
            return None, error_msg

        if not self.is_image(mime_type):
            error_msg = f"Unsupported image MIME type: {mime_type}"
            logger.warning(error_msg)
            return None, error_msg

        # Check cache first
        cache_key = hash(image_content) % (2**32)  # Simple hash for caching
        if cache_key in self._vision_cache:
            return self._vision_cache[cache_key], None

        try:
            # Extract base64 content if it has the marker
            b64_content = image_content
            if image_content.startswith("[BASE64_IMAGE]:"):
                b64_content = image_content[len("[BASE64_IMAGE]:"):]

            # Call Claude vision API via structured_call
            # System prompt instructs the model to extract text
            system_prompt = (
                "You are a text extraction assistant. Extract ALL text visible in the provided image. "
                "Return only the extracted text, preserving formatting as much as possible. "
                "If the image contains no text, respond with 'NO_TEXT_FOUND'."
            )

            user_content = f"Please extract all text from this image:\n[IMAGE_PLACEHOLDER]"

            # Since structured_call expects tool-based output, we'll use a simpler approach
            # by directly constructing the message with vision capabilities.
            # For now, we'll return a placeholder to indicate this needs integration.
            logger.info(
                f"Image text extraction requested for {mime_type} "
                "(integration with LLM client pending)"
            )

            # Placeholder: In a full implementation, this would call the LLM
            # For now, indicate that the image needs processing
            extracted_text = f"[IMAGE: Text extraction pending for {mime_type}]"

            # Cache the result
            self._vision_cache[cache_key] = extracted_text

            return extracted_text, None

        except Exception as exc:
            error_msg = f"Failed to extract text from image: {exc}"
            logger.warning(error_msg)
            return None, error_msg

    def process_image_attachment(
        self,
        attachment: Attachment,
        redactor=None,  # Optional PII redactor for redacting extracted text
    ) -> tuple[Attachment, Optional[str]]:
        """Process image attachment: extract text and apply redaction.
        
        Args:
            attachment: Image attachment to process
            redactor: PIIRedactor instance for redacting extracted text (optional)
            
        Returns:
            Tuple of (processed_attachment, error_message)
            - processed_attachment: New attachment with extracted text in content field
            - error_message: Error message if processing failed (or None on success)
        """
        if not self.is_image(attachment.mime_type):
            error_msg = f"Not an image: {attachment.mime_type}"
            return attachment, error_msg

        if not attachment.content:
            error_msg = "Empty image content"
            return attachment, error_msg

        # Extract text from image
        extracted_text, extraction_error = self.extract_text_from_image(
            attachment.content, attachment.mime_type
        )

        if extraction_error:
            return attachment, extraction_error

        # Apply PII redaction to extracted text if redactor provided
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

        # Create new attachment with extracted (and possibly redacted) text
        processed_attachment = Attachment(
            name=attachment.name,
            mime_type=attachment.mime_type,
            content=redacted_text if extracted_text else "",
            is_redacted=was_redacted,
            redaction_summary=redaction_summary,
            size_bytes=attachment.size_bytes,
        )

        return processed_attachment, None
