"""PROJ-456 (synthetic regression fixture, §1.10) -- the most important
test in this suite. No real ticket exists; this is a hand-crafted,
realistic-but-synthetic end-to-end run exercising every stage (0 through 3)
via ``StubLLMClient``, with ffmpeg mocked (it is not installed in this
sandbox) so Stage 0's video frame extraction succeeds deterministically.

Scenario: a reporter attached a short screen recording showing a vertical
scrollbar flashing on/off while typing in a lesson-plan editor. Two
competing hypotheses survive the challenge loop -- a platform/DPI rendering
explanation and a user-environment (browser extension) explanation -- and
neither fully converges, which is an honest NEEDS_EVIDENCE/
MECHANISM_CONVERGED_TRIGGER_OPEN result, not a failure.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from triage_pipeline.llm.stub import StubLLMClient
from triage_pipeline.orchestrator import ModelConfig, run_pipeline
from triage_pipeline.render import render_triage_report
from triage_pipeline.schemas import Attachment, NormalizedTicket


@pytest.fixture
def repo(tmp_path: Path) -> str:
    editor_dir = tmp_path / "src" / "editor"
    editor_dir.mkdir(parents=True)
    (editor_dir / "scrollbar.py").write_text(
        "def render_scrollbar(content_height, viewport_height):\n"
        "    # fractional device-pixel-ratio can round this boundary differently\n"
        "    return content_height > viewport_height\n"
    )
    return str(tmp_path)


def _ticket(video_path: str) -> NormalizedTicket:
    return NormalizedTicket(
        ticket_id="PROJ-456",
        source="jira_api",
        product="lesson-plan-editor",
        title="Scrollbar flashes on/off while typing in lesson plan editor",
        description_md=(
            "Several teachers at one school report the vertical scrollbar in the "
            "lesson plan editor rapidly appears and disappears while they type a "
            "long lesson plan. It does not happen for short plans. A screen "
            "recording is attached showing the behavior."
        ),
        comments=[
            "Support: confirmed on Chrome 124 at 125% OS display scaling. "
            "Could not reproduce at 100% scaling on the same machine.",
            "Reporter: this started sometime this week, did not happen last month.",
        ],
        reported_priority="Medium",
        reported_labels=["ui", "editor"],
        attachments=[
            Attachment(
                name="scrollbar-flicker.mp4",
                mime_type="video/mp4",
                content=f"[VIDEO_FILE_PATH]:{video_path}",
                size_bytes=2 * 1024 * 1024,
            )
        ],
        custom_fields={"Environment": "Chrome 124, Windows, 125% display scaling"},
    )


def _fake_ffmpeg_run(cmd, **kwargs):
    last = cmd[-1]
    out_dir = Path(last).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    if "fixed_%04d.png" in last:
        for i in range(2):
            (out_dir / f"fixed_{i:04d}.png").write_bytes(b"\x89PNG")
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    # scene-change pass
    (out_dir / "scene_0000.png").write_bytes(b"\x89PNG")
    return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()


def _stage0_vision_fixture():
    return {
        "surface": "lesson plan editor, main text area",
        "editor_type_visible": "rich text toolbar (bold/italic/list)",
        "browser_and_os_clues": (
            "Chrome toolbar visible; thin overlay-style scrollbar; no zoom "
            "indicator shown; cannot confirm OS scaling from pixels alone"
        ),
        "symptom_timeline": [
            {"timestamp": 1.0, "observation": "scrollbar appears"},
            {"timestamp": 1.6, "observation": "scrollbar disappears"},
            {"timestamp": 2.2, "observation": "scrollbar appears"},
            {"timestamp": 2.8, "observation": "scrollbar disappears"},
            {"timestamp": 3.4, "observation": "scrollbar appears"},
        ],
        "trigger_clues": "user is actively typing in each frame; no idle periods, no resize",
        "text_box_metrics": "text box height appears constant across frames",
        "not_visible": ["browser zoom percentage", "any extension icons/toolbar"],
        "confidence": {"surface": 0.8, "browser_and_os_clues": 0.4},
    }


def _hyp(mechanism, trigger, confidence, code_evidence="src/editor/scrollbar.py:3"):
    return {
        "mechanism": mechanism,
        "trigger": trigger,
        "explains_why_now": "unknown -- no deploy/version change confirmed around first_reported_date",
        "explains_scope": "affects users at fractional display scaling, not all users",
        "explains_intermittency": "flicker only while typing, matching content-height boundary crossings",
        "confirm_observation": "emulate 125% OS scaling or set device-pixel-ratio=1.25 and reproduce",
        "refute_observation": "flicker persists at 100% scaling with no extensions enabled",
        "code_evidence": code_evidence,
        "confidence": confidence,
    }


def _stage1_fixture():
    return {
        "ticket_facts": {
            "product": "lesson-plan-editor",
            "symptoms": ["vertical scrollbar rapidly appears/disappears while typing"],
            "repro_steps": [],
            "environment": "Chrome 124, Windows, 125% display scaling",
            "reported_severity": "Medium",
            "stack_trace_or_error": None,
            "technical_signals": ["render_scrollbar", "content_height > viewport_height"],
            "identifiers": ["PROJ-456"],
            "missing_info": ["exact browser zoom percentage", "whether any extensions are installed"],
            "is_reproducible_by_reporter": False,
            "first_reported_date": "this week",
            "scope": "multiple teachers at one school",
            "consistency": "intermittent; only during typing in long lesson plans",
            "attachments": ["scrollbar-flicker.mp4 (video) -- inspected, see observed_in_attachments"],
            "reporter_stated_replication": "support reproduced at 125% scaling; could not reproduce at 100%",
            "stated_vs_inferred": "scaling-dependence is support's stated finding, not a reporter suspicion",
        },
        "classification": {
            "verdict": "bug",
            "validated_severity": "Low",
            "confidence": 0.6,
            "reasoning_short": (
                "Specific, scaling-correlated symptom with a video and a support "
                "repro at one scaling factor -- enough signal to search code with, "
                "but no confirmed root cause yet."
            ),
            "duplicate_of": None,
            "duplicate_candidates_considered": [],
            "evidence_gaps": [
                {
                    "gap": "exact browser zoom percentage at time of recording",
                    "why_it_matters": "distinguishes OS-scaling from in-browser zoom as the fractional-DPR source",
                },
                {
                    "gap": "list of installed browser extensions",
                    "why_it_matters": "a grammar/spellcheck overlay extension could independently explain the flicker",
                },
            ],
        },
    }


def _agent2_blind_fixture():
    return {
        "hypotheses": [
            _hyp(
                "Fractional device-pixel-ratio causes render_scrollbar's content_height "
                "vs viewport_height comparison to toggle across its own boundary on "
                "sub-pixel rounding each re-render",
                "triggered by 125% OS display scaling producing a non-integer device-pixel-ratio",
                0.45,
            ),
            _hyp(
                "A browser extension (e.g. grammar/spellcheck overlay) injects DOM nodes "
                "that change measured content height while the user types",
                "trigger is whichever extension is installed and enabled for this user/tenant",
                0.4,
                code_evidence="none",
            ),
            _hyp(
                "A recent deploy changed layout/CSS for the editor's scroll container",
                "trigger would be the deploy timing lining up with first_reported_date",
                0.3,
                code_evidence="none",
            ),
        ],
        "symbols_not_found": [
            {"symbol": "ScrollbarOverlayController", "searched_in": ["src/", "dist/"], "class_chain_followed": True}
        ],
        "evidence_gaps": ["exact browser zoom percentage", "installed extensions"],
        "what_would_distinguish_top_two": "reproduce in a private window with no extensions at 125% scaling",
        "proposed_experiments": [
            "emulate device-pixel-ratio=1.25 in a local browser and observe render_scrollbar's output",
            "test in a private/incognito window (disables most extensions) at the same scaling",
            "read deploy logs around first_reported_date for editor/CSS changes",
        ],
    }


def _agent1_initial_fixture():
    return {
        "hypotheses": [
            _hyp(
                "Fractional device-pixel-ratio causes render_scrollbar's boundary "
                "comparison to flip sub-pixel on each re-render while content height "
                "is near the viewport height",
                "unknown -- scaling alone does not explain why it started only this week",
                0.4,
            ),
        ],
        "symbols_not_found": [],
        "evidence_gaps": ["exact zoom percentage", "deploy history around first_reported_date"],
        "what_would_distinguish_top_two": "",
        "proposed_experiments": ["emulate device-pixel-ratio=1.25 locally"],
    }


def _agent2_challenge_fixture():
    return {
        "mechanism_check": [
            {
                "step": "render_scrollbar() must be called repeatedly during typing",
                "requirement": "a re-render on every keystroke",
                "verified": "yes",
                "contradiction": "",
            },
            {
                "step": "content_height and viewport_height must be within sub-pixel range of each other",
                "requirement": "boundary values that fractional rounding could flip",
                "verified": "unknown",
                "contradiction": "no code read confirms sub-pixel rounding is actually involved here; "
                "the comparison is a plain Python '>' on whatever values are passed in",
            },
        ],
        "unexplained_symptoms": [
            "does not explain why this started only this week rather than being present "
            "at 125% scaling all along"
        ],
        "precedent_assessment": "no prior fix or similar bug was cited to compare against",
        "missed_alternatives": [
            "a browser extension overlay was not ruled out -- no evidence either confirms or refutes it"
        ],
        "falsification_test": {
            "observation": "test in a private window with no extensions at 125% scaling",
            "available_now": False,
            "needs_affected_user": False,
        },
        "mechanism_status": "WEAKENED",
        "trigger_status": "UNTESTED",
        "codebase_findings": ["src/editor/scrollbar.py:3"],
        "confidence_in_disproof": 0.4,
        "clarifying_questions": [
            "was the scrollbar flicker present at 125% scaling before this week, or is it new?"
        ],
    }


def _agent1_regen_fixture():
    return {
        "hypotheses": [
            _hyp(
                "Fractional device-pixel-ratio causes render_scrollbar's boundary "
                "comparison to flip sub-pixel on each re-render",
                "unknown -- cannot explain why now without deploy/version history",
                0.35,
            ),
            _hyp(
                "A browser extension (e.g. grammar/spellcheck overlay) injects DOM nodes "
                "that change measured content height while typing",
                "trigger is whichever extension is installed for this user/tenant; "
                "unknown whether it was recently installed or updated",
                0.3,
                code_evidence="none",
            ),
        ],
        "responses_to_challenge": [
            {
                "point": "sub-pixel rounding not confirmed in code",
                "response": "CONCEDED",
                "evidence": "render_scrollbar does a plain '>' comparison; no rounding/DPR logic found in scrollbar.py",
            },
            {
                "point": "does not explain why now",
                "response": "OPEN",
                "evidence": "",
            },
            {
                "point": "browser extension alternative not ruled out",
                "response": "CONCEDED",
                "evidence": "no code or ticket evidence confirms or rules out an extension; kept as a live alternative",
            },
        ],
        "agreed_experiments": [
            "test in a private/incognito window at 125% scaling (rules extensions in/out without the affected user)",
            "emulate device-pixel-ratio=1.25 locally and observe render_scrollbar's actual inputs",
            "read deploy/release logs around first_reported_date for editor or CSS changes",
        ],
        "agreed_missing_evidence": [
            "exact browser zoom percentage at time of recording",
            "list of installed browser extensions for the affected users",
        ],
        "convergence_status": "NEEDS_EVIDENCE",
        "overall_confidence": 0.35,
        "justification_for_changes": (
            "Conceded that sub-pixel rounding is not actually confirmed in the code path; "
            "neither hypothesis explains why the symptom started only this week, so neither "
            "mechanism nor trigger converges. Kept both as an honest ranked list with shared "
            "next steps rather than forcing a false pick."
        ),
    }


def _stage2_localize_sample():
    return {
        "candidate_areas": [
            {
                "path": "src/editor/scrollbar.py",
                "line": 3,
                "confidence": 0.4,
                "reason": "boundary comparison the challenge loop flagged as unverified for DPR rounding",
            }
        ]
    }


def _stage2_fixture():
    return {
        "candidate_areas": [
            {
                "path": "src/editor/scrollbar.py",
                "line": 3,
                "confidence": 0.4,
                "reason": (
                    "render_scrollbar()'s content_height > viewport_height comparison is the only "
                    "place in the codebase that decides scrollbar visibility; unverified whether "
                    "fractional DPR actually reaches this comparison"
                ),
            }
        ],
        "symbols_not_found": [
            {
                "symbol": "ScrollbarOverlayController",
                "searched_in": ["src/", "dist/", "build/"],
                "class_chain_followed": True,
            }
        ],
        "root_cause_hypothesis": (
            "Most likely a fractional-DPR rendering interaction with render_scrollbar()'s boundary "
            "comparison, OR a browser extension altering measured content height -- neither is "
            "confirmed; see hypotheses from the challenge loop."
        ),
        "overall_confidence": 0.35,
        "alternative_hypotheses": [
            "A browser extension (grammar/spellcheck overlay) injects DOM nodes that change "
            "measured content height while typing"
        ],
        "confidence_concerns": [
            "neither hypothesis explains why the symptom started only this week",
            "exact browser zoom percentage and installed extensions were never obtained",
        ],
        "how_to_confirm": [
            "test in a private/incognito window at 125% scaling",
            "emulate device-pixel-ratio=1.25 locally and log render_scrollbar's actual inputs",
        ],
        "fix_proposals": [],
        "blast_radius": None,
        "affected_areas": ["src/editor/scrollbar.py"],
        "impact_notes": "no fix proposed -- confidence is below the gate threshold",
        "story_point_factors": None,
    }


def _stage3_fixture(system_prompt, user_content, tool_executor):
    payload = json.loads(user_content)
    fc = payload["final_confidence"]
    jira_comment = (
        f"**AI Triage (draft, advisory only).** Verdict: bug, evidence quality: weak, "
        f"final confidence: {fc:.0%}.\n\n"
        "Two hypotheses remain open: a fractional device-pixel-ratio rendering interaction "
        "with render_scrollbar(), and a browser-extension overlay changing measured content "
        "height. Neither explains why the symptom started only this week. Agreed next "
        "experiments: test in a private window at 125% scaling, emulate device-pixel-ratio=1.25 "
        "locally, and check deploy logs around first_reported_date. Symbols searched but not "
        "found: ScrollbarOverlayController (searched src/, dist/, build/, class chain followed). "
        "This is AI-generated and nothing has been executed or tested -- please verify."
    )
    reporter_followup = (
        "Thanks for the detailed report and the recording -- that was very helpful. "
        "Could you let us know your exact browser zoom percentage (not just OS display scaling) "
        "at the time of recording, and whether you have any browser extensions enabled "
        "(e.g. a grammar or spellcheck tool)? We'd also like to know whether this has ever "
        "happened before this week at the same display scaling."
    )
    return {
        "jira_comment_md": jira_comment,
        "reporter_followup_md": reporter_followup,
        "suggested_next_steps": [
            "Consider testing in a private/incognito window at 125% scaling",
            "Consider emulating device-pixel-ratio=1.25 locally against render_scrollbar()",
            "Consider checking deploy/release logs around first_reported_date",
        ],
    }


def test_scrollbar_synthetic_regression(repo, tmp_path):
    video_path = tmp_path / "scrollbar-flicker.mp4"
    video_path.write_bytes(b"fake mp4 bytes")
    ticket = _ticket(str(video_path))

    stub = StubLLMClient(
        {
            f"stage0_attachments::{ticket.attachments[0].name}": _stage0_vision_fixture(),
            "stage1_classify": _stage1_fixture(),
            "stage1_5_challenge_1_5a_agent2_blind": _agent2_blind_fixture(),
            "stage1_5_challenge_1_5b_agent1_initial": _agent1_initial_fixture(),
            "stage1_5_challenge_1_5c_agent2_challenge_loop1": _agent2_challenge_fixture(),
            "stage1_5_challenge_1_5d_agent1_regen_loop1": _agent1_regen_fixture(),
            "stage2_localize_sample1": _stage2_localize_sample(),
            "stage2_localize_sample2": _stage2_localize_sample(),
            "stage2_investigate": _stage2_fixture(),
            "stage3_writeup": _stage3_fixture,
        }
    )

    config = ModelConfig(enable_attachments=True, enable_challenge=True, challenge_max_loops=1)

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"), patch(
        "subprocess.run", side_effect=_fake_ffmpeg_run
    ):
        report = run_pipeline(stub, ticket, repo, config=config)

    # --- Stage 0: the video was genuinely inspected, never described as missing.
    assert report.attachments_inspected is True
    assert len(report.attachments_analyzed) == 1
    assert report.attachments_analyzed[0].inspected is True
    assert report.observed_in_attachments is not None
    assert report.observed_in_attachments.surface == "lesson plan editor, main text area"
    # Interval is code-computed from the model's timestamps, not model-stated:
    # consecutive appear/disappear diffs are 0.6, 0.6, 0.6, 0.6s -> 600ms median.
    assert report.observed_periodic_interval_ms == pytest.approx(600.0)

    # --- Stage 1.5: challenge loop produced a ranked list (not a single
    # hypothesis), with mechanism/trigger tracked separately.
    assert report.was_challenged is True
    assert report.loop_iterations_run >= 1
    assert len(report.hypotheses) >= 2
    assert report.convergence_status in ("NEEDS_EVIDENCE", "MECHANISM_CONVERGED_TRIGGER_OPEN")
    assert report.agreed_experiments  # non-empty, shared next steps
    assert report.agreed_missing_evidence

    # --- Due diligence: symbols_not_found shows the class chain was followed
    # and build output was searched, never a bare "undefined" claim.
    assert len(report.symbols_not_found) >= 1
    snf = report.symbols_not_found[0]
    assert snf.symbol == "ScrollbarOverlayController"
    assert snf.class_chain_followed is True
    assert "dist/" in snf.searched_in or "build/" in snf.searched_in

    # --- Confidence: code-computed and capped low given weak evidence,
    # no execution/repro, and an unconverged challenge loop.
    assert report.confidence <= 0.4
    assert len(report.confidence_caps_applied) >= 1

    # --- Stage 3: the reporter follow-up must not ask for the already-
    # attached video, and must not claim the bug was confirmed.
    followup = (report.reporter_followup_md or "").lower()
    assert "attach" not in followup or "recording" not in followup.split("attach")[0]
    assert "scrollbar-flicker.mp4" not in followup
    assert "confirmed" not in followup
    assert "this has been confirmed" not in followup
    # It DOES ask for genuinely still-missing evidence.
    assert "zoom" in followup or "extension" in followup

    # Internal comment cites the exact same final_confidence number.
    assert f"final confidence: {report.confidence:.0%}" in report.jira_comment_md

    # Rendering must not crash and must surface the attachments section.
    markdown = render_triage_report(report)
    assert "## Attachments" in markdown
    assert "scrollbar-flicker.mp4" in markdown
    assert "NOT inspected" not in markdown
