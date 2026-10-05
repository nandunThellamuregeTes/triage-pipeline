"""End-to-end demo test (§12): StubLLMClient + bundled sample ticket + repo,
exercising the full pipeline including Stage 2's tool loop against real
ripgrep, with no network access or API key required."""
from pathlib import Path

from triage_pipeline.adapters.rovo import RovoExportAdapter
from triage_pipeline.demo import demo_stub_client
from triage_pipeline.orchestrator import run_pipeline
from triage_pipeline.render import render_triage_report

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEMO_TICKET_PATH = PACKAGE_ROOT / "sample_data" / "demo_ticket.md"
DEMO_REPO_PATH = PACKAGE_ROOT / "sample_data" / "demo_repo"


def test_demo_identifies_the_bundled_bug():
    ticket = RovoExportAdapter(str(DEMO_TICKET_PATH)).fetch()
    assert ticket.ticket_id == "DEMO-101"

    client = demo_stub_client()
    report = run_pipeline(client, ticket, str(DEMO_REPO_PATH))

    assert report.verdict == "bug"
    assert report.confidence > 0
    assert any("discount.py" in hit.path for hit in report.affected_areas)
    # Per-area confidence must be present, not a bare path list (§6).
    assert all(0.0 <= hit.confidence <= 1.0 for hit in report.affected_areas)
    assert all(hit.reason for hit in report.affected_areas)
    assert report.root_cause_hypothesis is not None
    assert report.story_points is not None
    assert report.fix_proposals

    # Stage 2's tool loop against real ripgrep was actually exercised, not faked.
    stage2_calls = [c for c in client.calls if c["stage"] == "stage2_investigate"]
    assert len(stage2_calls) == 1

    markdown = render_triage_report(report)
    assert "hypothesis" in markdown.lower()
    assert "Disclaimer" in markdown
    assert "discount.py" in markdown
