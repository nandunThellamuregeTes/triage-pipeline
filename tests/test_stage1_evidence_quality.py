"""Tests for Stage 1's code-enforced evidence rubric (§1.8)."""
from __future__ import annotations

from triage_pipeline.schemas import TicketFacts
from triage_pipeline.stages.stage1_classify import compute_evidence_quality


def _facts(**overrides) -> TicketFacts:
    base = dict(product="p")
    base.update(overrides)
    return TicketFacts(**base)


def test_stack_trace_is_strong():
    facts = _facts(stack_trace_or_error="TypeError: x is undefined\n  at foo.js:12")
    assert compute_evidence_quality(facts) == "strong"


def test_confirmed_reproduction_is_strong():
    facts = _facts(is_reproducible_by_reporter=True, repro_steps=["open editor", "type fast"])
    assert compute_evidence_quality(facts) == "strong"


def test_reporter_stated_replication_text_confirming_is_strong():
    facts = _facts(reporter_stated_replication="Support reproduced it on a Windows laptop.")
    assert compute_evidence_quality(facts) == "strong"


def test_reporter_stated_replication_text_denying_is_not_strong():
    facts = _facts(
        reporter_stated_replication="Support could not reproduce it.",
        symptoms=["scrollbar flashes"],
        environment="Chrome 120",
    )
    assert compute_evidence_quality(facts) == "moderate"


def test_symptoms_plus_environment_is_moderate():
    facts = _facts(symptoms=["scrollbar flashes"], environment="Chrome 120, 125% zoom")
    assert compute_evidence_quality(facts) == "moderate"


def test_symptoms_plus_repro_steps_is_moderate():
    facts = _facts(symptoms=["scrollbar flashes"], repro_steps=["open editor"])
    assert compute_evidence_quality(facts) == "moderate"


def test_symptoms_plus_log_like_technical_signal_is_moderate():
    facts = _facts(symptoms=["crash"], technical_signals=["stack trace in console.log"])
    assert compute_evidence_quality(facts) == "moderate"


def test_symptoms_only_is_weak():
    facts = _facts(symptoms=["scrollbar flashes"])
    assert compute_evidence_quality(facts) == "weak"


def test_nothing_at_all_is_weak():
    facts = _facts()
    assert compute_evidence_quality(facts) == "weak"


def test_attachments_do_not_upgrade_rating_by_themselves():
    # Attachments showing the symptom (recorded in TicketFacts.attachments)
    # must NOT upgrade weak/moderate evidence on their own.
    weak = _facts(attachments=["recording.mp4 (video, inspected: scrollbar flashes)"])
    assert compute_evidence_quality(weak) == "weak"

    moderate = _facts(
        symptoms=["scrollbar flashes"],
        environment="Chrome 120",
        attachments=["recording.mp4 (video, inspected: scrollbar flashes)"],
    )
    assert compute_evidence_quality(moderate) == "moderate"
