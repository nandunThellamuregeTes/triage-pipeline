"""Checkpoint fingerprinting tests (§12) -- the single most important test
in the suite per §0.4/§7: changing ticket content, or advancing the repo's
HEAD between runs, must invalidate the relevant cached stage(s) rather than
silently reusing them.
"""
import subprocess
from pathlib import Path

from triage_pipeline.llm.base import LLMClient
from triage_pipeline.llm.caching import CachingLLMClient


class CountingLLMClient(LLMClient):
    """Returns an incrementing counter each call, so tests can tell whether
    the inner client was actually invoked again."""

    def __init__(self):
        self.call_count = 0

    def structured_call(self, stage, model, system_prompt, user_content, output_schema,
                         tools=None, tool_executor=None, max_tool_turns=12, cwd=None):
        self.call_count += 1
        return {"n": self.call_count}


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_rerun_with_same_ticket_reuses_cache(tmp_path):
    inner = CountingLLMClient()
    checkpoint = tmp_path / "cp.json"
    client = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    r1 = client.structured_call("stage1_classify", "m", "sp", "uc", {})
    client2 = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    r2 = client2.structured_call("stage1_classify", "m", "sp", "uc", {})
    assert r1 == r2
    assert inner.call_count == 1  # second run reused the cache, didn't call inner again


def test_changed_ticket_fingerprint_invalidates_cache(tmp_path):
    inner = CountingLLMClient()
    checkpoint = tmp_path / "cp.json"
    client = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    client.structured_call("stage1_classify", "m", "sp", "uc", {})

    client2 = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-2-EDITED")
    client2.structured_call("stage1_classify", "m", "sp", "uc", {})
    assert inner.call_count == 2  # edited ticket -> cache miss -> recomputed


def test_advancing_repo_head_invalidates_cwd_stage(tmp_path):
    repo = _git_repo(tmp_path)
    inner = CountingLLMClient()
    checkpoint = tmp_path / "cp.json"

    client = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    client.structured_call("stage2_investigate", "m", "sp", "uc", {}, cwd=str(repo))
    assert inner.call_count == 1

    # Rerun with no repo change: cache hit.
    client2 = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    client2.structured_call("stage2_investigate", "m", "sp", "uc", {}, cwd=str(repo))
    assert inner.call_count == 1

    # Advance HEAD.
    (repo / "b.txt").write_text("new file")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "advance"], cwd=repo, check=True)

    client3 = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    client3.structured_call("stage2_investigate", "m", "sp", "uc", {}, cwd=str(repo))
    assert inner.call_count == 2  # repo HEAD advanced -> stale -> recomputed


def test_downstream_stages_invalidated_when_upstream_is_stale(tmp_path):
    inner = CountingLLMClient()
    checkpoint = tmp_path / "cp.json"

    client = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    client.structured_call("stage1_classify", "m", "sp", "uc", {})
    client.structured_call("stage2_investigate", "m", "sp", "uc", {})
    client.structured_call("stage3_writeup", "m", "sp", "uc", {})
    assert inner.call_count == 3

    # Edited ticket invalidates stage1's cache; stage2/3 downstream of it
    # must not be silently served from the old run either.
    client2 = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-2")
    client2.structured_call("stage1_classify", "m", "sp", "uc", {})
    client2.structured_call("stage2_investigate", "m", "sp", "uc", {})
    client2.structured_call("stage3_writeup", "m", "sp", "uc", {})
    assert inner.call_count == 6


def test_fresh_flag_ignores_cache_entirely(tmp_path):
    inner = CountingLLMClient()
    checkpoint = tmp_path / "cp.json"
    client = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1")
    client.structured_call("stage1_classify", "m", "sp", "uc", {})

    client2 = CachingLLMClient(inner, str(checkpoint), ticket_fingerprint="fp-1", fresh=True)
    client2.structured_call("stage1_classify", "m", "sp", "uc", {})
    assert inner.call_count == 2


def test_redo_from_forces_recompute_of_stage_and_later():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        checkpoint = str(Path(d) / "cp.json")
        inner = CountingLLMClient()
        client = CachingLLMClient(inner, checkpoint, ticket_fingerprint="fp-1")
        client.structured_call("stage1_classify", "m", "sp", "uc", {})
        client.structured_call("stage2_investigate", "m", "sp", "uc", {})
        assert inner.call_count == 2

        # --redo-from 2 means stage index 1 (stage2_investigate) onward recomputes,
        # stage1 (index 0) should still be served from cache.
        client2 = CachingLLMClient(inner, checkpoint, ticket_fingerprint="fp-1", redo_from_stage=2)
        client2.structured_call("stage1_classify", "m", "sp", "uc", {})
        client2.structured_call("stage2_investigate", "m", "sp", "uc", {})
        assert inner.call_count == 3  # only stage2 recomputed
