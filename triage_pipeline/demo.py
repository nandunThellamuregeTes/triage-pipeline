"""Fixtures wiring the bundled sample_data/ ticket + repo into a
``StubLLMClient`` for ``--demo`` (and reused by the end-to-end test, §12).

This is deterministic, offline, and zero-cost -- no network access or API
key required -- yet Stage 2's fixture still drives a REAL tool-use loop
against real ripgrep (via the tool_executor it's handed), so the demo
actually exercises the tool loop rather than faking it outright.
"""
from __future__ import annotations

from .llm.base import ToolCall
from .llm.stub import StubLLMClient


def _stage1_fixture(system_prompt, user_content, tool_executor):
    return {
        "ticket_facts": {
            "product": "billing-service",
            "symptoms": [
                "Orders of exactly 10 units do not receive the bulk discount",
                "Orders of 11+ units receive the discount correctly",
            ],
            "repro_steps": [
                "Order exactly 10 units of SKU-4821",
                "View the invoice preview",
                "Observe $0 discount instead of 10%",
            ],
            "environment": "staging",
            "reported_severity": "Medium",
            "stack_trace_or_error": None,
            "technical_signals": [
                "calculate_discount",
                "quantity > 10",
                "quantity >= 10",
            ],
            "identifiers": ["SKU-4821"],
            "missing_info": [],
            "is_reproducible_by_reporter": True,
        },
        "classification": {
            "verdict": "bug",
            "validated_severity": "Medium",
            "confidence": 0.9,
            "reasoning_short": (
                "Reporter and Sales both confirm the discount policy is "
                "inclusive of exactly 10 units; the described boundary "
                "behavior strongly suggests an off-by-one comparison bug."
            ),
            "duplicate_of": None,
        },
    }


def _stage2_fixture(system_prompt, user_content, tool_executor):
    # Drive a real tool-use loop against real ripgrep, exactly as a live
    # backend would, so the demo genuinely exercises Stage 2's tool loop.
    hits = tool_executor(ToolCall("search_code", {"query": "quantity > 10"}))
    bug_hit = hits[0] if hits else {"path": "billing/discount.py", "line": 10}
    excerpt = tool_executor(
        ToolCall("read_excerpt", {"path": bug_hit["path"], "line": bug_hit["line"]})
    )
    caller_hits = tool_executor(ToolCall("search_code", {"query": "calculate_discount"}))
    affected = sorted({h["path"] for h in caller_hits}) or [bug_hit["path"]]

    return {
        "candidate_areas": [
            {
                "path": bug_hit["path"],
                "line": bug_hit["line"],
                "confidence": 0.9,
                "reason": (
                    "Comparison `quantity > 10` excludes orders of exactly 10 units; "
                    "matches the reported boundary behavior exactly. Excerpt inspected:\n"
                    + excerpt[:200]
                ),
            }
        ],
        "root_cause_hypothesis": (
            "The discount eligibility check in calculate_discount() uses a strict "
            "`>` comparison against 10, excluding orders of exactly 10 units, "
            "contradicting the stated >=10 policy."
        ),
        "overall_confidence": 0.88,
        "fix_proposals": [
            {
                "summary": "Change the comparison from `>` to `>=` in calculate_discount().",
                "rationale": (
                    "Aligns the code with the confirmed policy (orders of 10+ units "
                    "qualify for the bulk discount)."
                ),
                "diff_sketch": (
                    "--- a/billing/discount.py\n"
                    "+++ b/billing/discount.py\n"
                    "@@\n"
                    "-    if quantity > 10:\n"
                    "+    if quantity >= 10:\n"
                ),
                "suggested_tests": [
                    "test_calculate_discount_applies_at_exactly_10_units",
                    "test_calculate_discount_applies_above_10_units",
                    "test_calculate_discount_withholds_below_10_units",
                ],
            }
        ],
        "blast_radius": "Low",
        "affected_areas": affected,
        "impact_notes": (
            "calculate_discount() currently has a single caller (build_invoice in "
            "billing/invoice.py); no other call sites were found."
        ),
        "story_point_factors": {
            "change_size": 0,
            "blast_radius_score": 0,
            "localization_confidence_score": 0,
            "testing_effort": 1,
            "cross_team_coordination": 0,
            "justifications": {
                "change_size": "Single-character operator change.",
                "blast_radius_score": "One confirmed caller, low-risk billing math.",
                "localization_confidence_score": "High confidence in the exact line.",
                "testing_effort": "Needs a boundary-value unit test at quantity==10.",
                "cross_team_coordination": "No other team's code is touched.",
            },
        },
    }


def _stage3_fixture(system_prompt, user_content, tool_executor):
    return {
        "jira_comment_md": (
            "**AI Triage (draft, advisory only):** This appears to be a genuine "
            "off-by-one bug in `calculate_discount()` -- the eligibility check uses "
            "`quantity > 10` instead of `quantity >= 10`, so orders of exactly 10 "
            "units are silently excluded from the bulk discount. Consider changing "
            "the comparison operator and adding a boundary-value test at "
            "quantity==10. Estimated effort: low (see story points below). This "
            "comment was AI-generated and nothing has been tested -- please verify "
            "before acting."
        ),
        "reporter_followup_md": None,
        "suggested_next_steps": [
            "Consider changing `quantity > 10` to `quantity >= 10` in billing/discount.py",
            "Consider adding a unit test for quantity exactly equal to 10",
            "Consider checking for other discount tiers with the same off-by-one pattern",
        ],
    }


def _stage4_fixture(system_prompt, user_content, tool_executor):
    return {
        "addresses_root_cause": "yes",
        "matches_fix_option": 1,
        "deviation_notes": "",
        "blast_radius_check": "within_predicted",
        "blast_radius_notes": "Only billing/discount.py was touched, as predicted.",
        "concerns": [],
        "confidence": 0.85,
        "summary_md": (
            "The diff changes `quantity > 10` to `quantity >= 10` in "
            "calculate_discount(), matching fix option 1 exactly. This is a static "
            "read of the diff, not test execution -- please run the test suite "
            "before merging."
        ),
        "suggested_next_steps": [
            "Consider running the billing unit test suite before merging",
        ],
    }


def demo_stub_client() -> StubLLMClient:
    return StubLLMClient(
        {
            "stage1_classify": _stage1_fixture,
            "stage2_investigate": _stage2_fixture,
            "stage3_writeup": _stage3_fixture,
            "stage4_verify": _stage4_fixture,
        }
    )
