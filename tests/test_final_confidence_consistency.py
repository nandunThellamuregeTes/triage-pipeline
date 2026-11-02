"""Integration test (§1.4): proves the pipeline's single code-computed
``final_confidence`` number appears consistently everywhere it is supposed
to -- the report header, the rendered Stage 2/root-cause section, and
Stage 3's write-up text -- never a different, independently-computed value
in any of those places."""
import json
from pathlib import Path

import pytest

from triage_pipeline.llm.stub import StubLLMClient
from triage_pipeline.orchestrator import ModelConfig, run_pipeline
from triage_pipeline.render import render_triage_report
from triage_pipeline.schemas import NormalizedTicket


@pytest.fixture
def tiny_repo(tmp_path: Path) -> str:
    (tmp_path / "mod.py").write_text("def foo():\n    return 1\n")
    return str(tmp_path)


def _ticket() -> NormalizedTicket:
    return NormalizedTicket(
        ticket_id="T-99",
        source="manual",
        product="demo",
        title="Something broke",
        description_md="It broke.",
        comments=[],
    )


def _stage1_dict():
    return {
        "ticket_facts": {
            "product": "demo",
            "symptoms": ["s"],
            "repro_steps": [],
            "missing_info": [],
            "is_reproducible_by_reporter": False,
        },
        "classification": {
            "verdict": "bug",
            "validated_severity": "Low",
            "confidence": 0.9,
            "evidence_quality": "weak",
            "reasoning_short": "because",
            "duplicate_of": None,
        },
    }


def _localization_sample_fixtures(paths=("mod.py",)):
    sample = {
        "candidate_areas": [
            {"path": p, "line": 1, "confidence": 0.8, "reason": "cross-check"} for p in paths
        ]
    }
    return {"stage2_localize_sample1": sample, "stage2_localize_sample2": sample}


def _stage3_echo_fixture(system_prompt, user_content, tool_executor):
    """Echoes back the pipeline-supplied ``final_confidence`` verbatim into
    the jira comment text, exactly as Stage 3's own real prompt instructs
    the model to do (§1.4's FINAL CONFIDENCE RULE) -- this is what we assert
    against below, standing in for a real LLM obeying that rule."""
    payload = json.loads(user_content)
    fc = payload["final_confidence"]
    return {
        "jira_comment_md": f"Draft comment. final confidence: {fc:.0%}.",
        "reporter_followup_md": None,
        "suggested_next_steps": ["Consider doing X"],
    }


def test_final_confidence_is_identical_in_header_rendered_section_and_writeup(tiny_repo):
    stub = StubLLMClient(
        {
            "stage1_classify": _stage1_dict(),
            **_localization_sample_fixtures(),
            "stage2_investigate": {
                "candidate_areas": [
                    {"path": "mod.py", "line": 1, "confidence": 0.95, "reason": "looks right"}
                ],
                "root_cause_hypothesis": "foo() returns the wrong constant",
                "overall_confidence": 0.95,
                "fix_proposals": [],
                "blast_radius": None,
                "affected_areas": ["mod.py"],
                "impact_notes": "contained",
            },
            "stage3_writeup": _stage3_echo_fixture,
        }
    )
    report = run_pipeline(stub, _ticket(), tiny_repo)

    # evidence_quality is "weak" -> compute_confidence caps at 0.4,
    # regardless of Stage 2's raw 0.95 self-reported confidence.
    assert report.confidence == pytest.approx(0.4)
    assert len(report.confidence_caps_applied) >= 1

    markdown = render_triage_report(report)
    # The rendered "Root cause" section must display the SAME number as the
    # header -- both read straight from report.confidence (final_confidence).
    assert f"Confidence:** {report.confidence:.0%}" in markdown
    assert f"confidence {report.confidence:.0%}" in markdown

    # Stage 3's write-up text must cite the identical number verbatim.
    assert f"final confidence: {report.confidence:.0%}" in report.jira_comment_md
    assert f"{report.confidence:.0%}" in markdown
