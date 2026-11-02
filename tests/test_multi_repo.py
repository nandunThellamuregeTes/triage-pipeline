"""Multi-repo workspace, routing and pipeline wiring."""
from pathlib import Path

import pytest

from triage_pipeline.llm.caching import _heads
from triage_pipeline.llm.stub import StubLLMClient
from triage_pipeline.orchestrator import run_pipeline
from triage_pipeline.render import render_triage_report
from triage_pipeline.schemas import NormalizedTicket
from triage_pipeline.workspace import Workspace, WorkspaceError, parse_repo_specs, route_services


@pytest.fixture
def services(tmp_path: Path) -> dict[str, str]:
    app = tmp_path / "staff-app"
    msg = tmp_path / "messaging"
    auth = tmp_path / "auth"
    for d in (app, msg, auth):
        d.mkdir()
    (app / "SendEmail.tsx").write_text("post('/ss/messaging/v1/bulk-email', body)\n")
    (msg / "controller.ts").write_text("router.post('/v1/bulk-email', BulkEmailController.send)\n")
    (auth / "login.ts").write_text("export function loginUser() {}\n")
    return {"staff-app": str(app), "messaging": str(msg), "auth": str(auth)}


def _workspace(services: dict[str, str]) -> Workspace:
    return parse_repo_specs([f"{name}={path}" for name, path in services.items()])


def test_parse_repo_specs_names_and_defaults(services):
    ws = parse_repo_specs([f"api={services['messaging']}", services["auth"]])
    assert [s.name for s in ws.services] == ["api", "auth"]
    assert ws.is_multi and ws.primary.name == "api"
    assert not parse_repo_specs([services["auth"]]).is_multi


def test_parse_repo_specs_rejects_bad_input(services, tmp_path):
    with pytest.raises(WorkspaceError):
        parse_repo_specs([str(tmp_path / "missing")])
    with pytest.raises(WorkspaceError):
        parse_repo_specs([f"x={services['auth']}", f"x={services['messaging']}"])


def test_route_picks_services_that_contain_the_route(services):
    ws = _workspace(services)
    matches = route_services(ws, ["https://x.example.com/ss/messaging/v1/bulk-email?id=5"], "")
    names = [m.service for m in matches]
    assert "auth" not in names
    assert set(names) == {"staff-app", "messaging"}
    assert all(m.evidence for m in matches)


def test_route_uses_identifiers_and_returns_nothing_without_hits(services):
    ws = _workspace(services)
    assert [m.service for m in route_services(ws, ["loginUser"], "")] == ["auth"]
    assert route_services(ws, ["totally_unrelated_symbol"], "nothing here") == []


def _ticket() -> NormalizedTicket:
    return NormalizedTicket(
        ticket_id="T-9", source="manual", product="demo", title="Bulk email fails",
        description_md="POST /ss/messaging/v1/bulk-email returns Fail", comments=[],
    )


def _stage1(signals):
    return {
        "ticket_facts": {
            "product": "demo", "symptoms": ["s"], "repro_steps": [], "missing_info": [],
            "technical_signals": signals,
        },
        "classification": {
            "verdict": "bug", "validated_severity": "Low", "confidence": 0.8, "reasoning_short": "r",
        },
    }


def _stage2(service):
    return {
        "candidate_areas": [
            {"path": "controller.ts", "line": 1, "confidence": 0.8, "reason": "r", "service": service}
        ],
        "root_cause_hypothesis": "h",
        "overall_confidence": 0.3,
    }


_WRITEUP = {"jira_comment_md": "c", "reporter_followup_md": None, "suggested_next_steps": []}


def test_pipeline_routes_and_passes_extra_dirs_to_stage2(services):
    stub = StubLLMClient(
        {
            "stage1_classify": _stage1(["/ss/messaging/v1/bulk-email"]),
            "stage2_investigate": _stage2("messaging"),
            "stage2_localize_sample1": _stage2("messaging"),
            "stage2_localize_sample2": _stage2("messaging"),
            "stage3_writeup": _WRITEUP,
        }
    )
    ws = _workspace(services)
    report = run_pipeline(stub, _ticket(), ws.primary.path, workspace=ws)

    assert report.verdict == "bug"
    assert {m.service for m in report.service_routing} == {"staff-app", "messaging"}
    stage2 = next(c for c in stub.calls if c["stage"] == "stage2_investigate")
    assert stage2["extra_dirs"] and len(stage2["extra_dirs"]) == 1
    assert report.affected_areas[0].service == "messaging"
    assert set(report.repo_heads) == set(services)
    rendered = render_triage_report(report)
    assert "## Services searched" in rendered
    assert "`messaging:controller.ts`" in rendered


def test_pipeline_without_a_service_match_downgrades_to_insufficient_info(services):
    stub = StubLLMClient({"stage1_classify": _stage1(["nothing_matches_anywhere"]), "stage3_writeup": _WRITEUP})
    ws = _workspace(services)
    ticket = _ticket()
    ticket.description_md = "It broke."
    report = run_pipeline(stub, ticket, ws.primary.path, workspace=ws)

    assert report.verdict == "insufficient_info"
    assert report.needs_human_review
    assert "stage2_investigate" not in [c["stage"] for c in stub.calls]
    assert "No configured repository matched" in render_triage_report(report)


def test_single_repo_workspace_is_unchanged(services):
    ws = parse_repo_specs([services["messaging"]])
    stub = StubLLMClient(
        {
            "stage1_classify": _stage1(["BulkEmailController"]),
            "stage2_investigate": _stage2(None),
            "stage2_localize_sample1": _stage2(None),
            "stage2_localize_sample2": _stage2(None),
            "stage3_writeup": _WRITEUP,
        }
    )
    report = run_pipeline(stub, _ticket(), ws.primary.path, workspace=ws)
    assert report.service_routing == [] and report.repo_heads == {}
    assert all(c["extra_dirs"] is None for c in stub.calls)
    assert "## Services searched" not in render_triage_report(report)


def test_cache_head_format_is_plain_for_single_repo_and_dict_for_many(tmp_path):
    assert _heads(None, None) is None
    assert _heads(str(tmp_path), None) is None  # not a git repo
    multi = _heads(str(tmp_path), [str(tmp_path / "other")])
    assert isinstance(multi, dict) and set(multi) == {str(tmp_path), str(tmp_path / "other")}
