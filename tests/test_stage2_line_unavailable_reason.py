"""Stage 2 line_unavailable_reason handling (§6): a CodeHit with no `line`
must always carry an explanatory reason -- either the model's own, or a
defensive fallback -- so the rendered report never shows a bare, unexplained
'-'."""
from pathlib import Path

from triage_pipeline.llm.base import LLMClient
from triage_pipeline.schemas import TicketFacts
from triage_pipeline.stages import stage2_investigate


class FixedOutputLLMClient(LLMClient):
    def __init__(self, candidate_areas):
        self._candidate_areas = candidate_areas

    def structured_call(self, stage, model, system_prompt, user_content, output_schema,
                         tools=None, tool_executor=None, max_tool_turns=12, cwd=None):
        return {
            "candidate_areas": self._candidate_areas,
            "root_cause_hypothesis": "some hypothesis",
            "overall_confidence": 0.6,
            "fix_proposals": [],
            "blast_radius": None,
            "affected_areas": [],
            "impact_notes": "",
            "story_point_factors": None,
        }


def _facts():
    return TicketFacts(product="demo", technical_signals=["foo"])


def test_line_with_value_has_no_unavailable_reason(tmp_path: Path):
    llm = FixedOutputLLMClient(
        [{"path": "a.py", "line": 10, "confidence": 0.5, "reason": "why"}]
    )
    result = stage2_investigate.run(llm, _facts(), "title", str(tmp_path), model="m")
    hit = result.candidate_areas[0]
    assert hit.line == 10
    assert hit.line_unavailable_reason is None


def test_null_line_keeps_models_own_reason(tmp_path: Path):
    llm = FixedOutputLLMClient(
        [
            {
                "path": "a.py",
                "line": None,
                "confidence": 0.3,
                "reason": "why",
                "line_unavailable_reason": "entry point, not a specific defect line",
            }
        ]
    )
    result = stage2_investigate.run(llm, _facts(), "title", str(tmp_path), model="m")
    hit = result.candidate_areas[0]
    assert hit.line is None
    assert hit.line_unavailable_reason == "entry point, not a specific defect line"


def test_null_line_without_models_reason_gets_defensive_fallback(tmp_path: Path):
    llm = FixedOutputLLMClient(
        [{"path": "a.py", "line": None, "confidence": 0.3, "reason": "why"}]
    )
    result = stage2_investigate.run(llm, _facts(), "title", str(tmp_path), model="m")
    hit = result.candidate_areas[0]
    assert hit.line is None
    assert hit.line_unavailable_reason == "not specified by model"
