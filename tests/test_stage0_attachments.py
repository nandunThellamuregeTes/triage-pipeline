"""Tests for Stage 0 (attachment analysis), §1.10.

ffmpeg is NOT installed in this sandbox, so these tests mock
``subprocess.run``/``shutil.which`` rather than requiring a real binary.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from triage_pipeline.schemas import Attachment, AttachmentFrameObservation
from triage_pipeline.stages import stage0_attachments as s0


def _video_attachment(name="recording.mp4", size_bytes=1024, path="/tmp/does-not-matter.mp4"):
    return Attachment(
        name=name,
        mime_type="video/mp4",
        content=f"[VIDEO_FILE_PATH]:{path}",
        size_bytes=size_bytes,
    )


class FakeLLMClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def structured_call(self, stage, model, system_prompt, user_content, output_schema,
                         tools=None, tool_executor=None, max_tool_turns=12, cwd=None):
        self.calls.append(stage)
        return self.response


def test_oversized_attachment_is_skipped():
    att = _video_attachment(size_bytes=100 * 1024 * 1024)
    result = s0.analyze_attachments(FakeLLMClient({}), [att], model="m", max_attachment_size=50 * 1024 * 1024)
    assert result.attachments_inspected is False
    assert result.attachments[0].inspected is False
    assert "exceeds size limit" in result.attachments[0].reason_not_inspected


def test_unsupported_attachment_type_is_skipped():
    att = Attachment(name="notes.docx", mime_type="application/msword", content="x", size_bytes=10)
    result = s0.analyze_attachments(FakeLLMClient({}), [att], model="m")
    assert result.attachments[0].inspected is False
    assert result.attachments[0].type == "other"
    assert "not supported" in result.attachments[0].reason_not_inspected


def test_missing_file_on_disk_is_recorded_not_missing_from_ticket():
    att = _video_attachment(path="/nonexistent/path/video.mp4")
    result = s0.analyze_attachments(FakeLLMClient({}), [att], model="m")
    inspection = result.attachments[0]
    assert inspection.inspected is False
    assert "not available on disk" in inspection.reason_not_inspected
    # It must still be recorded as an attachment, not simply absent.
    assert inspection.filename == att.name


def test_ffmpeg_missing_degrades_gracefully(tmp_path: Path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")
    att = _video_attachment(path=str(video_path))

    with patch("shutil.which", return_value=None):
        result = s0.analyze_attachments(FakeLLMClient({}), [att], model="m")

    assert result.attachments_inspected is False
    assert result.attachments[0].reason_not_inspected == "ffmpeg not available"


def test_frame_extraction_produces_timestamped_frames(tmp_path: Path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")
    out_dir = tmp_path / "frames"

    def fake_run(cmd, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        if "fixed_%04d.png" in cmd[-1]:
            for i in range(3):
                (out_dir / f"fixed_{i:04d}.png").write_bytes(b"\x89PNG")
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        # scene-change pass
        (out_dir / "scene_0000.png").write_bytes(b"\x89PNG")
        stderr = "[Parsed_showinfo] n:0 pts_time:2.500000\n"
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": stderr})()

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"), patch(
        "subprocess.run", side_effect=fake_run
    ):
        frames = s0.extract_video_frames(str(video_path), str(out_dir), fps=4, seconds=15, max_frames=60)

    assert len(frames) == 4  # 3 fixed-rate + 1 scene-change
    timestamps = sorted(ts for _, ts in frames)
    assert timestamps[0] == 0.0
    assert 2.5 in timestamps


def test_interval_calculation_from_known_timestamps():
    timeline = [
        AttachmentFrameObservation(timestamp=1.0, observation="scrollbar appears"),
        AttachmentFrameObservation(timestamp=1.5, observation="scrollbar disappears"),
        AttachmentFrameObservation(timestamp=2.0, observation="scrollbar appears"),
        AttachmentFrameObservation(timestamp=2.5, observation="scrollbar disappears"),
    ]
    interval_ms = s0.compute_periodic_interval_ms(timeline)
    # Consecutive diffs: 0.5, 0.5, 0.5 seconds => median 500ms.
    assert interval_ms == 500.0


def test_interval_calculation_ignores_non_appear_disappear_events():
    timeline = [
        AttachmentFrameObservation(timestamp=1.0, observation="scrollbar appears"),
        AttachmentFrameObservation(timestamp=3.0, observation="user typed a character"),
        AttachmentFrameObservation(timestamp=4.0, observation="scrollbar disappears"),
    ]
    interval_ms = s0.compute_periodic_interval_ms(timeline)
    assert interval_ms == 3000.0


def test_fewer_than_two_events_returns_none():
    timeline = [AttachmentFrameObservation(timestamp=1.0, observation="scrollbar appears")]
    assert s0.compute_periodic_interval_ms(timeline) is None


def test_video_end_to_end_sets_attachments_inspected_true(tmp_path: Path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")
    att = _video_attachment(path=str(video_path))

    def fake_run(cmd, **kwargs):
        last = cmd[-1]
        out_dir = Path(last).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        if "fixed_%04d.png" in last:
            (out_dir / "fixed_0000.png").write_bytes(b"\x89PNG")
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    fake_llm = FakeLLMClient(
        {
            "surface": "lesson plan editor",
            "editor_type_visible": "rich text",
            "browser_and_os_clues": "Chrome, 125% zoom",
            "symptom_timeline": [
                {"timestamp": 1.0, "observation": "scrollbar appears"},
                {"timestamp": 1.4, "observation": "scrollbar disappears"},
            ],
            "trigger_clues": "typing",
            "text_box_metrics": "",
            "not_visible": ["network tab"],
            "confidence": {"surface": 0.8},
        }
    )

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"), patch(
        "subprocess.run", side_effect=fake_run
    ):
        result = s0.analyze_attachments(fake_llm, [att], model="m")

    assert result.attachments_inspected is True
    assert result.attachments[0].inspected is True
    assert result.observed_in_attachments is not None
    assert result.observed_in_attachments.surface == "lesson plan editor"
    assert result.observed_periodic_interval_ms == pytest.approx(400.0)
    # Stage name was varied per-attachment so caching never collapses two
    # different attachments into one cached vision answer.
    assert fake_llm.calls == [f"stage0_attachments::{att.name}"]
