"""PII (Personally Identifiable Information) detection and redaction.

Uses Presidio for detecting PII entities (names, emails, IDs, etc.) and applies
REDACT strategy: keep content structure, mask sensitive values as [REDACTED].
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

try:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    PRESIDIO_AVAILABLE = True
except (ImportError, RuntimeError) as e:
    # Presidio not available, will use fallback
    PRESIDIO_AVAILABLE = False
    AnalyzerEngine = None
    RecognizerResult = None
    AnonymizerEngine = None
    OperatorConfig = None

from .schemas import Attachment

logger = logging.getLogger(__name__)

# School name patterns (case-insensitive for school type keywords, but name must start with capital)
SCHOOL_NAME_PATTERNS = [
    # Common school name formats: "Word School", "Word Academy" etc.
    # The first word must start uppercase, followed by (optionally capitalized) school type
    r'\b[A-Z][a-z]+ (?:[Hh]igh|[Mm]iddle|[Ee]lementary|[Pp]rimary|[Ss]econdary|[Pp]ublic|[Pp]rivate|[Cc]harter) [Ss]chool\b',
    r'\b[A-Z][a-z]+ (?:[Aa]cademy|[Cc]ollege|[Ii]nstitute|[Uu]niversity)\b',
    # With district
    r'\b[A-Z][a-z]+ [Ss]chool [Dd]istrict\b',
    # Variations
    r'\b[A-Z][a-z]+ [Jj]unior [Hh]igh\b',
    r'\b[A-Z][a-z]+ [Ss]enior [Hh]igh\b',
]

# School name markers (words that often appear near school names)
SCHOOL_MARKERS = {
    "school", "academy", "college", "institute", "university",
    "district", "board", "education", "high school", "middle school",
    "elementary", "primary", "secondary", "public", "private", "charter"
}


class MockRecognizerResult:
    """Mock RecognizerResult when Presidio is unavailable."""
    def __init__(self, entity_type: str, start: int, end: int, score: float):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


class PIIRedactor:
    """Detects and redacts PII from text, with special handling for school names."""

    def __init__(self, custom_entities: Optional[list[str]] = None):
        """Initialize PII redactor with Presidio engines (lazy initialization).
        
        Args:
            custom_entities: Additional entity types to detect beyond defaults
        """
        self.custom_entities = custom_entities or []
        self.analyzer = None
        self.anonymizer = None
        self._presidio_available = PRESIDIO_AVAILABLE
        self._presidio_initialized = False
    
    def _ensure_presidio_initialized(self) -> bool:
        """Lazily initialize Presidio on first use.
        
        Returns:
            True if Presidio is available and initialized, False otherwise
        """
        if self._presidio_initialized:
            return self._presidio_available
        
        self._presidio_initialized = True
        
        if not self._presidio_available:
            return False
        
        try:
            # Set environment to avoid model download issues
            os.environ["PRESIDIO_DISABLE_TELEMETRY"] = "true"
            self.analyzer = AnalyzerEngine()
            self.anonymizer = AnonymizerEngine()
            logger.info("Presidio initialized successfully")
            self._presidio_available = True
            return True
        except SystemExit as e:
            # spacy.cli.download() raises SystemExit on failure
            logger.warning(f"Presidio initialization failed (SystemExit): {e}. Using fallback (school names only).")
            self.analyzer = None
            self.anonymizer = None
            self._presidio_available = False
            return False
        except Exception as e:
            # Any other exception (ImportError, RuntimeError, etc.)
            logger.warning(f"Failed to initialize Presidio engines: {e}. Using fallback mode.")
            self.analyzer = None
            self.anonymizer = None
            self._presidio_available = False
            return False

    def _detect_school_name(self, text: str) -> list[tuple[int, int, str]]:
        """Detect school names using patterns and context heuristics.
        
        Returns list of (start_pos, end_pos, matched_text) tuples.
        """
        school_matches = []

        # Try regex patterns (no re.IGNORECASE needed; patterns handle case)
        for pattern in SCHOOL_NAME_PATTERNS:
            for match in re.finditer(pattern, text):
                school_matches.append(
                    (match.start(), match.end(), match.group())
                )

        return school_matches

    def detect_pii(self, text: str) -> list:
        """Detect all PII entities in text using Presidio.
        
        Args:
            text: Text to analyze
            
        Returns:
            List of RecognizerResult objects with detected entities
        """
        if not text:
            return []

        if not self._ensure_presidio_initialized():
            # Fallback: no PII detection without Presidio
            return []

        try:
            results = self.analyzer.analyze(text=text, language="en")
            return results
        except Exception as exc:
            logger.warning(f"Error analyzing text for PII: {exc}")
            return []

    def _build_redaction_operators(self) -> dict:
        """Build operator configuration for anonymization.
        
        Defines how different entity types should be redacted.
        """
        if not self._ensure_presidio_initialized() or OperatorConfig is None:
            return {}
        
        return {
            "PERSON": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "CREDIT_CARD": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "CRYPTO": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "ID_NUMBER": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "IP_ADDRESS": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "LOCATION": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "ORG": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
            "SCHOOL_NAME": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
        }

    def redact_text(
        self,
        text: str,
        detect_school_names: bool = True,
        confidence_threshold: float = 0.5,
    ) -> tuple[str, Optional[str], bool]:
        """Redact PII from text.
        
        Args:
            text: Text to redact
            detect_school_names: Also detect and redact school names
            confidence_threshold: Minimum confidence for Presidio entities (0.0-1.0)
            
        Returns:
            Tuple of (redacted_text, redaction_summary, was_redacted)
            - redacted_text: Text with PII masked as [REDACTED]
            - redaction_summary: Human-readable summary (e.g., "3 names redacted")
            - was_redacted: Whether any redactions were made
        """
        if not text:
            return text, None, False

        redaction_summary_parts = []
        redacted_text = text

        # Detect Presidio entities (if available)
        pii_results = self.detect_pii(text)

        # Filter by confidence threshold
        filtered_results = [r for r in pii_results if r.score >= confidence_threshold]

        if filtered_results and self._ensure_presidio_initialized() and self.anonymizer:
            try:
                # Use anonymizer to redact detected entities
                redacted_text = self.anonymizer.anonymize(
                    text=text,
                    analyzer_results=filtered_results,
                    operators=self._build_redaction_operators(),
                )
            except Exception as exc:
                logger.warning(f"Error anonymizing text: {exc}")
                # Fallback: return original text
                return text, None, False

            # Build summary
            entity_counts = {}
            for result in filtered_results:
                entity_type = result.entity_type
                entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1

            redaction_summary_parts = [
                f"{count} {entity_type.lower()}{'s' if count > 1 else ''}"
                for entity_type, count in sorted(entity_counts.items())
            ]

        # Detect school names (separate from Presidio)
        if detect_school_names:
            school_matches = self._detect_school_name(redacted_text)

            if school_matches:
                # Sort by position (reverse order to maintain positions while replacing)
                for start, end, matched_text in sorted(
                    school_matches, key=lambda x: x[0], reverse=True
                ):
                    redacted_text = (
                        redacted_text[:start] + "[REDACTED]" + redacted_text[end:]
                    )

                school_count = len(school_matches)
                redaction_summary_parts.insert(
                    0,
                    f"{school_count} school name{'s' if school_count > 1 else ''}"
                )

        # Build final summary
        if redaction_summary_parts:
            redaction_summary = ", ".join(redaction_summary_parts) + " redacted"
            was_redacted = True
        else:
            redaction_summary = None
            was_redacted = False

        return redacted_text, redaction_summary, was_redacted

    def redact_attachment(
        self,
        attachment: Attachment,
        detect_school_names: bool = True,
        confidence_threshold: float = 0.5,
    ) -> Attachment:
        """Redact PII from an attachment.
        
        Creates a new Attachment object with redacted content.
        
        Args:
            attachment: Attachment to redact
            detect_school_names: Also detect and redact school names
            confidence_threshold: Minimum confidence for Presidio entities
            
        Returns:
            New Attachment object with redacted content and metadata
        """
        if not attachment.content:
            return attachment

        # Skip if already redacted
        if attachment.is_redacted:
            return attachment

        redacted_content, summary, was_redacted = self.redact_text(
            attachment.content,
            detect_school_names=detect_school_names,
            confidence_threshold=confidence_threshold,
        )

        return Attachment(
            name=attachment.name,
            mime_type=attachment.mime_type,
            content=redacted_content,
            is_redacted=was_redacted,
            redaction_summary=summary,
            size_bytes=attachment.size_bytes,
        )
