"""Typed data contracts shared between pipeline stages.

Per design principle §0.2, stages pass these structured objects to each
other -- never a free-text restatement of a previous stage's output. Per
§0.3, if a field exists here, the render layer (see ``render.py``) must
either display it or document an explicit reason not to.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

Severity = Literal["Critical", "High", "Medium", "Low"]
Verdict = Literal["bug", "not_a_bug", "duplicate", "insufficient_info"]
BlastRadius = Literal["Low", "Medium", "High", "Critical"]
AddressesRootCause = Literal["yes", "partially", "no", "unclear"]
BlastRadiusCheck = Literal[
    "within_predicted", "exceeds_predicted", "narrower_than_predicted", "not_assessed"
]


def _asdict(obj) -> dict:
    """dataclasses.asdict, but keeps nested dataclasses as plain dicts."""
    return asdict(obj)


@dataclass
class Attachment:
    """A file attachment from a Jira ticket, with optional redaction metadata."""
    name: str  # original filename
    mime_type: str  # e.g., "text/plain", "image/png", "application/json"
    content: str  # text content (extracted from file or OCR'd from image)
    is_redacted: bool = False  # True if PII redaction was applied
    redaction_summary: Optional[str] = None  # e.g., "2 student names, 1 school name redacted"
    size_bytes: Optional[int] = None  # original file size before extraction


@dataclass
class LinkedTicket:
    key: str
    link_type: str  # "duplicates", "blocks", "relates to", "subtask", "parent", etc.
    title: str
    status: Optional[str]
    description_md: str
    comments: list[str] = field(default_factory=list)


@dataclass
class NormalizedTicket:
    ticket_id: str
    source: Literal["jira_api", "rovo_export", "manual"]
    product: str
    title: str
    description_md: str
    comments: list[str] = field(default_factory=list)
    reported_priority: Optional[str] = None
    reported_labels: list[str] = field(default_factory=list)
    reporter: Optional[str] = None
    linked_tickets: list[LinkedTicket] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    # Jira custom fields whose *name* matched a triage-relevant pattern (see
    # jira.CUSTOM_FIELD_NAME_PATTERNS), keyed by human-readable field name --
    # e.g. {"Steps to reproduce issue": "...", "Environment": "..."}.
    custom_fields: dict[str, str] = field(default_factory=dict)

    def content_fingerprint(self) -> str:
        """Deterministic hash of everything that should invalidate a cache
        entry if it changes. Used by CachingLLMClient (§7)."""
        import hashlib
        import json

        payload = {
            "ticket_id": self.ticket_id,
            "product": self.product,
            "title": self.title,
            "description_md": self.description_md,
            "comments": self.comments,
            "reported_priority": self.reported_priority,
            "reported_labels": sorted(self.reported_labels),
            "linked_tickets": [
                {
                    "key": lt.key,
                    "link_type": lt.link_type,
                    "title": lt.title,
                    "status": lt.status,
                    "description_md": lt.description_md,
                    "comments": lt.comments,
                }
                for lt in self.linked_tickets
            ],
            "attachments": [
                {
                    "name": att.name,
                    "mime_type": att.mime_type,
                    "content_hash": hash(att.content) if att.content else None,
                    "is_redacted": att.is_redacted,
                }
                for att in self.attachments
            ],
            "custom_fields": dict(sorted(self.custom_fields.items())),
        }
        blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


@dataclass
class DuplicateCandidate:
    ticket_ref: str  # a key or a title from recent_ticket_titles
    similarity_score: float  # 0-1, computed deterministically (§3)
    source: Literal["explicit_jira_link", "title_similarity"]


@dataclass
class TicketFacts:
    product: str
    symptoms: list[str] = field(default_factory=list)
    repro_steps: list[str] = field(default_factory=list)  # empty is fine
    environment: Optional[str] = None
    reported_severity: Optional[Severity] = None
    stack_trace_or_error: Optional[str] = None
    technical_signals: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    is_reproducible_by_reporter: Optional[bool] = None


@dataclass
class Classification:
    verdict: Verdict
    validated_severity: Severity
    confidence: float  # 0-1
    reasoning_short: str
    duplicate_of: Optional[str] = None
    duplicate_candidates_considered: list[DuplicateCandidate] = field(default_factory=list)


@dataclass
class CodeHit:
    path: str
    line: Optional[int]
    confidence: float  # 0-1 -- MUST be rendered, not discarded (§6)
    reason: str  # MUST be rendered, not discarded (§6)


@dataclass
class FixProposal:
    summary: str
    rationale: str
    diff_sketch: str
    suggested_tests: list[str] = field(default_factory=list)


@dataclass
class StoryPointFactors:
    change_size: int  # 0-3
    blast_radius_score: int  # 0-3
    localization_confidence_score: int  # 0-3 (inverse of model's confidence)
    testing_effort: int  # 0-3
    cross_team_coordination: int  # 0-3
    justifications: dict[str, str] = field(default_factory=dict)


@dataclass
class InvestigationResult:
    candidate_areas: list[CodeHit] = field(default_factory=list)
    root_cause_hypothesis: str = ""  # "hypothesis" on purpose -- never implies certainty
    overall_confidence: float = 0.0
    fix_proposals: list[FixProposal] = field(default_factory=list)
    blast_radius: Optional[BlastRadius] = None
    affected_areas: list[str] = field(default_factory=list)
    impact_notes: str = ""
    story_point_factors: Optional[StoryPointFactors] = None  # None iff fix_proposals==[]
    degraded: bool = False  # True if the tool-turn budget was exhausted (§0.7)
    degraded_reason: Optional[str] = None


@dataclass
class TriageReport:
    ticket_id: str
    verdict: Verdict
    reported_severity: Optional[str]
    validated_severity: Optional[str]
    affected_areas: list[CodeHit] = field(default_factory=list)  # full objects, see §6
    fix_proposals: list[FixProposal] = field(default_factory=list)
    blast_radius: Optional[str] = None
    story_points: Optional[int] = None  # computed deterministically, see §4
    story_point_factors: Optional[StoryPointFactors] = None
    confidence: float = 0.0
    needs_human_review: bool = False
    jira_comment_md: str = ""
    reporter_followup_md: Optional[str] = None
    suggested_next_steps: list[str] = field(default_factory=list)
    duplicate_of: Optional[str] = None
    duplicate_candidates_considered: list[DuplicateCandidate] = field(default_factory=list)
    root_cause_hypothesis: Optional[str] = None
    missing_info: list[str] = field(default_factory=list)
    # Top-of-report summary fields -- sourced directly from the original
    # NormalizedTicket/TicketFacts/Classification, no extra LLM call needed.
    ticket_title: str = ""
    symptoms: list[str] = field(default_factory=list)
    reported_priority: Optional[str] = None
    reported_labels: list[str] = field(default_factory=list)
    reasoning_short: str = ""
    # Challenge loop fields (Stage 1.5)
    was_challenged: bool = False  # True if challenge loop ran
    challenge_history: list[ChallengeLoopIteration] = field(default_factory=list)  # all loop iterations


@dataclass
class ChallengeResult:
    """Agent 2 (Challenge) output for a single iteration of the challenge loop."""
    can_disprove: bool  # True if Agent 2 found credible flaws
    flaws_found: list[str] = field(default_factory=list)  # specific flaws or gaps
    alternative_hypotheses: list[str] = field(default_factory=list)  # possible alternatives
    codebase_findings: list[str] = field(default_factory=list)  # file:line references
    confidence_in_disproof: float = 0.0  # 0-1: how confident is Agent 2 in its verdict
    clarifying_questions: list[str] = field(default_factory=list)  # for Agent 1 to address


@dataclass
class ChallengeLoopIteration:
    """One iteration of the challenge loop: Agent 2 verdict + Agent 1 regeneration."""
    loop_num: int
    agent2_verdict: ChallengeResult
    agent1_regenerated_hypothesis: Optional[str] = None  # if Agent 2 found flaws
    convergence_status: Optional[str] = None  # "improved", "no_change", "diverged"
    confidence_before: float = 0.0
    confidence_after: float = 0.0


@dataclass
class FixVerification:
    addresses_root_cause: AddressesRootCause
    matches_fix_option: Optional[int]
    deviation_notes: str
    blast_radius_check: BlastRadiusCheck
    blast_radius_notes: str
    suggested_tests_found: list[str] = field(default_factory=list)  # deterministic, §9
    concerns: list[str] = field(default_factory=list)
    confidence: float = 0.0
    summary_md: str = ""
    suggested_next_steps: list[str] = field(default_factory=list)
