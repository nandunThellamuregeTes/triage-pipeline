"""Attachment extraction and processing for Jira tickets.

Handles downloading attachments from Jira API with size limits,
extracting text content from supported formats, and preparing
attachments for PII redaction and LLM processing.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import tempfile
import uuid
import warnings
from pathlib import Path
from typing import Optional

import requests

from ..schemas import Attachment

# Supported text file extensions for extraction
SUPPORTED_TEXT_EXTENSIONS = {
    ".log", ".txt", ".csv", ".json", ".md", ".sql", ".xml",
    ".sh", ".properties", ".yaml", ".yml", ".conf", ".config"
}

# Size limits (in bytes)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB per file
MAX_TOTAL_SIZE = 50 * 1024 * 1024  # 50MB per ticket
# Videos get a larger per-file allowance (Stage 0's own limit, §1.5), since
# screen recordings are routinely bigger than a log file but still useful.
MAX_VIDEO_FILE_SIZE = 50 * 1024 * 1024  # 50MB per video

# Image MIME types (for vision API processing)
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

# Video extensions/MIME types -- NOT extracted as "text content" the way
# text files are; instead the raw bytes are persisted to disk (like images)
# and handed off to stage0_attachments.py's ffmpeg-based frame extraction +
# vision analysis (§1.5).
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
VIDEO_MIME_TYPES = {
    "video/mp4", "video/quicktime", "video/webm", "video/x-msvideo", "video/x-matroska",
}


class AttachmentExtractionError(RuntimeError):
    """Raised for actionable attachment extraction problems (never a raw stack trace)."""


def _guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename, with fallback.
    
    Includes custom mappings for common file extensions.
    """
    custom_mime_types = {
        ".log": "text/plain",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".sql": "text/plain",
        ".xml": "application/xml",
        ".sh": "text/plain",
        ".properties": "text/plain",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".conf": "text/plain",
        ".config": "text/plain",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
    }
    
    ext = Path(filename).suffix.lower()
    if ext in custom_mime_types:
        return custom_mime_types[ext]
    
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def _extract_text_from_json(content: str) -> str:
    """Extract readable text from JSON for logging/error objects."""
    try:
        data = json.loads(content)
        # Pretty-print for readability
        return json.dumps(data, indent=2)
    except (json.JSONDecodeError, ValueError):
        # Invalid JSON, return as-is
        return content


def _extract_text_from_csv(content: str) -> str:
    """CSV is already text, return as-is."""
    return content


def _extract_text_from_xml(content: str) -> str:
    """XML is already text, return as-is."""
    return content


def _extract_text_from_log(content: str) -> str:
    """Log files are already text, return as-is."""
    return content


def _extract_text_from_sql(content: str) -> str:
    """SQL files are already text, return as-is."""
    return content


def _extract_text_generic(content: str) -> str:
    """Fallback for other text files."""
    return content


def _extract_text_content(filename: str, content: str) -> str:
    """Extract/format text content based on file type.
    
    Args:
        filename: original filename (for type detection)
        content: raw text content
        
    Returns:
        formatted/extracted text
    """
    ext = Path(filename).suffix.lower()
    
    if ext == ".json":
        return _extract_text_from_json(content)
    elif ext == ".csv":
        return _extract_text_from_csv(content)
    elif ext == ".xml":
        return _extract_text_from_xml(content)
    elif ext == ".log":
        return _extract_text_from_log(content)
    elif ext == ".sql":
        return _extract_text_from_sql(content)
    else:
        return _extract_text_generic(content)


def download_and_extract_attachment(
    attachment_info: dict,
    session: requests.Session,
    max_file_size: int = MAX_FILE_SIZE,
    max_video_file_size: int = MAX_VIDEO_FILE_SIZE,
) -> Optional[Attachment]:
    """Download and extract a single attachment from Jira.
    
    Args:
        attachment_info: Attachment dict from Jira API response
        session: requests.Session with auth configured
        max_file_size: maximum size per file (default 10MB)
        max_video_file_size: maximum size for video attachments (default 50MB,
            matching Stage 0's own limit -- see stage0_attachments.py §1.5)
        
    Returns:
        Attachment object if successful, None if skipped/failed
    """
    filename = attachment_info.get("filename", "unknown")
    download_url = attachment_info.get("content")
    size_bytes = attachment_info.get("size", 0)
    
    if not download_url:
        warnings.warn(f"Attachment {filename} has no download URL, skipping")
        return None
    
    # Determine if this is a text file we should extract
    ext = Path(filename).suffix.lower()
    is_text_file = ext in SUPPORTED_TEXT_EXTENSIONS
    guessed_mime = _guess_mime_type(filename)
    is_image_file = guessed_mime in IMAGE_MIME_TYPES
    is_video_file = guessed_mime in VIDEO_MIME_TYPES or ext in VIDEO_EXTENSIONS

    # Check size limits -- videos get their own (larger) allowance.
    effective_limit = max_video_file_size if is_video_file else max_file_size
    if size_bytes > effective_limit:
        warnings.warn(
            f"Attachment {filename} ({size_bytes} bytes) exceeds max file size "
            f"({effective_limit} bytes), skipping"
        )
        return None
    
    # Skip files we can't/won't process
    if not is_text_file and not is_image_file and not is_video_file:
        return None
    
    # Download the file
    try:
        resp = session.get(download_url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        warnings.warn(f"Failed to download attachment {filename}: {exc}")
        return None
    
    # Extract content
    mime_type = guessed_mime
    
    if is_text_file:
        try:
            # Decode text content
            content = resp.content.decode("utf-8", errors="replace")
            # Format based on type
            content = _extract_text_content(filename, content)
        except Exception as exc:
            warnings.warn(f"Failed to extract text from {filename}: {exc}")
            return None
    elif is_image_file:
        # For images, persist the full bytes to a temp file so a later
        # vision-capable LLM call (image_processing.ImageProcessor) can open
        # the actual image directly -- a vision model needs the real pixel
        # data, not a truncated/encoded text preview, to read on-screen text
        # or compare visual details.
        try:
            tmp_dir = Path(tempfile.gettempdir()) / "triage_pipeline_attachments"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
            tmp_path = tmp_dir / safe_name
            tmp_path.write_bytes(resp.content)
            content = f"[IMAGE_FILE_PATH]:{tmp_path}"
        except Exception as exc:
            warnings.warn(f"Failed to save image {filename}: {exc}")
            return None
    elif is_video_file:
        # Videos are NEVER treated as "text content" the way log/json/etc.
        # files are above -- persist the raw bytes and hand the path off to
        # stage0_attachments.py's ffmpeg-based frame extraction + vision
        # analysis (§1.5), using the same on-disk-path marker convention as
        # images.
        try:
            tmp_dir = Path(tempfile.gettempdir()) / "triage_pipeline_attachments"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
            tmp_path = tmp_dir / safe_name
            tmp_path.write_bytes(resp.content)
            content = f"[VIDEO_FILE_PATH]:{tmp_path}"
        except Exception as exc:
            warnings.warn(f"Failed to save video {filename}: {exc}")
            return None
    else:
        return None
    
    return Attachment(
        name=filename,
        mime_type=mime_type,
        content=content,
        is_redacted=False,
        redaction_summary=None,
        size_bytes=size_bytes,
    )


def extract_attachments(
    issue: dict,
    session: requests.Session,
    max_total_size: int = MAX_TOTAL_SIZE,
    max_file_size: int = MAX_FILE_SIZE,
    max_video_file_size: int = MAX_VIDEO_FILE_SIZE,
) -> list[Attachment]:
    """Extract all processable attachments from a Jira issue.
    
    Args:
        issue: Issue dict from Jira API response
        session: requests.Session with auth configured
        max_total_size: maximum total size for all attachments
        max_file_size: maximum size per file
        max_video_file_size: maximum size for video attachments
        
    Returns:
        List of successfully extracted Attachment objects
    """
    fields = issue.get("fields", {})
    attachment_list = fields.get("attachment", []) or []
    
    if not attachment_list:
        return []
    
    attachments: list[Attachment] = []
    total_size = 0
    
    for att_info in attachment_list:
        # Check total size limit
        att_size = att_info.get("size", 0)
        if total_size + att_size > max_total_size:
            warnings.warn(
                f"Attachment {att_info.get('filename')} would exceed total size limit "
                f"({max_total_size} bytes), skipping remaining attachments"
            )
            break
        
        # Try to extract this attachment
        try:
            att = download_and_extract_attachment(att_info, session, max_file_size, max_video_file_size)
            if att is not None:
                attachments.append(att)
                total_size += att_size
        except Exception as exc:
            # Log but continue with other attachments
            filename = att_info.get("filename", "unknown")
            warnings.warn(f"Unexpected error processing attachment {filename}: {exc}")
            continue
    
    return attachments
