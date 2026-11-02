"""Orchestrator tests (§12): short-circuit and happy-path branching, driven
by a StubLLMClient so no network/LLM credentials are required."""
from pathlib import Path

import pytest

from triage_pipeline.llm.stub import StubLLMClient
from triage_pipeline.orchestrator import ModelConfig, run_pipeline
from triage_pipeline.schemas import NormalizedTicket


def _ticket(ticket_id="T-1") -> NormalizedTicket:
    return NormalizedTicket(
        ticket_id=ticket_id,
        source="manual",
        product="demo",
        title="Something broke",
        description_md="It broke.",
        comments=[],
    )


@pytest.fixture
def tiny_repo(tmp_path: Path) -> str:
    (tmp_path / "mod.py").write_text("def foo():\n    return 1\n")
    return str(tmp_path)


def _stage1_dict(verdict: str, confidence: float = 0.9):
    return {
        "ticket_facts": {
            "product": "demo",
            "symptoms": ["s"],
            "repro_steps": [],
            "missing_info": [],
        },
        "classification": {
            "verdict": verdict,
            "validated_severity": "Low",
            "confidence": confidence,
            "reasoning_short": "because",
            "duplicate_of": "T-0" if verdict == "duplicate" else None,
        },
    }


def _stage3_dict():
    return {
        "jira_comment_md": "draft comment",
        "reporter_followup_md": None,
        "suggested_next_steps": ["Consider doing X"],
    }


def _localization_sample_fixtures(paths=("mod.py",)):
    """Fixture entries for Stage 2's N-1 extra localization-only cross-check
    samples (§1.6, default localization_samples=3 -> stage2_localize_sample1
    and stage2_localize_sample2)."""
    sample = {
        "candidate_areas": [
            {"path": p, "line": 1, "confidence": 0.8, "reason": "cross-check"} for p in paths
        ]
    }
    return {
        "stage2_localize_sample1": sample,
        "stage2_localize_sample2": sample,
    }


@pytest.mark.parametrize("verdict", ["not_a_bug", "insufficient_info", "duplicate"])
def test_short_circuit_skips_stage2(tiny_repo, verdict):
    stub = StubLLMClient(
        {
            "stage1_classify": _stage1_dict(verdict),
            "stage3_writeup": _stage3_dict(),
            # Deliberately NOT registering stage2_investigate -- if the
            # orchestrator tried to call it, StubLLMClient would raise KeyError.
        }
    )
    report = run_pipeline(stub, _ticket(), tiny_repo)

    assert report.verdict == verdict
    assert report.fix_proposals == []
    assert report.story_points is None
    assert report.affected_areas == []
    stage_names_called = [c["stage"] for c in stub.calls]
    assert "stage2_investigate" not in stage_names_called


def test_happy_path_runs_stage2_and_computes_story_points(tiny_repo):
    stub = StubLLMClient(
        {
            "stage1_classify": _stage1_dict("bug"),
            **_localization_sample_fixtures(),
            "stage2_investigate": {
                "candidate_areas": [
                    {"path": "mod.py", "line": 1, "confidence": 0.9, "reason": "looks right"}
                ],
                "root_cause_hypothesis": "foo() returns the wrong constant",
                "overall_confidence": 0.9,
                "fix_proposals": [
                    {
                        "summary": "fix foo",
                        "rationale": "because",
                        "diff_sketch": "- return 1\n+ return 2",
                        "suggested_tests": ["test_foo"],
                    }
                ],
                "blast_radius": "Low",
                "affected_areas": ["mod.py"],
                "impact_notes": "contained",
                "story_point_factors": {
                    "change_size": 1,
                    "blast_radius_score": 0,
                    "localization_confidence_score": 0,
                    "testing_effort": 1,
                    "cross_team_coordination": 0,
                    "justifications": {},
                },
            },
            "stage3_writeup": _stage3_dict(),
        }
    )
    report = run_pipeline(stub, _ticket(), tiny_repo)

    assert report.verdict == "bug"
    assert len(report.fix_proposals) == 1
    assert report.story_points == 1  # total=2 -> band 0-2 -> 1
    assert report.story_point_factors is not None
    assert len(report.affected_areas) == 1
    stage_names_called = [c["stage"] for c in stub.calls]
    assert "stage2_investigate" in stage_names_called
    assert "stage3_writeup" in stage_names_called


def test_below_threshold_bug_has_no_fix_proposals(tiny_repo):
    stub = StubLLMClient(
        {
            "stage1_classify": _stage1_dict("bug"),
            **_localization_sample_fixtures(),
            "stage2_investigate": {
                "candidate_areas": [
                    {"path": "mod.py", "line": 1, "confidence": 0.3, "reason": "a guess"}
                ],
                "root_cause_hypothesis": "not sure",
                "overall_confidence": 0.3,
                # Even if a backend misbehaved and still sent these, the
                # orchestrator/stage must discard them below threshold.
                "fix_proposals": [
                    {"summary": "x", "rationale": "y", "diff_sketch": "z", "suggested_tests": []}
                ],
                "blast_radius": "Low",
                "affected_areas": ["mod.py"],
                "impact_notes": "n/a",
                "story_point_factors": {
                    "change_size": 1,
                    "blast_radius_score": 1,
                    "localization_confidence_score": 1,
                    "testing_effort": 1,
                    "cross_team_coordination": 1,
                    "justifications": {},
                },
            },
            "stage3_writeup": _stage3_dict(),
        }
    )
    report = run_pipeline(stub, _ticket(), tiny_repo)

    assert report.fix_proposals == []
    assert report.blast_radius is None
    assert report.story_points is None
    assert report.story_point_factors is None
    assert report.needs_human_review is True
    # Candidate areas (the localization itself) are still surfaced.
    assert len(report.affected_areas) == 1


def _hypothesis_item(mechanism="X causes Y", trigger="deploy on 2024-01-01", confidence=0.6):
    return {
        "mechanism": mechanism,
        "trigger": trigger,
        "explains_why_now": "started after deploy",
        "explains_scope": "affects all users",
        "explains_intermittency": "n/a",
        "confirm_observation": "repro confirms",
        "refute_observation": "repro fails",
        "code_evidence": "mod.py:1",
        "confidence": confidence,
    }


def test_challenge_loop_runs_before_stage2_and_populates_report(tiny_repo):
    stub = StubLLMClient(
        {
            "stage1_classify": _stage1_dict("bug"),
            "stage1_5_challenge_1_5b_agent1_initial": {
                "hypotheses": [_hypothesis_item("A", confidence=0.5), _hypothesis_item("B", confidence=0.4)],
                "symbols_not_found": [],
                "evidence_gaps": ["no repro"],
                "what_would_distinguish_top_two": "a repro",
                "proposed_experiments": ["emulate fractional DPR"],
            },
            "stage1_5_challenge_1_5a_agent2_blind": {
                "hypotheses": [
                    _hypothesis_item("C", confidence=0.45),
                    _hypothesis_item("D", confidence=0.4),
                    _hypothesis_item("E", confidence=0.35),
                ],
                "symbols_not_found": [],
                "evidence_gaps": [],
                "what_would_distinguish_top_two": "",
                "proposed_experiments": [],
            },
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": {
                "mechanism_check": [],
                "unexplained_symptoms": [],
                "precedent_assessment": "",
                "missed_alternatives": [],
                "falsification_test": None,
                "mechanism_status": "SUPPORTED",
                "trigger_status": "SUPPORTED",
                "codebase_findings": ["mod.py:1"],
                "confidence_in_disproof": 0.1,
                "clarifying_questions": [],
            },
            "stage1_5_challenge_1_5d_agent1_regen_loop1": {
                "hypotheses": [_hypothesis_item("A (refined)", confidence=0.6)],
                "responses_to_challenge": [
                    {"point": "mechanism check", "response": "REBUTTED", "evidence": "mod.py:1"}
                ],
                "agreed_experiments": ["reproduce it"],
                "agreed_missing_evidence": [],
                "convergence_status": "CONVERGED",
                "overall_confidence": 0.6,
                "justification_for_changes": "no new flaws found",
            },
            **_localization_sample_fixtures(),
            "stage2_investigate": {
                "candidate_areas": [
                    {"path": "mod.py", "line": 1, "confidence": 0.9, "reason": "looks right"}
                ],
                "root_cause_hypothesis": "A (refined)",
                "overall_confidence": 0.8,
                "alternative_hypotheses": ["B"],
                "confidence_concerns": [],
                "how_to_confirm": ["reproduce it"],
                "fix_proposals": [
                    {
                        "summary": "fix foo",
                        "rationale": "because",
                        "diff_sketch": "- return 1\n+ return 2",
                        "suggested_tests": ["test_foo"],
                    }
                ],
                "blast_radius": "Low",
                "affected_areas": ["mod.py"],
                "impact_notes": "contained",
                "story_point_factors": {
                    "change_size": 1,
                    "blast_radius_score": 0,
                    "localization_confidence_score": 0,
                    "testing_effort": 1,
                    "cross_team_coordination": 0,
                    "justifications": {},
                },
            },
            "stage3_writeup": _stage3_dict(),
        }
    )
    config = ModelConfig(enable_challenge=True, challenge_max_loops=3)
    report = run_pipeline(stub, _ticket(), tiny_repo, config=config)

    stage_names_called = [c["stage"] for c in stub.calls]
    # Challenge loop stages must run BEFORE stage2_investigate.
    assert stage_names_called.index("stage1_5_challenge_1_5b_agent1_initial") < stage_names_called.index(
        "stage2_investigate"
    )
    assert "stage1_5_challenge_1_5a_agent2_blind" in stage_names_called
    assert "stage1_5_challenge_1_5c_agent2_challenge_loop1" in stage_names_called
    assert "stage1_5_challenge_1_5d_agent1_regen_loop1" in stage_names_called
    # Converged after one loop -- should not attempt a second loop iteration.
    assert "stage1_5_challenge_1_5c_agent2_challenge_loop2" not in stage_names_called

    assert report.was_challenged is True
    assert report.convergence_status == "CONVERGED"
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].mechanism == "A (refined)"
    assert report.agent1_initial_pass is not None
    assert len(report.agent1_initial_pass.hypotheses) == 2
    assert report.agent2_blind_pass is not None
    assert report.alternative_hypotheses == ["B"]
    assert report.how_to_confirm == ["reproduce it"]
    assert len(report.challenge_history) == 1
    assert report.needs_human_review is False
