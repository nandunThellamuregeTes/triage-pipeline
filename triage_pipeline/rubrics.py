"""Deterministic scoring rubrics.

Kept in one editable place so a team can recalibrate without touching any
prompt (§4). The LLM never computes the final story-point number -- this
pure function does, and it is unit-tested independently of any model call.
"""
from __future__ import annotations

from .schemas import StoryPointFactors

# (inclusive_min, inclusive_max, points) -- edit this table to recalibrate.
_BANDS: list[tuple[int, int, int]] = [
    (0, 2, 1),
    (3, 4, 2),
    (5, 6, 3),
    (7, 9, 5),
    (10, 12, 8),
    (13, 15, 13),
]


def score_to_points(factors: StoryPointFactors) -> int:
    """Map five 0-3 sub-scores to a Fibonacci-ish story-point estimate.

    This is the ONLY place the final number is computed. Stage 2 only ever
    emits the raw sub-scores (``StoryPointFactors``); an LLM must never be
    trusted to do this arithmetic itself (§4).
    """
    total = (
        factors.change_size
        + factors.blast_radius_score
        + factors.localization_confidence_score
        + factors.testing_effort
        + factors.cross_team_coordination
    )
    for lo, hi, points in _BANDS:
        if lo <= total <= hi:
            return points
    # total is out of the documented 0-15 range (e.g. caller passed bad data).
    # Clamp rather than raise -- a scoring glitch should not crash the run.
    if total < _BANDS[0][0]:
        return _BANDS[0][2]
    return _BANDS[-1][2]
