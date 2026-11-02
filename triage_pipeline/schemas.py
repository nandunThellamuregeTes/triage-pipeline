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
EvidenceQuality = Literal["strong", "moderate", "weak"]
MechanismStepVerified = Literal["yes", "no", "unknown"]
ChallengeStatus = Literal["REFUTED", "WEAKENED", "UNTESTED", "SUPPORTED"]
ChallengeResponse = Literal["CONCEDED", "REBUTTED", "OPEN"]
# Mechanism and trigger converge separately (§1.2): mechanism can converge
# from code reading alone; trigger usually cannot without evidence.
# LOOP_NOT_RUN must NEVER be a final state in production -- orchestrator.py
# raises if it occurs (§1.3).
ConvergenceStatus = Literal[
    "CONVERGED", "MECHANISM_CONVERGED_TRIGGER_OPEN", "NEEDS_EVIDENCE", "LOOP_NOT_RUN"
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
class AttachmentInspection:
    """Stage 0's record of what happened to ONE attachment -- always
    present, even when the attachment could not be inspected, so later
    stages never describe an attached file as "missing" (§1.5/§1.9)."""
    filename: str
    type: str  # e.g. "video", "image", "pdf", "other"
    inspected: bool = False
    reason_not_inspected: Optional[str] = None  # e.g. "exceeds size limit", "ffmpeg not available"


@dataclass
class AttachmentFrameObservation:
    """One timestamped observation from a video's extracted frames (Stage 0
    §2.0 ``symptom_timeline``)."""
    timestamp: float  # seconds from the start of the video
    observation: str


@dataclass
class AttachmentAnalysis:
    """Structured output of Stage 0's vision pass over extracted
    attachment frames/images (§1.5, §2.0). Purely descriptive -- never
    speculates about causes."""
    surface: str = ""  # application area shown, plus visible tenant/version text
    editor_type_visible: str = ""
    browser_and_os_clues: str = ""
    symptom_timeline: list[AttachmentFrameObservation] = field(default_factory=list)
    trigger_clues: str = ""
    text_box_metrics: str = ""
    not_visible: list[str] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)  # per-field, 0-1


@dataclass
class SymbolNotFound:
    """One symbol an agent could not locate -- required to show due
    diligence was done (class chain + build output) before reporting
    "undefined" (§1.7/§2.2-2.6 CODE SEARCH RULE)."""
    symbol: str
    searched_in: list[str] = field(default_factory=list)  # e.g. ["source", "build output"]
    class_chain_followed: bool = False


@dataclass
class Stage0Result:
    """Full output of Stage 0 (attachment analysis), passed into every
    later stage as evidence of what the reporter saw -- still UNTRUSTED
    DATA, never an instruction (§1.5)."""
    attachments: list[AttachmentInspection] = field(default_factory=list)
    observed_in_attachments: Optional[AttachmentAnalysis] = None
    observed_periodic_interval_ms: Optional[float] = None  # median interval between repeated state changes (e.g. flashing/toggling), or None
    attachments_inspected: bool = False  # True iff at least one attachment was actually inspected


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
class EvidenceGap:
    """One specific missing item that would most change the diagnosis,
    e.g. 'browser version' or 'screen-recording contents', plus why it
    matters (Stage 1 §evidence_gaps)."""
    gap: str
    why_it_matters: str = ""


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
    # Richer fact-extraction fields (Stage 1 proposal): all "exactly as
    # stated; null when absent, never guessed".
    first_reported_date: Optional[str] = None
    scope: Optional[str] = None  # how many users/tenants affected, how identified
    consistency: Optional[str] = None  # consistent vs intermittent, stated trigger
    attachments: list[str] = field(default_factory=list)  # "filename (type)" notes, incl. what Stage 0 observed
    reporter_stated_replication: Optional[str] = None  # who could/couldn't reproduce it, on what system
    stated_vs_inferred: Optional[str] = None  # notes keeping reporter-stated facts separate from suspicions


@dataclass
class Classification:
    verdict: Verdict
    validated_severity: Severity
    confidence: float  # 0-1
    reasoning_short: str
    duplicate_of: Optional[str] = None
    duplicate_candidates_considered: list[DuplicateCandidate] = field(default_factory=list)
    evidence_quality: Optional[EvidenceQuality] = None
    evidence_gaps: list[EvidenceGap] = field(default_factory=list)


@dataclass
class CodeHit:
    path: str
    line: Optional[int]
    confidence: float  # 0-1 -- MUST be rendered, not discarded (§6)
    reason: str  # MUST be rendered, not discarded (§6)
    # Required whenever `line` is None -- explains WHY no single line is
    # cited (e.g. "entry point/routing class, not a specific defect line",
    # "ruled out, ran out of time to locate the real path"). Never let a
    # missing line render as an unexplained "-" in the report (§6).
    line_unavailable_reason: Optional[str] = None
    # Set only in multi-repo runs: which service the path is relative to.
    service: Optional[str] = None


@dataclass
class ServiceMatch:
    """One service the routing step selected for Stage 2, with its evidence."""
    service: str
    path: str
    score: int
    evidence: list[str] = field(default_factory=list)


@dataclass
class FixProposal:
    summary: str
    rationale: str
    diff_sketch: str
    suggested_tests: list[str] = field(default_factory=list)
    # §2.6: which hypothesis this fix addresses, and whether it would also
    # resolve the next-ranked alternative (so a reviewer can see at a
    # glance whether a fix is a narrow patch or likely to hold up even if
    # the #2 hypothesis turns out to be the real cause).
    addresses_hypothesis: str = ""
    also_resolves_next_alternative: Optional[bool] = None
    also_resolves_reason: str = ""


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
    overall_confidence: float = 0.0  # Stage 2's raw, self-reported value (pre-cap)
    fix_proposals: list[FixProposal] = field(default_factory=list)
    blast_radius: Optional[BlastRadius] = None
    affected_areas: list[str] = field(default_factory=list)
    impact_notes: str = ""
    story_point_factors: Optional[StoryPointFactors] = None  # None iff fix_proposals==[]
    degraded: bool = False  # True if the tool-turn budget was exhausted (§0.7)
    degraded_reason: Optional[str] = None
    # Stage 2 outputs: hypotheses that remain plausible alongside
    # root_cause_hypothesis, and the cheapest observations/experiments that
    # would confirm/refute the top hypothesis. Returned even when the
    # confidence gate blocks a fix.
    alternative_hypotheses: list[str] = field(default_factory=list)
    how_to_confirm: list[str] = field(default_factory=list)
    # Stage 2 must NOT subtract confidence itself (§1.4) -- it only lists
    # reasons confidence should be lower; the pipeline (compute_confidence,
    # orchestrator.py) applies the actual numeric caps and records them here.
    confidence_concerns: list[str] = field(default_factory=list)
    confidence_caps_applied: list[str] = field(default_factory=list)  # code-computed, see §1.4
    # CODE SEARCH RULE (§1.7/§2.6): symbols only reported "not found" after
    # following the class chain and searching build output.
    symbols_not_found: list[SymbolNotFound] = field(default_factory=list)
    # Multi-sample localization check (§1.6): agreement across N independent
    # localization samples, plus each sample's own top files (never averaged
    # into one answer).
    localization_agreement: Optional[float] = None  # 0-1, None if only one sample was run
    localization_samples: list[list[str]] = field(default_factory=list)  # top files per sample


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
    loop_iterations_run: int = 0  # must be >=1 whenever was_challenged is True (§1.1/§1.3)
    agent2_blind_pass: Optional[BlindPassResult] = None  # Agent 2's independent blind-pass hypotheses (1.5a)
    agent1_initial_pass: Optional[BlindPassResult] = None  # Agent 1's initial hypotheses, seeded by code hits (1.5b)
    hypotheses: list[Hypothesis] = field(default_factory=list)  # final ranked hypotheses from the challenge loop
    convergence_status: Optional[ConvergenceStatus] = None  # see ConvergenceStatus; never LOOP_NOT_RUN
    agreed_experiments: list[str] = field(default_factory=list)  # cheapest first, runnable without affected user
    agreed_missing_evidence: list[str] = field(default_factory=list)
    # Stage 2 outputs surfaced to the top level for rendering (§6)
    alternative_hypotheses: list[str] = field(default_factory=list)
    confidence_concerns: list[str] = field(default_factory=list)
    confidence_caps_applied: list[str] = field(default_factory=list)  # code-computed, see compute_confidence (§1.4)
    how_to_confirm: list[str] = field(default_factory=list)
    symbols_not_found: list[SymbolNotFound] = field(default_factory=list)
    localization_agreement: Optional[float] = None
    localization_samples: list[list[str]] = field(default_factory=list)
    evidence_quality: Optional[EvidenceQuality] = None
    evidence_gaps: list[EvidenceGap] = field(default_factory=list)
    # Stage 0 outputs (§1.5), surfaced so a reporter's attached video/image
    # is never described as "missing" when it was in fact inspected.
    attachments_analyzed: list[AttachmentInspection] = field(default_factory=list)
    observed_in_attachments: Optional[AttachmentAnalysis] = None
    observed_periodic_interval_ms: Optional[float] = None
    attachments_inspected: bool = False
    # Multi-repo runs only: services considered and the evidence for each.
    service_routing: list[ServiceMatch] = field(default_factory=list)
    repo_heads: dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class Hypothesis:
    """One root-cause hypothesis, scored and falsifiable -- never a bare
    string, so a human can see what would confirm/refute it (§1.5).

    ``mechanism`` and ``trigger`` are tracked and converged SEPARATELY
    (§1.2): mechanism (what happens) can often converge from code reading
    alone; trigger (why here and now) usually cannot without evidence."""
    mechanism: str  # what happens, step by step
    trigger: str = ""  # why it happens here and now; "unknown" if not inventable from evidence
    explains_why_now: str = ""  # why the symptom started when it did
    explains_scope: str = ""  # why only these users/tenants/browsers
    explains_intermittency: str = ""
    confirm_observation: str = ""  # observation that would CONFIRM this hypothesis
    refute_observation: str = ""  # observation that would REFUTE this hypothesis
    code_evidence: str = ""  # file:line verified with read_excerpt, or "none"
    confidence: float = 0.0  # 0-1
    status: Optional[ChallengeStatus] = None  # REFUTED | WEAKENED | UNTESTED | SUPPORTED, once evaluated


@dataclass
class BlindPassResult:
    """Output of an independent hypothesis-generation pass (Stage 1.5a/b):
    Agent 2's blind pass, or Agent 1's initial pass -- each formed without
    seeing the other's hypotheses."""
    hypotheses: list[Hypothesis] = field(default_factory=list)  # 2+ (Agent 1) or 3+ (Agent 2) distinct, ranked
    symbols_not_found: list[SymbolNotFound] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    what_would_distinguish_top_two: str = ""
    proposed_experiments: list[str] = field(default_factory=list)  # runnable without the affected user


@dataclass
class MechanismCheckStep:
    """One step of Agent 2's per-step mechanism check of Agent 1's top
    hypothesis (Stage 1.5c)."""
    step: str
    requirement: str  # what must be true for this step to happen
    verified: MechanismStepVerified  # "yes" | "no" | "unknown"
    contradiction: str = ""  # non-empty if this step contradicts a verifiable fact


@dataclass
class FalsificationTest:
    observation: str  # the single observation that would most cheaply refute the hypothesis
    available_now: bool = False  # is that observation available now (log, attachment, version, etc.)?
    needs_affected_user: bool = False  # True if it can only be run by the affected user reproducing the bug


@dataclass
class ChallengeResult:
    """Agent 2 (Challenge) output for a single iteration of the challenge
    loop. Mechanism and trigger are scored SEPARATELY (§1.2/§2.4)."""
    mechanism_check: list[MechanismCheckStep] = field(default_factory=list)
    unexplained_symptoms: list[str] = field(default_factory=list)
    precedent_assessment: str = ""
    missed_alternatives: list[str] = field(default_factory=list)
    falsification_test: Optional[FalsificationTest] = None
    mechanism_status: ChallengeStatus = "UNTESTED"  # REFUTED | WEAKENED | UNTESTED | SUPPORTED
    trigger_status: ChallengeStatus = "UNTESTED"
    codebase_findings: list[str] = field(default_factory=list)  # file:line references verified
    confidence_in_disproof: float = 0.0  # 0-1
    clarifying_questions: list[str] = field(default_factory=list)  # for Agent 1 to answer with evidence


@dataclass
class ChallengeResponseItem:
    """Agent 1's response to one specific point Agent 2 raised (Stage 1.5c)."""
    point: str
    response: ChallengeResponse  # CONCEDED | REBUTTED | OPEN
    evidence: str = ""  # required for REBUTTED; empty/explanatory for the others


@dataclass
class ChallengeRegenResult:
    """Agent 1's (Analyst) regeneration after Agent 2's challenge (Stage 1.5d)."""
    hypotheses: list[Hypothesis] = field(default_factory=list)  # ranked, updated
    responses_to_challenge: list[ChallengeResponseItem] = field(default_factory=list)
    agreed_experiments: list[str] = field(default_factory=list)  # cheapest first, runnable without affected user
    agreed_missing_evidence: list[str] = field(default_factory=list)
    convergence_status: ConvergenceStatus = "NEEDS_EVIDENCE"
    overall_confidence: float = 0.0  # Agent 1's raw value; final numeric confidence is code-computed (§1.4)
    justification_for_changes: str = ""


@dataclass
class ChallengeLoopIteration:
    """One iteration of the challenge loop: Agent 2's challenge + Agent 1's regeneration."""
    loop_num: int
    agent2_verdict: ChallengeResult
    agent1_regen: Optional[ChallengeRegenResult] = None
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
