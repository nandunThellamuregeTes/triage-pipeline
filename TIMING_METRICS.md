# Pipeline Timing Metrics (Option A Implementation)

## Overview

The triage pipeline now includes automatic timing measurements at two levels:

1. **Per-Stage Timing** — Individual stage execution time (Stage 1, 2, 1.5, 3)
2. **Per-Iteration Timing** — Challenge loop iteration breakdown (Stage 1.5 only)
3. **Total Pipeline Timing** — End-to-end pipeline execution time

## Timing Output

### Per-Stage Timing

As the pipeline runs, each stage prints its completion time:

```
[stage1] Done in 2.3s -- verdict=bug, confidence=85%
[stage2] Done in 15.7s -- overall_confidence=62%, 3 candidate area(s), 2 fix proposal(s)
[stage1.5] Challenge loop iteration 1/3...
[stage1.5] Agent 2 verdict: can_disprove=True, confidence=78%
[stage1.5] Convergence: improved, new_confidence=68%
[stage1.5] Challenge loop iteration 2/3...
...
```

### Challenge Loop Iteration Breakdown

When the challenge loop runs (Stage 1.5), per-iteration timing is printed:

```
[stage1.5] Challenge loop timing breakdown:
  Loop 1: 8.2s (improved)
  Loop 2: 6.1s (converged)
[stage1.5] Total challenge loop time: 14.3s
```

Each line shows:
- **Loop number** — Iteration index (1, 2, 3, ...)
- **Elapsed time** — Seconds spent in this loop
- **Status** — One of:
  - `improved` — Agent 1 regenerated with new hypothesis
  - `converged` — Hypothesis accepted by Agent 2
  - `no_change` — Hypothesis unchanged after regeneration

### Total Pipeline Timing Summary

At the end of the entire diagnostic run, a summary is printed:

```
============================================================
[PIPELINE SUMMARY] Total end-to-end time: 28.4s
============================================================
```

This is the total wall-clock time from pipeline start to finish, including all stages and overhead.

## Technical Implementation

### Files Modified

#### 1. `triage_pipeline/stages/stage1_5_challenge.py`
- Added `import time` at top
- Added `loop_start_time` and `loop_timings` tracking at function start
- Added `iter_start_time` for each loop iteration
- After each iteration, capture elapsed time and status in `loop_timings` list
- Before function return, print per-iteration breakdown with total loop time

**Key lines:**
```python
loop_start_time = time.monotonic()
loop_timings: list[dict] = []

for loop_num in range(1, max_loops + 1):
    iter_start_time = time.monotonic()
    # ... loop body ...
    iter_elapsed = time.monotonic() - iter_start_time
    loop_timings.append({
        "loop_num": loop_num,
        "elapsed_seconds": iter_elapsed,
        "status": convergence_status,
    })

# At function end:
total_loop_time = time.monotonic() - loop_start_time
if loop_timings:
    print("\n[stage1.5] Challenge loop timing breakdown:")
    for timing in loop_timings:
        print(f"  Loop {timing['loop_num']}: {timing['elapsed_seconds']:.1f}s ...")
    print(f"[stage1.5] Total challenge loop time: {total_loop_time:.1f}s")
```

#### 2. `triage_pipeline/main.py`
- Added `pipeline_start_time = time.monotonic()` at start of `_run_diagnostic()`
- Calculate `pipeline_total_time` after `run_pipeline()` completes
- Print summary box with total end-to-end time

**Key lines:**
```python
def _run_diagnostic(args) -> int:
    pipeline_start_time = time.monotonic()
    # ... main logic ...
    pipeline_total_time = time.monotonic() - pipeline_start_time
    # ... print report ...
    print(f"\n{'='*60}")
    print(f"[PIPELINE SUMMARY] Total end-to-end time: {pipeline_total_time:.1f}s")
    print(f"{'='*60}")
```

## Example Timeline

Here's what a full pipeline run might look like with all timing information:

```
[stage1] Classifying ticket PROJ-123...
[stage1] Done in 2.3s -- verdict=bug, confidence=78%

[stage2] Investigating (localize + fix + impact, this can take a while)...
[stage2] Done in 14.7s -- overall_confidence=62%, 4 candidate area(s), 2 fix proposal(s)

[stage1.5] Challenge loop triggered (confidence 62% < 75%)
[stage1.5] Challenge loop iteration 1/3...
[stage1.5] Agent 2 verdict: can_disprove=True, confidence=81%
[stage1.5] Flaws found. Asking Agent 1 to regenerate...
[stage1.5] Convergence: improved, new_confidence=68%

[stage1.5] Challenge loop iteration 2/3...
[stage1.5] Agent 2 verdict: can_disprove=False, confidence=45%
[stage1.5] Hypothesis withstands challenge
[stage1.5] Confidence boosted to 72% (+0.04)
[stage1.5] Hypothesis accepted after 2 loop(s).

[stage1.5] Challenge loop timing breakdown:
  Loop 1: 7.8s (improved)
  Loop 2: 6.2s (converged)
[stage1.5] Total challenge loop time: 14.0s

[stage3] Writing up report (jira comment, next steps)...
[stage3] Done in 1.2s

============================================================
[PIPELINE SUMMARY] Total end-to-end time: 32.2s
============================================================
```

## Use Cases

### Performance Analysis
Use the timing output to understand which stage is the bottleneck:
- **Stage 2 heavy?** → Investigation with code search is slow; consider optimizing search
- **Stage 1.5 iterating many times?** → Challenge loop regenerations are expensive; consider relaxing convergence threshold
- **Stage 3 slow?** → Write-up LLM call is heavy

### Challenge Loop Effectiveness
Monitor loop iteration timing to understand cost/benefit:
- **Loop 1 very long, Loop 2 quick?** → First agent regeneration was expensive but effective
- **Many loops?** → Convergence is hard; may want to escalate sooner

### Batch Processing
When running pipelines on multiple tickets, total timing helps estimate throughput and resource planning.

## Future Enhancements (Not in Option A)

This implementation provides a solid foundation for future enhancements:

**Option B (Moderate effort):**
- Structured JSON output for per-stage and per-iteration timing
- Export timing data to `--timing-json output.json` for dashboards
- Per-component breakdown (LLM time vs. tool time)

**Option C (High effort):**
- Cache hit/miss tracking per stage
- Sub-component profiling (search time, LLM latency, token counting)
- Per-agent timing (Agent 1 investigation vs. Agent 2 challenge)

## Notes

- Timing uses `time.monotonic()` (monotonic clock, unaffected by system clock adjustments)
- All times are wall-clock time (not CPU time); includes I/O, LLM latency, tool execution
- Printed with 1 decimal place precision (e.g., `2.3s`, `14.7s`)
- Per-iteration status helps identify which loop type took longest
