"""Affected-areas table rendering (§6): a CodeHit with no line must always
show a self-explanatory reason, never a bare unexplained '-'."""
from triage_pipeline.render import render_triage_report
from triage_pipeline.schemas import CodeHit, TriageReport


def _report(affected_areas):
    return TriageReport(
        ticket_id="T-1",
        verdict="bug",
        reported_severity="Medium",
        validated_severity="Medium",
        affected_areas=affected_areas,
    )


def test_line_present_renders_as_plain_number():
    report = _report([CodeHit(path="a.py", line=42, confidence=0.5, reason="why")])
    md = render_triage_report(report)
    assert "| `a.py` | 42 | 50% | why |" in md


def test_line_missing_with_explicit_reason_is_shown():
    report = _report(
        [
            CodeHit(
                path="b.py",
                line=None,
                confidence=0.3,
                reason="why",
                line_unavailable_reason="entry point, not a specific defect line",
            )
        ]
    )
    md = render_triage_report(report)
    assert "entry point, not a specific defect line" in md
    assert "| `b.py` | - _(entry point, not a specific defect line)_ | 30% | why |" in md


def test_line_missing_without_reason_falls_back_to_default_note():
    report = _report([CodeHit(path="c.py", line=None, confidence=0.2, reason="why")])
    md = render_triage_report(report)
    assert "not specified by model" in md
