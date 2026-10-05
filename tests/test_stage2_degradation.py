"""Stage 2 tool-budget exhaustion test (§12/§0.7): a tool-turn budget
timeout must degrade to a low-confidence result, never raise an unhandled
exception that aborts the run."""
from pathlib import Path

import pytest

from triage_pipeline.llm.base import LLMClient, ToolBudgetExhausted
from triage_pipeline.schemas import TicketFacts
from triage_pipeline.stages import stage2_investigate


class BudgetExhaustingLLMClient(LLMClient):
    def structured_call(self, stage, model, system_prompt, user_content, output_schema,
                         tools=None, tool_executor=None, max_tool_turns=12, cwd=None):
        raise ToolBudgetExhausted("ran out of turns", turns_used=max_tool_turns)


def test_stage2_degrades_gracefully_on_budget_exhaustion(tmp_path: Path):
    (tmp_path / "mod.py").write_text("def foo():\n    return 1\n")
    facts = TicketFacts(product="demo", technical_signals=["foo"])

    result = stage2_investigate.run(
        BudgetExhaustingLLMClient(), facts, "ticket title", str(tmp_path), model="m"
    )

    assert result.degraded is True
    assert result.overall_confidence <= 0.3
    assert result.fix_proposals == []
    assert result.story_point_factors is None
    assert "cut short" in result.root_cause_hypothesis.lower()
