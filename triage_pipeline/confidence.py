"""Single source of truth for pipeline confidence (§1.4).

Direct, mandatory fix for a confirmed defect: a prior version computed
confidence inconsistently (header said one number, Stage 2 silently
subtracted an undefined extra amount, Stage 3 invented its own per-
hypothesis numbers). Every numeric adjustment to confidence now happens
HERE, in plain deterministic Python, never inside an LLM prompt. Every
stage downstream (header, Stage 2 output, Stage 3 write-up) must display
this exact number -- never restate or recompute it.

Each cap below is independently applied (min-of-all / subtract-all) and
logged with a human-readable reason string, so the arithmetic is fully
auditable (§6).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .schemas import ConvergenceStatus, EvidenceQuality

# convergence_status values that mean "the challenge loop did not reach a
# fully-converged answer" -- both still cap confidence (§1.4).
_UNCONVERGED_STATUSES = ("NEEDS_EVIDENCE", "MECHANISM_CONVERGED_TRIGGER_OPEN")


@dataclass
class ConfidenceInputs:
    """Everything ``compute_confidence`` needs to know to apply the §1.4
    cap table. Built by the orchestrator from Stage 1/1.5/2 outputs --
    never from an LLM-authored numeric adjustment."""
    raw_confidence: float  # Stage 2's (or, pre-Stage-2, the challenge loop's) self-reported confidence
    executed_or_reproduced: bool = False  # True iff something was actually run/reproduced, not just read
    evidence_quality: Optional[EvidenceQuality] = None
    convergence_status: Optional[ConvergenceStatus] = None
    explains_why_now: bool = True  # False if the top hypothesis could not explain "why now"
    localization_agreement: Optional[float] = None  # 0-1, from the multi-sample localization check (§1.6)
    key_symbol_not_located: bool = False  # True iff a symbol was genuinely unresolved after full diligence (§1.7)


def compute_confidence(inputs: ConfidenceInputs) -> tuple[float, list[str]]:
    """Deterministically compute ``final_confidence`` from ``inputs``.

    Returns ``(final_confidence, caps_applied)`` where ``caps_applied`` is a
    list of human-readable reason strings, one per cap/reduction that
    actually changed the number (so a report that applied no caps has an
    empty list, not a list of no-op entries).
    """
    confidence = max(0.0, min(1.0, inputs.raw_confidence))
    caps_applied: list[str] = []

    if not inputs.executed_or_reproduced:
        cap = 0.5
        if confidence > cap:
            confidence = cap
            caps_applied.append(f"capped at {cap:.0%}: nothing was executed and no reproduction exists")

    if inputs.evidence_quality == "weak":
        cap = 0.4
        if confidence > cap:
            confidence = cap
            caps_applied.append(f"capped at {cap:.0%}: evidence quality is weak")

    if inputs.convergence_status in _UNCONVERGED_STATUSES:
        cap = 0.4
        if confidence > cap:
            confidence = cap
            caps_applied.append(
                f"capped at {cap:.0%}: challenge loop convergence_status is {inputs.convergence_status}"
            )

    if not inputs.explains_why_now:
        before = confidence
        confidence = max(0.0, confidence - 0.1)
        if confidence != before:
            caps_applied.append(
                "reduced by 10 points: the leading hypothesis does not explain why the symptom started now"
            )

    if inputs.localization_agreement is not None and inputs.localization_agreement < 0.5:
        cap = 0.35
        if confidence > cap:
            confidence = cap
            caps_applied.append(
                f"capped at {cap:.0%}: localization disagrees across independent samples "
                f"(agreement={inputs.localization_agreement:.0%})"
            )

    if inputs.key_symbol_not_located:
        before = confidence
        confidence = max(0.0, confidence - 0.1)
        if confidence != before:
            caps_applied.append(
                "reduced by 10 points: a key code symbol could not be located despite following the "
                "class chain and searching build output"
            )

    confidence = max(0.0, min(1.0, confidence))
    return confidence, caps_applied
