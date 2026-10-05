"""Unit tests for the deterministic story-point function (§12).

Every boundary of the 0-15 -> Fibonacci mapping, with no LLM involved.
"""
from triage_pipeline.rubrics import score_to_points
from triage_pipeline.schemas import StoryPointFactors


def _factors(total: int) -> StoryPointFactors:
    """Build a StoryPointFactors whose five sub-scores sum to ``total``."""
    parts = [0, 0, 0, 0, 0]
    remaining = total
    i = 0
    while remaining > 0:
        take = min(3, remaining)
        parts[i % 5] += take
        remaining -= take
        i += 1
    return StoryPointFactors(
        change_size=parts[0],
        blast_radius_score=parts[1],
        localization_confidence_score=parts[2],
        testing_effort=parts[3],
        cross_team_coordination=parts[4],
        justifications={},
    )


def test_band_0_to_2():
    for total in (0, 1, 2):
        assert score_to_points(_factors(total)) == 1


def test_band_3_to_4():
    for total in (3, 4):
        assert score_to_points(_factors(total)) == 2


def test_band_5_to_6():
    for total in (5, 6):
        assert score_to_points(_factors(total)) == 3


def test_band_7_to_9():
    for total in (7, 8, 9):
        assert score_to_points(_factors(total)) == 5


def test_band_10_to_12():
    for total in (10, 11, 12):
        assert score_to_points(_factors(total)) == 8


def test_band_13_to_15():
    for total in (13, 14, 15):
        assert score_to_points(_factors(total)) == 13


def test_max_possible_total_is_15():
    factors = StoryPointFactors(3, 3, 3, 3, 3, {})
    assert score_to_points(factors) == 13
