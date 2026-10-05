# AI-Assisted Bug Triage Pipeline

A production-quality prototype that turns a Jira bug ticket into a
structured, human-reviewable triage report (verdict, severity, root cause,
fix proposal(s), blast radius, story-point estimate, draft Jira comment),
plus an independent on-demand stage that checks a real code change against
that earlier diagnosis.

**Everything this pipeline produces is advisory.** Nothing here auto-applies
a fix, auto-posts to Jira, or executes code/tests. See the Disclaimer in
every rendered report.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

Requires `ripgrep` (`rg`) on PATH for Stage 2's code search, and `git` for
Stage 4's diff tool and checkpoint fingerprinting.

## Quick start: fully offline demo

```bash
python -m triage_pipeline.main --demo
```

Runs with **zero network access and zero API key** against the bundled
`sample_data/demo_ticket.md` and `sample_data/demo_repo/` (a tiny billing
package with one real, findable off-by-one bug). Produces a Markdown report
identifying the bug, with a hedged root-cause *hypothesis*, per-area
confidence scores, and an auditable story-point breakdown.

Run it twice back-to-back and you'll see every stage reused from the
checkpoint cache:

```
[checkpoint] Reusing cached stage1_classify result.
[checkpoint] Reusing cached stage2_investigate result.
[checkpoint] Reusing cached stage3_writeup result.
```

## Real runs

### Against live Jira + a real repo, via the GitHub Copilot CLI backend

```bash
export JIRA_BASE_URL=https://yourcompany.atlassian.net
export JIRA_EMAIL=you@yourcompany.com
export JIRA_API_TOKEN=...   # https://id.atlassian.com/manage-profile/security/api-tokens

python -m triage_pipeline.main --ticket-key PROJ-123 \
    --repo /path/to/your/checkout --backend copilot \
    --out report.md
```

This backend shells out to an already-authenticated local `copilot` CLI
(`copilot -p ... --allow-all-tools`) in non-interactive mode, so a user with
a GitHub Copilot subscription can run the whole pipeline without metered
per-token API billing. Trade-off: each `structured_call` spawns a fresh
`copilot` process, so there is no cross-call prompt caching the way a
direct, persistent API client could get.

### Stage 4 -- verify a fix against an earlier diagnosis

```bash
python -m triage_pipeline.main --verify-fix --ticket-id PROJ-123 \
    --repo /path/to/your/checkout --base main --backend copilot
```

Requires that `--ticket-key PROJ-123` (or equivalent) was run first and
classified the ticket as `bug` -- its checkpoint is read to recover the
original diagnosis. The diff against `--base` is computed deterministically
by `git diff`; the model only judges its meaning, never reconstructs it.

## CLI flag reference

```
# Diagnostic pipeline
python -m triage_pipeline.main --demo
python -m triage_pipeline.main --source {jira,rovo,manual} [--file PATH] [--ticket-key KEY]
    [--no-comments] [--no-linked-tickets] [--max-linked-tickets N]
    --repo PATH --backend {copilot,stub}
    [--out PATH] [--json]
    [--fresh] [--redo-from N] [--no-checkpoint] [--checkpoint PATH]
    [--max-tool-turns N] [--confidence-threshold F]

# Stage 4, independent
python -m triage_pipeline.main --verify-fix --ticket-id KEY --repo PATH --base REF
    [--paths PATH...] [--backend {copilot,stub}] [--out PATH] [--json]
```

- `--ticket-key` alone implies `--source jira`.
- `--backend stub` is the same deterministic, offline backend used by
  `--demo` and by the test suite -- useful for scripting/testing without any
  LLM backend at all.
- Jira auth/network issues, git ref issues, and missing checkpoints produce
  a clear, actionable error message on stderr (exit code 1), never a raw
  stack trace.

## Architecture

See the original build spec for the full pipeline diagram (§1). In short:

1. **Stage 1 (Understand & Classify)** -- cheap model, no tools, one call
   producing `TicketFacts` + `Classification`.
2. **Deterministic branch** (`orchestrator.py`) -- plain Python `if
   classification.verdict == "bug"`, never an LLM decision.
3. **Stage 2 (Investigate)** -- stronger model, ONE continuous tool-use
   session (localize -> gate on confidence -> fix -> blast radius -> story
   point factors).
4. **Deterministic story-point scoring** (`rubrics.py`) -- a pure, unit
   -tested function; the model never computes the final number.
5. **Stage 3 (Write-up)** -- cheap model, no tools, narrates the structured
   findings into Jira-comment prose.
6. **Stage 4 (Verify Fix)** -- independent, on-demand, tool-free; judges a
   deterministically-computed `git diff`.

### Where each non-negotiable design principle (§0) is satisfied

| # | Principle | Where |
|---|---|---|
| 1 | Deterministic control flow, no LLM router | `orchestrator.py` (`run_pipeline`) -- plain `if classification.verdict == "bug"` |
| 2 | Typed structured data between stages | `schemas.py` dataclasses; every stage function takes/returns them, never prose |
| 3 | No silently-discarded fields | `render.py` renders every `CodeHit.confidence`/`.reason`, `StoryPointFactors`, hedged hypothesis + confidence |
| 4 | Fingerprinted caching | `llm/caching.py` (`CachingLLMClient`) -- ticket content hash + repo HEAD per stage |
| 5 | Advisory-only artifacts | Every stage's system prompt + `render.py`'s disclaimer/suggestion framing/checklist captions |
| 6 | Ticket/code text treated as untrusted data | Explicit "SECURITY NOTE" paragraph in every stage's system prompt (`stages/*.py`) |
| 7 | No tool-budget hard-crash | `llm/base.py` (`ToolBudgetExhausted`), caught in `stages/stage2_investigate.py` -> degraded result, never an unhandled exception |

## Assumptions

Where this spec was silent, the following reasonable, production-quality
choices were made:

- **Second backend**: implemented only a GitHub Copilot CLI backend
  (`llm/copilot_cli.py`, shelling out to `copilot -p ... --allow-all-tools
  --output-format json`), per explicit user instruction. No
  `AnthropicLLMClient`/metered-API backend was implemented in this build.
- **Structured output on the Copilot CLI backend**: the CLI has no API to
  force a custom tool call with an arbitrary JSON schema, so the backend
  instructs the model to respond with exactly one fenced ` ```json ` block
  matching the schema, with one corrective retry if the first response
  doesn't parse. This is a documented, backend-specific approximation of
  the "forced `emit_result` tool call" ideal described in §7.
- **Tool-turn budget on the Copilot CLI backend**: the CLI doesn't expose a
  raw "max tool turns" flag, so the backend streams
  `--output-format json` events and counts `assistant.turn_start` events
  itself, terminating the subprocess and raising `ToolBudgetExhausted` once
  `max_tool_turns` is exceeded -- enforcing the *same* budget contract as
  any other backend would, rather than an unconfigurable limit.
- **Stage 2's tools on the Copilot CLI backend**: rather than wiring custom
  MCP tool definitions for `search_code`/`read_excerpt`, the backend relies
  on the Copilot CLI's own built-in read-only file/search tools (scoped via
  `-C <repo>`, with `--deny-tool write --deny-tool shell` to keep the
  session read-only). The `CodeSearchTool`/`read_excerpt` Python interface
  is still used directly by `StubLLMClient` fixtures and is documented as
  the swap point for a future language-aware search tool.
- **Model IDs for the Copilot CLI backend**: `claude-haiku-4.5` for the
  cheap/fast tier (Stages 1 and 3), `claude-sonnet-5` for the strong tier
  (Stages 2 and 4) -- verify current model availability with `copilot
  --model <id> ...` before relying on these.
- **Checkpoint file location**: defaults to
  `.triage_checkpoints/<ticket_id>.json` in the current working directory;
  overridable with `--checkpoint PATH`.
- **`needs_human_review`**: set `True` whenever the verdict is
  `insufficient_info`, Stage 1's classification confidence is below 0.5,
  Stage 2 degraded due to a tool-budget exhaustion, or Stage 2's
  localization confidence stayed below the configured gate threshold.
- **Rovo export format**: a `# KEY: Title` heading, `**Field:** value`
  metadata lines, and `## Description` / `## Comments` sections (see
  `sample_data/demo_ticket.md` for a worked example). Documented in
  `adapters/rovo.py`'s module docstring since the spec described this only
  loosely.
- **Secret redaction patterns**: a small, explicit set of regexes (AWS
  access key IDs, `api_key=...`-style assignments, bearer tokens, PEM
  private-key headers, GitHub tokens) in `tools/code_search.py`. Documented
  as a starting set, not exhaustive -- extend the list for your
  environment's credential formats.

## Testing

```bash
python -m pytest -q
```

Runs fully offline, no credentials or network access required (see
`tests/`). Covers: the deterministic story-point rubric (every boundary),
the deterministic duplicate-detection prefilter, ADF->Markdown conversion,
Jira adapter linked-ticket collection/dedup/caps/failure-skipping,
orchestrator short-circuit and happy-path branching, Stage 2's graceful
degradation on tool-budget exhaustion, checkpoint fingerprint invalidation
(ticket edits and repo HEAD advances), and an end-to-end `--demo`-equivalent
run that exercises Stage 2's tool loop against real ripgrep.

## Out of scope (§14)

No auto-posting back to Jira, no actual test execution/sandboxed code
running, no multi-repository investigation, no UI beyond the CLI + rendered
Markdown, and no fifth LLM-driven review/critic stage. Where a check could
plausibly be "yet another agent," it was implemented as a deterministic
Python check instead (duplicate prefiltering, story-point scoring,
checkpoint fingerprinting, `suggested_tests_found` matching).
