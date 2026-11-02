"""Dedicated tests for ``compute_confidence`` (§1.4): every cap individually,
plus combinations, so the exact cap table in ``confidence.py`` is verified
independently of any single stage/orchestrator path."""
import pytest

from triage_pipeline.confidence import ConfidenceInputs, compute_confidence


def _inputs(**overrides):
    base = dict(
        raw_confidence=0.9,
        executed_or_reproduced=True,
        evidence_quality="strong",
        convergence_status="CONVERGED",
        explains_why_now=True,
        localization_agreement=0.9,
        key_symbol_not_located=False,
    )
    base.update(overrides)
    return ConfidenceInputs(**base)


def test_no_caps_applied_when_everything_is_clean():
    confidence, caps = compute_confidence(_inputs())
    assert confidence == 0.9
    assert caps == []


def test_not_executed_or_reproduced_caps_at_0_5():
    confidence, caps = compute_confidence(_inputs(executed_or_reproduced=False))
    assert confidence == 0.5
    assert len(caps) == 1
    assert "0.5" in caps[0] or "50%" in caps[0]


def test_not_executed_cap_is_noop_if_raw_confidence_already_below_cap():
    confidence, caps = compute_confidence(
        _inputs(raw_confidence=0.3, executed_or_reproduced=False)
    )
    assert confidence == 0.3
    assert caps == []


def test_weak_evidence_quality_caps_at_0_4():
    confidence, caps = compute_confidence(_inputs(evidence_quality="weak"))
    assert confidence == 0.4
    assert len(caps) == 1
    assert "weak" in caps[0]


def test_moderate_evidence_quality_is_not_capped():
    confidence, caps = compute_confidence(_inputs(evidence_quality="moderate"))
    assert confidence == 0.9
    assert caps == []


def test_needs_evidence_convergence_caps_at_0_4():
    confidence, caps = compute_confidence(_inputs(convergence_status="NEEDS_EVIDENCE"))
    assert confidence == 0.4
    assert len(caps) == 1
    assert "NEEDS_EVIDENCE" in caps[0]


def test_mechanism_converged_trigger_open_caps_at_0_4():
    confidence, caps = compute_confidence(
        _inputs(convergence_status="MECHANISM_CONVERGED_TRIGGER_OPEN")
    )
    assert confidence == 0.4
    assert len(caps) == 1
    assert "MECHANISM_CONVERGED_TRIGGER_OPEN" in caps[0]


def test_converged_status_is_not_capped():
    confidence, caps = compute_confidence(_inputs(convergence_status="CONVERGED"))
    assert confidence == 0.9
    assert caps == []


def test_no_challenge_loop_convergence_status_is_not_capped():
    # convergence_status is None when the challenge loop never ran at all --
    # must not be treated as an uncapped status (it isn't in the cap list),
    # but also must never be LOOP_NOT_RUN in practice (enforced upstream).
    confidence, caps = compute_confidence(_inputs(convergence_status=None))
    assert confidence == 0.9
    assert caps == []


def test_not_explains_why_now_reduces_by_0_1():
    confidence, caps = compute_confidence(_inputs(explains_why_now=False))
    assert confidence == 0.8
    assert len(caps) == 1
    assert "why the symptom started now" in caps[0]


def test_not_explains_why_now_floors_at_zero():
    confidence, caps = compute_confidence(
        _inputs(raw_confidence=0.05, explains_why_now=False)
    )
    assert confidence == 0.0
    assert len(caps) == 1


def test_low_localization_agreement_caps_at_0_35():
    confidence, caps = compute_confidence(_inputs(localization_agreement=0.3))
    assert confidence == 0.35
    assert len(caps) == 1
    assert "agree" in caps[0].lower()


def test_localization_agreement_exactly_0_5_is_not_capped():
    confidence, caps = compute_confidence(_inputs(localization_agreement=0.5))
    assert confidence == 0.9
    assert caps == []


def test_localization_agreement_none_is_not_capped():
    # None means fewer than 2 usable samples were run -- not a disagreement.
    confidence, caps = compute_confidence(_inputs(localization_agreement=None))
    assert confidence == 0.9
    assert caps == []


def test_key_symbol_not_located_reduces_by_0_1():
    confidence, caps = compute_confidence(_inputs(key_symbol_not_located=True))
    assert confidence == 0.8
    assert len(caps) == 1
    assert "symbol" in caps[0]


def test_key_symbol_not_located_floors_at_zero():
    confidence, caps = compute_confidence(
        _inputs(raw_confidence=0.05, key_symbol_not_located=True)
    )
    assert confidence == 0.0


def test_all_caps_combined_apply_in_order_and_floor_at_zero():
    confidence, caps = compute_confidence(
        _inputs(
            raw_confidence=0.95,
            executed_or_reproduced=False,  # caps at 0.5
            evidence_quality="weak",  # caps at 0.4 (tighter than 0.5)
            convergence_status="NEEDS_EVIDENCE",  # caps at 0.4 (no-op, already there)
            explains_why_now=False,  # -0.1 -> 0.3
            localization_agreement=0.1,  # caps at 0.35 (no-op, already below)
            key_symbol_not_located=True,  # -0.1 -> 0.2
        )
    )
    assert confidence == pytest.approx(0.2)
    # Every cap that actually changed the number must be recorded; the
    # localization cap is a no-op here (confidence already below 0.35) so it
    # must NOT appear.
    reasons = " | ".join(caps)
    assert "executed" in reasons
    assert "weak" in reasons
    # NEEDS_EVIDENCE's own cap (0.4) is a no-op here since weak evidence
    # already capped confidence at 0.4 -- only caps that actually CHANGE the
    # number are recorded (§1.4), so it must not appear.
    assert "NEEDS_EVIDENCE" not in reasons
    assert "started now" in reasons
    assert "symbol" in reasons
    assert "disagree" not in reasons.lower() and "localization" not in reasons.lower()


def test_raw_confidence_is_clamped_to_0_1_range():
    confidence, caps = compute_confidence(_inputs(raw_confidence=1.5))
    assert confidence == 1.0
    confidence, caps = compute_confidence(_inputs(raw_confidence=-0.5))
    assert confidence == 0.0


def test_caps_applied_list_is_empty_when_nothing_changed():
    # A maximally clean set of inputs with a low raw_confidence should apply
    # no caps at all -- the list must be empty, never a list of no-op entries.
    confidence, caps = compute_confidence(_inputs(raw_confidence=0.1))
    assert confidence == 0.1
    assert caps == []
