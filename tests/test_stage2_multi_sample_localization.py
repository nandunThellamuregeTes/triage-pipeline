"""Tests for Stage 2's multi-sample localization check (§1.6)."""
from pathlib import Path

from triage_pipeline.llm.base import LLMClient
from triage_pipeline.schemas import TicketFacts
from triage_pipeline.stages import stage2_investigate


def _facts():
    return TicketFacts(product="demo", technical_signals=["foo"])


class ScriptedLLMClient(LLMClient):
    """Returns a different, pre-scripted response per `stage` name -- lets
    us simulate independent localization samples agreeing or disagreeing."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.stages_called: list[str] = []

    def structured_call(self, stage, model, system_prompt, user_content, output_schema,
                         tools=None, tool_executor=None, max_tool_turns=12, cwd=None):
        self.stages_called.append(stage)
        if stage in self.responses:
            return self.responses[stage]
        raise KeyError(f"no scripted response for stage {stage!r}")


PRIMARY_RESPONSE = {
    "candidate_areas": [
        {"path": "a.py", "line": 10, "confidence": 0.8, "reason": "r"},
        {"path": "b.py", "line": 20, "confidence": 0.6, "reason": "r"},
    ],
    "root_cause_hypothesis": "h",
    "overall_confidence": 0.6,
    "fix_proposals": [],
    "blast_radius": None,
    "affected_areas": [],
    "impact_notes": "",
    "story_point_factors": None,
}


def test_full_agreement_across_samples_yields_agreement_one(tmp_path: Path):
    agreeing_sample = {
        "candidate_areas": [
            {"path": "a.py", "line": 10, "confidence": 0.9, "reason": "r"},
            {"path": "b.py", "line": 20, "confidence": 0.7, "reason": "r"},
        ]
    }
    llm = ScriptedLLMClient(
        {
            stage2_investigate.STAGE_NAME: PRIMARY_RESPONSE,
            "stage2_localize_sample1": agreeing_sample,
            "stage2_localize_sample2": agreeing_sample,
        }
    )
    result = stage2_investigate.run(llm, _facts(), "title", str(tmp_path), model="m")

    assert result.localization_agreement == 1.0
    assert len(result.localization_samples) == 3
    # Primary sample's own candidate_areas are untouched by the extra samples.
    assert [c.path for c in result.candidate_areas] == ["a.py", "b.py"]


def test_disagreement_across_samples_yields_low_agreement(tmp_path: Path):
    disagreeing_sample_1 = {"candidate_areas": [{"path": "x.py", "line": 1, "confidence": 0.9, "reason": "r"}]}
    disagreeing_sample_2 = {"candidate_areas": [{"path": "y.py", "line": 1, "confidence": 0.9, "reason": "r"}]}
    llm = ScriptedLLMClient(
        {
            stage2_investigate.STAGE_NAME: PRIMARY_RESPONSE,
            "stage2_localize_sample1": disagreeing_sample_1,
            "stage2_localize_sample2": disagreeing_sample_2,
        }
    )
    result = stage2_investigate.run(llm, _facts(), "title", str(tmp_path), model="m")

    assert result.localization_agreement is not None
    assert result.localization_agreement < 0.5


def test_stage_names_vary_per_sample_to_avoid_cache_collapse(tmp_path: Path):
    llm = ScriptedLLMClient(
        {
            stage2_investigate.STAGE_NAME: PRIMARY_RESPONSE,
            "stage2_localize_sample1": {"candidate_areas": []},
            "stage2_localize_sample2": {"candidate_areas": []},
        }
    )
    stage2_investigate.run(llm, _facts(), "title", str(tmp_path), model="m")
    assert llm.stages_called == [
        stage2_investigate.STAGE_NAME,
        "stage2_localize_sample1",
        "stage2_localize_sample2",
    ]


def test_localization_samples_never_merged_into_primary_candidate_areas(tmp_path: Path):
    other_sample = {"candidate_areas": [{"path": "zzz.py", "line": 1, "confidence": 0.99, "reason": "r"}]}
    llm = ScriptedLLMClient(
        {
            stage2_investigate.STAGE_NAME: PRIMARY_RESPONSE,
            "stage2_localize_sample1": other_sample,
            "stage2_localize_sample2": other_sample,
        }
    )
    result = stage2_investigate.run(llm, _facts(), "title", str(tmp_path), model="m")
    # The merged/averaged answer must never appear in the primary output.
    assert "zzz.py" not in [c.path for c in result.candidate_areas]
    assert result.localization_samples[0] == ["a.py", "b.py"]
    assert result.localization_samples[1] == ["zzz.py"]
    assert result.localization_samples[2] == ["zzz.py"]


def test_single_sample_has_no_agreement_value(tmp_path: Path):
    llm = ScriptedLLMClient({stage2_investigate.STAGE_NAME: PRIMARY_RESPONSE})
    result = stage2_investigate.run(
        llm, _facts(), "title", str(tmp_path), model="m", localization_samples=1
    )
    assert result.localization_agreement is None
    assert len(result.localization_samples) == 1
