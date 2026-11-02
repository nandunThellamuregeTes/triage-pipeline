"""Tests for the Stage 1.5 challenge loop (§1.1-§1.3): loop-always-runs,
mechanism/trigger split, ranked-list-not-dropped-alternative behavior, and
symbols_not_found propagation. Driven directly against
``stage1_5_challenge.run_challenge_loop`` with a ``StubLLMClient``."""
from pathlib import Path

import pytest

from triage_pipeline.llm.stub import StubLLMClient
from triage_pipeline.schemas import TicketFacts
from triage_pipeline.stages import stage1_5_challenge


@pytest.fixture
def tiny_repo(tmp_path: Path) -> str:
    (tmp_path / "mod.py").write_text("def foo():\n    return 1\n")
    return str(tmp_path)


def _facts(**overrides) -> TicketFacts:
    base = dict(
        product="demo",
        symptoms=["scrollbar flashes"],
        repro_steps=[],
        missing_info=[],
    )
    base.update(overrides)
    return TicketFacts(**base)


def _hyp(mechanism="M", trigger="T", confidence=0.5):
    return {
        "mechanism": mechanism,
        "trigger": trigger,
        "explains_why_now": "x",
        "explains_scope": "y",
        "explains_intermittency": "z",
        "confirm_observation": "c",
        "refute_observation": "r",
        "code_evidence": "mod.py:1",
        "confidence": confidence,
    }


def _blind_pass(hyps, symbols_not_found=None):
    return {
        "hypotheses": hyps,
        "symbols_not_found": symbols_not_found or [],
        "evidence_gaps": [],
        "what_would_distinguish_top_two": "",
        "proposed_experiments": [],
    }


def _challenge_result(mechanism_status="UNTESTED", trigger_status="UNTESTED"):
    return {
        "mechanism_check": [],
        "unexplained_symptoms": [],
        "precedent_assessment": "",
        "missed_alternatives": [],
        "falsification_test": None,
        "mechanism_status": mechanism_status,
        "trigger_status": trigger_status,
        "codebase_findings": [],
        "confidence_in_disproof": 0.2,
        "clarifying_questions": [],
    }


def _regen_result(hyps, convergence_status="NEEDS_EVIDENCE", responses=None, confidence=0.5):
    return {
        "hypotheses": hyps,
        "responses_to_challenge": responses or [],
        "agreed_experiments": [],
        "agreed_missing_evidence": [],
        "convergence_status": convergence_status,
        "overall_confidence": confidence,
        "justification_for_changes": "",
    }


def test_loop_always_runs_at_least_one_iteration_even_with_no_hypotheses(tiny_repo):
    # Both blind passes produce NO hypotheses at all -- the loop must still
    # run >=1 iteration, never short-circuit on "nothing to challenge".
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass([]),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result([]),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=1
    )
    assert result.loop_iterations_run >= 1
    assert result.convergence_status != "LOOP_NOT_RUN"
    stage_names = [c["stage"] for c in stub.calls]
    assert "stage1_5_challenge_1_5c_agent2_challenge_loop1" in stage_names
    assert "stage1_5_challenge_1_5d_agent1_regen_loop1" in stage_names


def test_loop_always_runs_even_when_confidence_would_be_below_any_threshold(tiny_repo):
    # Every reported confidence is far below any plausible Stage 2 gate
    # threshold -- the challenge loop must run regardless, since it is
    # NEVER gated by confidence (§1.1).
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass([_hyp(confidence=0.05)]),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp(confidence=0.05)]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result(
                [_hyp(confidence=0.05)], confidence=0.05
            ),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=1
    )
    assert result.loop_iterations_run >= 1


def test_loop_not_run_value_is_coerced_to_needs_evidence(tiny_repo):
    # Even if the model disobeys its explicit instruction and emits
    # LOOP_NOT_RUN, the module must defensively coerce it away (§1.3) --
    # the orchestrator's own `raise RuntimeError` is a second, independent
    # safety net, not the only one.
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass([_hyp("A")]),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp("B")]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result(
                [_hyp("B")], convergence_status="LOOP_NOT_RUN"
            ),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=1
    )
    assert result.convergence_status == "NEEDS_EVIDENCE"


def test_mechanism_and_trigger_fields_are_populated_separately(tiny_repo):
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass([_hyp("A", "trigger-A")]),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp("B", "trigger-B")]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(
                mechanism_status="SUPPORTED", trigger_status="UNTESTED"
            ),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result(
                [_hyp("B (refined)", "trigger-B refined")],
                convergence_status="MECHANISM_CONVERGED_TRIGGER_OPEN",
            ),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=1
    )
    assert result.hypotheses[0].mechanism == "B (refined)"
    assert result.hypotheses[0].trigger == "trigger-B refined"
    iteration = result.challenge_history[0]
    assert iteration.agent2_verdict.mechanism_status == "SUPPORTED"
    assert iteration.agent2_verdict.trigger_status == "UNTESTED"
    assert result.convergence_status == "MECHANISM_CONVERGED_TRIGGER_OPEN"


def test_ranked_list_not_dropped_when_agent1_has_one_and_agent2_has_three(tiny_repo):
    # Agent 1 (initial) produces a single hypothesis; Agent 2's blind pass
    # produces three alternatives. The loop must end with a RANKED LIST
    # (Agent 1's regenerated hypotheses), not a single hypothesis with the
    # alternatives silently dropped -- Agent 1's regen response is expected
    # to carry forward multiple hypotheses once it has seen Agent 2's blind
    # pass alternatives (via the 1.5c/1.5d exchange).
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass(
                [_hyp("Platform/DPI", confidence=0.45), _hyp("User extension", confidence=0.4),
                 _hyp("Recent deploy", confidence=0.35)]
            ),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp("App code defect", confidence=0.5)]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(
                mechanism_status="WEAKENED", trigger_status="UNTESTED"
            ),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result(
                [
                    _hyp("App code defect", confidence=0.4),
                    _hyp("Platform/DPI", confidence=0.35),
                    _hyp("User extension", confidence=0.3),
                ],
                convergence_status="NEEDS_EVIDENCE",
            ),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=1
    )
    assert len(result.hypotheses) == 3
    mechanisms = [h.mechanism for h in result.hypotheses]
    assert "App code defect" in mechanisms
    assert "Platform/DPI" in mechanisms
    assert "User extension" in mechanisms
    assert result.convergence_status == "NEEDS_EVIDENCE"  # a valid, honest end state, not a failure


def test_symbols_not_found_propagate_from_blind_passes_to_result(tiny_repo):
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass(
                [_hyp("A")],
                symbols_not_found=[
                    {"symbol": "FlashTimer", "searched_in": ["src/", "dist/"], "class_chain_followed": True}
                ],
            ),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp("B")]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result([_hyp("B")]),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=1
    )
    assert len(result.symbols_not_found) == 1
    assert result.symbols_not_found[0].symbol == "FlashTimer"
    assert result.symbols_not_found[0].class_chain_followed is True


def test_loop_exits_on_criterion_a_all_points_resolved_and_terminal_status(tiny_repo):
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass([_hyp("A")]),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp("B")]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(
                mechanism_status="SUPPORTED", trigger_status="SUPPORTED"
            ),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result(
                [_hyp("B (confirmed)")],
                convergence_status="CONVERGED",
                responses=[{"point": "mechanism check", "response": "REBUTTED", "evidence": "mod.py:1"}],
            ),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=3
    )
    assert result.loop_iterations_run == 1
    assert result.convergence_status == "CONVERGED"
    stage_names = [c["stage"] for c in stub.calls]
    assert "stage1_5_challenge_1_5c_agent2_challenge_loop2" not in stage_names


def test_loop_exits_on_criterion_b_two_stagnant_iterations(tiny_repo):
    # Neither iteration resolves all points (an OPEN response each time), and
    # the top hypothesis signature never changes across iterations -- the
    # "two consecutive iterations with no new facts/no ranking change" streak
    # requires a 3rd iteration to confirm the streak against iteration 1's
    # freshly-established baseline signature, so exit happens at iteration 3
    # here (it never reaches max_loops because the streak criterion fires
    # first in the 3-iteration config below).
    stagnant_regen = _regen_result(
        [_hyp("A", confidence=0.5)],
        convergence_status="NEEDS_EVIDENCE",
        responses=[{"point": "x", "response": "OPEN", "evidence": ""}],
        confidence=0.5,
    )
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass([_hyp("A")]),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp("A")]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": stagnant_regen,
            "stage1_5_challenge_1_5c_agent2_challenge_loop2": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop2": stagnant_regen,
            "stage1_5_challenge_1_5c_agent2_challenge_loop3": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop3": stagnant_regen,
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=5
    )
    assert result.loop_iterations_run == 3
    assert result.convergence_status in ("NEEDS_EVIDENCE", "MECHANISM_CONVERGED_TRIGGER_OPEN")
    stage_names = [c["stage"] for c in stub.calls]
    assert "stage1_5_challenge_1_5c_agent2_challenge_loop4" not in stage_names


def test_loop_respects_max_loops_as_a_hard_ceiling(tiny_repo):
    # Points stay OPEN every iteration and the ranked list keeps changing, so
    # neither exit criterion is ever met -- the loop must still stop at
    # max_loops rather than run forever.
    stub = StubLLMClient(
        {
            "stage1_5_challenge_1_5a_agent2_blind": _blind_pass([_hyp("A")]),
            "stage1_5_challenge_1_5b_agent1_initial": _blind_pass([_hyp("A")]),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _regen_result(
                [_hyp("A1", confidence=0.3)],
                convergence_status="NEEDS_EVIDENCE",
                responses=[{"point": "x", "response": "OPEN", "evidence": ""}],
            ),
            "stage1_5_challenge_1_5c_agent2_challenge_loop2": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop2": _regen_result(
                [_hyp("A2", confidence=0.4)],
                convergence_status="NEEDS_EVIDENCE",
                responses=[{"point": "y", "response": "OPEN", "evidence": ""}],
            ),
            "stage1_5_challenge_1_5c_agent2_challenge_loop3": _challenge_result(),
            "stage1_5_challenge_1_5d_agent1_regen_loop3": _regen_result(
                [_hyp("A3", confidence=0.5)],
                convergence_status="NEEDS_EVIDENCE",
                responses=[{"point": "z", "response": "OPEN", "evidence": ""}],
            ),
        }
    )
    result = stage1_5_challenge.run_challenge_loop(
        stub, _facts(), "Something broke", tiny_repo, model="strong", max_loops=3
    )
    assert result.loop_iterations_run == 3
    assert result.convergence_status != "LOOP_NOT_RUN"
