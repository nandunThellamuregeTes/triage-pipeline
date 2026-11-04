# Challenge Agent Quick-Start Guide

## Overview

The challenge agent (Stage 1.5) runs **before** Stage 2 code investigation,
for every "bug"-verdict ticket, when `--challenge` is enabled. It does not
critique a single hypothesis handed to it -- it generates its own ranked
hypotheses (blind, from two independent agents), then iteratively
stress-tests them through a mechanism-check challenge/regen loop. Stage 2
then uses the final ranked hypotheses purely as **leads, not conclusions**
while doing its own (multi-sample) code localization and fix proposal.

As of this redesign, Stage 1.5 is preceded by a new **Stage 0** (attachment
analysis: video/image inspection) and the loop converges **mechanism and
trigger separately** rather than with a single pass/fail verdict.

## Pipeline Order

```
Stage 0: Attachment analysis (video/image inspection, before Stage 1)
         ↓
Stage 1: Classify (verdict, code-computed evidence_quality, evidence_gaps)
         ↓ verdict == "bug" AND --challenge?
Stage 1.5a: Agent 2 blind pass (independent, no sight of Agent 1)
            +  Agent 1 initial pass (mechanism + trigger hypotheses)
         ↓
Stage 1.5c/d loop (ALWAYS >=1 iteration when enabled; up to --challenge-max-loops):
    Agent 2 (Challenge): per-step mechanism check of Agent 1's top hypothesis,
                          compares against its own blind-pass hypotheses,
                          returns mechanism_status AND trigger_status separately
         ↓
    Agent 1 (Regen):     responds to every point (CONCEDED/REBUTTED/OPEN),
                          re-ranks hypotheses (never drops an alternative
                          without a specific verified fact), declares
                          convergence_status
         ↓
    Exit when (a) all points CONCEDED/REBUTTED (no OPEN) with a terminal
    convergence_status, OR (b) two consecutive iterations with no new
    verified facts and no ranking change. Max 3 iterations. Running out of
    objections alone is NOT a valid exit reason.
         ↓
Stage 2: Investigate -- localize (N independent samples, never merged),
         gate (controls ONLY whether fix proposals/story points/blast
         radius are produced -- NEVER whether the challenge loop ran),
         fix proposals. Given the challenge loop's final ranked hypotheses
         + convergence_status as LEADS, not conclusions.
         ↓
Pipeline code computes `final_confidence` (confidence.py cap table) from
Stage 1/1.5/2 outputs -- this is the ONLY place confidence is finalized.
         ↓
Stage 3: Write-up -- narrates `final_confidence` verbatim; never computes or
         restates its own number. Honesty rules: challenge-loop agreement is
         NOT validation.
```

## Key Features

✅ **Always Runs (opt-in, unconditional, NOT confidence-gated)**: Triggers
whenever `--challenge` is passed and verdict is `"bug"`. Once triggered, the
loop always runs **at least one full iteration** -- it is never skipped or
short-circuited because Stage 2 hasn't run yet (there is no Stage 2
confidence at this point to gate on) or because some other confidence value
looks low. `LOOP_NOT_RUN` must never be the loop's *final*
`convergence_status`; the orchestrator raises `RuntimeError` as a safety net
if it ever is.

✅ **Independent Blind Passes (1.5a)**: Agent 2 forms 3+ ranked hypotheses
with **no sight of Agent 1's work at all** (true blind pass, not just "before
the loop"). Agent 1's initial pass (also 1.5a/b) forms its own hypotheses
from pre-fetched code hits, independent of Agent 2's blind pass.

✅ **Mechanism vs. Trigger, Converged Separately**: Every `Hypothesis` has a
`mechanism` (what happens, step by step) and a `trigger` (why here/now).
`ChallengeResult` carries `mechanism_status` and `trigger_status`
independently (each one of `REFUTED` / `WEAKENED` / `UNTESTED` /
`SUPPORTED`). A hypothesis can have its mechanism verified in code while its
trigger stays unresolved -- that's `MECHANISM_CONVERGED_TRIGGER_OPEN`, not a
failure state.

✅ **Four Terminal Convergence Statuses** (`ConvergenceStatus`):
| Status | Meaning |
|---|---|
| `CONVERGED` | mechanism AND trigger each have a verified discriminating fact separating the top hypothesis from the next |
| `MECHANISM_CONVERGED_TRIGGER_OPEN` | mechanism verified in code; trigger unresolved |
| `NEEDS_EVIDENCE` | neither converges, but the agents agreed on a ranked list + shared discriminating experiments/missing evidence -- **a valid, honest success state, not a failure** |
| `LOOP_NOT_RUN` | only a non-terminal placeholder; must **never** appear as the loop's final status in production -- the orchestrator raises `RuntimeError` if it does |

✅ **No Confidence-Boost Heuristic**: There is no fixed "+0.05 if Agent 2
can't disprove it" rule. Absence of disproof is NOT support. Confidence only
rises when a verified, discriminating fact is added -- self-reported by
Agent 1 against explicit rules in its prompt (e.g. no repro ⇒ raw confidence
capped at 0.5). This self-reported number then still passes through the
pipeline's code-computed cap table (see "Confidence" below) before becoming
`final_confidence`.

✅ **Full Audit Trail**: Blind passes and the full loop history are stored on
`TriageReport.agent1_initial_pass`, `.agent2_blind_pass`, and
`.challenge_history`, along with `.agreed_experiments`,
`.agreed_missing_evidence`, and `.symbols_not_found` (due-diligence record
from the CODE SEARCH RULE -- see below).

---

## Usage

### Basic Usage (Enable Challenge)

```bash
python3 -m triage_pipeline.main \
  --ticket-key PROJ-123 \
  --repo /path/to/repo \
  --challenge
```

### Without Challenge (Fast Path)

```bash
python3 -m triage_pipeline.main \
  --ticket-key PROJ-123 \
  --repo /path/to/repo
```

### Custom Configuration (Challenge + Attachments + Localization Samples)

```bash
python3 -m triage_pipeline.main \
  --ticket-key PROJ-123 \
  --repo /path/to/repo \
  --challenge \
  --challenge-max-loops 2 \
  --enable-attachments \
  --localization-samples 3
```

---

## CLI Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--challenge` | flag | `false` | Enable the Stage 1.5 challenge loop (blind passes + iterative mechanism/trigger check loop), run before Stage 2 |
| `--challenge-max-loops` | int | `3` | Maximum challenge/regen loop iterations before stopping with whatever `convergence_status` the last iteration produced |
| `--enable-attachments` | flag | `false` | Enable Stage 0 attachment analysis (video frame extraction + vision model inspection) |
| `--max-attachment-size` | int | `50_000_000` (50MB) | Skip attachments over this size; recorded as `AttachmentInspection(inspected=False, ...)`, never silently dropped |
| `--video-fps` | int | `4` | Fixed sample rate for the first N seconds of video frame extraction |
| `--video-seconds` | int | `15` | How many seconds of fixed-rate sampling to extract (scene-change frames are additionally sampled across the whole video) |
| `--max-video-frames` | int | `60` | Hard cap on frames sent to the vision model per video |
| `--localization-samples` | int | `3` | Number of independent Stage 2 localization samples (never merged/averaged -- see "Multi-Sample Localization" below) |

---

## Confidence: Code-Computed, Not LLM-Restated

`triage_pipeline/confidence.py`'s `compute_confidence()` is the **only**
place `final_confidence` is produced. The orchestrator builds
`ConfidenceInputs` from Stage 1 (`evidence_quality`), Stage 1.5
(`convergence_status`, hypothesis `explains_why_now`), and Stage 2
(`localization_agreement`, `symbols_not_found`) and calls it once, after
Stage 2 completes. Every stage's own self-reported number (classification
confidence, challenge-loop `overall_confidence`, Stage 2
`overall_confidence`) is only a raw INPUT -- never the final value. Stage 3
receives `final_confidence` read-only and must only narrate it in prose,
never recompute or restate a different number.

Cap table (applied in code, not by the LLM):

| Condition | Cap |
|---|---|
| not `executed_or_reproduced` | ≤ 0.5 |
| `evidence_quality == "weak"` | ≤ 0.4 |
| `convergence_status` in (`NEEDS_EVIDENCE`, `MECHANISM_CONVERGED_TRIGGER_OPEN`) | ≤ 0.4 |
| not `explains_why_now` | −0.1 |
| `localization_agreement < 0.5` | ≤ 0.35 |
| `key_symbol_not_located` | −0.1 |

`executed_or_reproduced` is derived from
`TicketFacts.is_reproducible_by_reporter` -- nothing is ever actually
executed by this pipeline (no test suite is run), so the only legitimate
signal of a real reproduction is the reporter's/support's own stated
replication (documented in `orchestrator.py`).

---

## Multi-Sample Localization (Stage 2)

Stage 2's localization step runs `config.localization_samples` times
(default 3), each as its own independent `llm_client.structured_call(stage=
f"stage2_localize_sample{i}", ...)` call -- the stage-name suffix exists
specifically so `CachingLLMClient` does not collapse the repeated calls into
one cached response. The samples are **never merged or averaged** into one
answer. Instead:

- `localization_agreement` (0-1) is a Jaccard-like file-overlap score across
  the samples' top candidate files, computed in Python.
- `localization_samples: list[list[str]]` stores each sample's own top files
  separately on the report.
- If `localization_agreement < 0.5`, the confidence cap table above applies
  a hard ceiling -- low agreement is treated as evidence of genuine
  uncertainty, not something to paper over by picking one sample.

---

## CODE SEARCH RULE (applies to every Stage 1.5/Stage 2 prompt)

Every prompt that gives the model `search_code`/`read_excerpt`/`list_dir`/
`grep_files` also carries this rule verbatim: never report a symbol as
undefined or missing until you have (i) followed the class chain (`extends`,
`implements`, mixins, base classes, prototype assignments) and (ii) searched
built/bundled output. Any symbol that still can't be found after that is
recorded in `symbols_not_found` (with `searched_in`/`class_chain_followed`)
as a due-diligence record, not silently asserted as "doesn't exist".

---

## TriageReport Fields (Challenge Loop + Confidence + Attachments)

```python
report.was_challenged: bool                           # True if loop ran
report.agent1_initial_pass: BlindPassResult | None     # Agent 1's initial pass (1.5b)
report.agent2_blind_pass: BlindPassResult | None       # Agent 2's blind pass (1.5a), no sight of Agent 1
report.hypotheses: list[Hypothesis]                    # final ranked hypotheses (mechanism + trigger)
report.convergence_status: "CONVERGED" | "MECHANISM_CONVERGED_TRIGGER_OPEN" | "NEEDS_EVIDENCE" | None
report.loop_iterations_run: int                        # always >=1 if was_challenged
report.challenge_history: list[ChallengeLoopIteration]  # every loop iteration (ChallengeResult + ChallengeRegenResult)
report.agreed_experiments: list[str]
report.agreed_missing_evidence: list[str]

report.confidence: float                # code-computed `final_confidence` (§ confidence.py) -- the ONLY final number
report.confidence_caps_applied: list[str]
report.confidence_concerns: list[str]    # Stage 2's named reasons confidence should be lower (not a free-form reduction)
report.symbols_not_found: list[SymbolNotFound]          # merged from the challenge loop + Stage 2
report.localization_agreement: float | None
report.localization_samples: list[list[str]]

report.needs_human_review: bool

# Stage 0 attachment fields:
report.attachments_analyzed: list[AttachmentInspection]
report.observed_in_attachments: AttachmentAnalysis | None
report.observed_flash_interval_ms: float | None  # median of appear/disappear intervals, computed in Python
report.attachments_inspected: bool

# Stage 1 outputs:
report.evidence_quality: str | None   # code-computed via compute_evidence_quality(), LLM only explains it
report.evidence_gaps: list[EvidenceGap]
```

---

## Decision Gate Conditions

```python
if classification.verdict == "bug" and config.enable_challenge:
    # Run the full loop: 1.5a blind passes + 1.5c/d iterative challenge,
    # unconditionally, before Stage 2 even starts.
```

| `--challenge` | Verdict | Result |
|---|---|---|
| ✓ | bug | 🔄 **LOOP** (always, regardless of any confidence value -- there is no Stage 2 confidence yet to gate on) |
| ✓ | duplicate / insufficient_info / invalid | ⏭️ Skip (not a bug) |
| ✗ | bug | ⏭️ Skip (flag not set) |

Stage 2's own confidence gate (`--confidence-threshold`) is a **completely
separate** decision that only controls whether Stage 2 produces fix
proposals/story points/blast radius for a "bug" verdict -- it has no bearing
on whether the challenge loop ran, since the loop always ran earlier.

---

## Debugging Tips

### Check If the Challenge Loop Ran

Look at console output:
```
[stage0] Analyzing 1 attachment(s)...
[stage1] Classifying ticket PROJ-456...
[stage1.5] Running challenge loop (blind passes + mechanism-check iteration)...
[stage1.5a] Agent 2 forming independent blind-pass hypotheses...
[stage1.5b] Agent 1 forming independent initial hypotheses (seeded by code hits)...
[stage1.5c/d] Challenge loop iteration 1/3...
[stage1.5c] Agent 2 verdict: mechanism_status=WEAKENED, trigger_status=UNTESTED
[stage1.5d] Agent 1 convergence_status=NEEDS_EVIDENCE, confidence=35%
```

### Inspect Challenge History

```python
import json

report_dict = json.load(open("report.json"))
print(f"was_challenged: {report_dict['was_challenged']}")
print(f"convergence_status: {report_dict['convergence_status']}")
print(f"loop_iterations_run: {report_dict['loop_iterations_run']}")
print(json.dumps(report_dict["challenge_history"], indent=2))
```

---

## Troubleshooting

### Challenge Loop Not Running

**Symptom**: `[stage1.5]` messages not in logs.

**Checklist**:
- [ ] Did you pass `--challenge`?
- [ ] Is verdict == `"bug"` (not duplicate/invalid/insufficient_info)?

### `convergence_status` Stays `NEEDS_EVIDENCE`

**Reason**: This is an intentionally honest, valid end state when evidence is
genuinely insufficient to discriminate between hypotheses (no repro, no
logs, low localization agreement). It is **not** a bug in the loop --
`agreed_experiments`/`agreed_missing_evidence` should still be populated so a
human has a concrete next step. Compare against
`MECHANISM_CONVERGED_TRIGGER_OPEN`, which means strictly more was learned
(the mechanism, specifically).

### `RuntimeError: Challenge loop finished with LOOP_NOT_RUN...`

**Reason**: This is a hard safety net, not expected behavior -- it means
`stage1_5_challenge.run_challenge_loop()` returned without ever running a
real iteration while the loop was enabled for a "bug" verdict. Treat this as
a regression in the loop's own logic (see `stage1_5_challenge.py`), not
something to silently catch or suppress.

### `stage0_attachments` Reports `attachments_inspected=False`

**Reason**: either the attachment exceeded `--max-attachment-size`, had an
unsupported MIME type, or `ffmpeg` was not found on `PATH` (checked via
`shutil.which("ffmpeg")`). In every one of these cases, downstream stages
are given `attachments_inspected=False` and must never describe the
attachment as "missing from the ticket" -- only as "not inspected" (and why).

---

## Next Steps

1. **Test on Real Tickets**: Use `--challenge --enable-attachments` and
   inspect `challenge_history` / `attachments_analyzed`.
2. **Monitor Results**: Track convergence-status distribution (`CONVERGED`
   vs. `MECHANISM_CONVERGED_TRIGGER_OPEN` vs. `NEEDS_EVIDENCE`) and
   `needs_human_review` frequency.
3. **Collect Feedback**: Did the blind passes surface hypotheses Stage 2
   alone would have missed? Did low `localization_agreement` correlate with
   genuinely hard tickets?
4. **Tune `--challenge-max-loops` / `--localization-samples`** based on
   observed convergence patterns and agreement scores.

---

## Questions?

Refer to:
- Code comments in `stage1_5_challenge.py`, `stage0_attachments.py`, and
  `confidence.py` -- implementation details and judgement calls.
- `schemas.py` -- full field-level documentation of every dataclass
  (`Hypothesis`, `BlindPassResult`, `ChallengeResult`, `ChallengeRegenResult`,
  `ChallengeLoopIteration`, `Stage0Result`, `AttachmentAnalysis`).
- `tests/test_stage1_5_challenge.py`, `tests/test_confidence.py`,
  `tests/test_scrollbar_regression.py` -- worked examples of every
  convergence status, cap, and the full Stage 0-3 flow end to end.
