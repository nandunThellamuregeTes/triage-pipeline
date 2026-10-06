# Timing Output Examples

## Example 1: Simple Bug (No Challenge Loop)

When running the pipeline on a straightforward bug without needing the challenge loop:

```
$ python3 -m triage_pipeline.main --repo ~/myrepo --ticket-key TRIAG-1234

[stage1] Classifying ticket TRIAG-1234...
[stage1] Done in 2.1s -- verdict=bug, confidence=92%

[stage2] Investigating (localize + fix + impact, this can take a while)...
[stage2] Done in 16.3s -- overall_confidence=85%, 2 candidate area(s), 1 fix proposal(s)

[stage3] Writing up report (jira comment, next steps)...
[stage3] Done in 0.8s

============================================================
[PIPELINE SUMMARY] Total end-to-end time: 19.2s
============================================================

Report written to stdout...
```

**Interpretation:**
- Stage 1 (classify) took 2.1s
- Stage 2 (investigate) took 16.3s — most time spent here
- Stage 3 (writeup) took 0.8s
- Challenge loop did NOT run (confidence 85% > threshold 75%)
- **Total pipeline: 19.2s**

---

## Example 2: Bug with Challenge Loop (Single Iteration)

When the hypothesis confidence is below the threshold, the challenge loop runs:

```
$ python3 -m triage-pipeline.main --repo ~/myrepo --ticket-key TRIAG-5210 --challenge

[stage1] Classifying ticket TRIAG-5210...
[stage1] Done in 2.3s -- verdict=bug, confidence=78%

[stage2] Investigating (localize + fix + impact, this can take a while)...
[stage2] Done in 14.7s -- overall_confidence=62%, 4 candidate area(s), 2 fix proposal(s)

[stage1.5] Challenge loop triggered (confidence 62% < 75%)
[stage1.5] Challenge loop iteration 1/3...
[stage1.5] Agent 2 verdict: can_disprove=False, confidence=45%
[stage1.5] Hypothesis withstands challenge (confidence_in_disproof=45%)
[stage1.5] Confidence boosted to 65% (+0.03)
[stage1.5] Hypothesis accepted after 1 loop(s).

[stage1.5] Challenge loop timing breakdown:
  Loop 1: 6.2s (converged)
[stage1.5] Total challenge loop time: 6.2s

[stage3] Writing up report (jira comment, next steps)...
[stage3] Done in 1.1s

============================================================
[PIPELINE SUMMARY] Total end-to-end time: 24.3s
============================================================

Report written to stdout...
```

**Interpretation:**
- Stage 1: 2.3s
- Stage 2: 14.7s (initial investigation)
- Challenge loop entered because confidence 62% < 75%
- Loop 1: 6.2s, Agent 2 could NOT disprove hypothesis (converged)
- Confidence boosted from 62% → 65% (+3%)
- Challenge loop exited after 1 iteration (hypothesis accepted)
- Stage 3: 1.1s
- **Total pipeline: 24.3s** (added 5.1s for challenge loop)

---

## Example 3: Bug with Challenge Loop (Multiple Iterations)

When Agent 2 finds flaws, Agent 1 regenerates, creating multiple loop iterations:

```
$ python3 -m triage-pipeline.main --repo ~/myrepo --ticket-key TRIAG-8901 --challenge

[stage1] Classifying ticket TRIAG-8901...
[stage1] Done in 2.5s -- verdict=bug, confidence=75%

[stage2] Investigating (localize + fix + impact, this can take a while)...
[stage2] Done in 18.2s -- overall_confidence=58%, 5 candidate area(s), 3 fix proposal(s)

[stage1.5] Challenge loop triggered (confidence 58% < 75%)
[stage1.5] Challenge loop iteration 1/3...
[stage1.5] Agent 2 verdict: can_disprove=True, confidence=82%
[stage1.5] Flaws found. Asking Agent 1 to regenerate...
[stage1.5] Convergence: improved, new_confidence=65%

[stage1.5] Challenge loop iteration 2/3...
[stage1.5] Agent 2 verdict: can_disprove=True, confidence=71%
[stage1.5] Flaws found. Asking Agent 1 to regenerate...
[stage1.5] Convergence: improved, new_confidence=72%

[stage1.5] Challenge loop iteration 3/3...
[stage1.5] Agent 2 verdict: can_disprove=False, confidence=38%
[stage1.5] Hypothesis withstands challenge (confidence_in_disproof=38%)
[stage1.5] Confidence boosted to 74% (+0.02)
[stage1.5] Hypothesis accepted after 3 loop(s).

[stage1.5] Challenge loop timing breakdown:
  Loop 1: 9.8s (improved)
  Loop 2: 8.7s (improved)
  Loop 3: 7.1s (converged)
[stage1.5] Total challenge loop time: 25.6s

[stage3] Writing up report (jira comment, next steps)...
[stage3] Done in 1.3s

============================================================
[PIPELINE SUMMARY] Total end-to-end time: 47.6s
============================================================

Report written to stdout...
```

**Interpretation:**
- Stage 1: 2.5s
- Stage 2: 18.2s (initial investigation, confidence 58%)
- Challenge loop entered (58% < 75%)
- **Loop 1: 9.8s**
  - Agent 2 found flaws (can_disprove=True, 82% confidence)
  - Agent 1 regenerated hypothesis
  - Status: "improved" (new hypothesis is different)
  - New confidence: 65%
- **Loop 2: 8.7s**
  - Agent 2 again found flaws (can_disprove=True, 71% confidence)
  - Agent 1 regenerated again
  - Status: "improved" (new hypothesis is different)
  - New confidence: 72%
- **Loop 3: 7.1s**
  - Agent 2 could NOT disprove (can_disprove=False, 38% confidence)
  - Status: "converged" (hypothesis accepted)
  - Confidence boosted: 74%
  - Loop exited
- **Challenge loop total: 25.6s** (expensive due to multiple regenerations)
- Stage 3: 1.3s
- **Total pipeline: 47.6s** (significantly longer due to challenge loop)

---

## Example 4: Not a Bug (Challenge Loop Not Triggered)

When the ticket is classified as something other than "bug":

```
$ python3 -m triage-pipeline.main --repo ~/myrepo --ticket-key TRIAG-9999 --challenge

[stage1] Classifying ticket TRIAG-9999...
[stage1] Done in 1.9s -- verdict=insufficient_info, confidence=68%

[stage2] Skipped (verdict is 'insufficient_info', not 'bug').

[stage3] Writing up report (jira comment, next steps)...
[stage3] Done in 0.7s

============================================================
[PIPELINE SUMMARY] Total end-to-end time: 3.3s
============================================================

Report written to stdout...
```

**Interpretation:**
- Stage 1: 1.9s
- Verdict is NOT "bug", so Stage 2 and challenge loop are **skipped**
- Stage 3: 0.7s
- **Total pipeline: 3.3s** (very fast because stages 2-3 skipped)
- Challenge loop NOT triggered (only triggers for bugs with low confidence)

---

## Timing Analysis Tips

### Identifying Bottlenecks

| Stage | Typical Time | If Much Longer |
|-------|---|---|
| Stage 1 (Classify) | 1-3s | LLM latency issue? |
| Stage 2 (Investigate) | 10-25s | Code search slow? Tool budget exhausted? |
| Challenge Loop | 5-30s | Multiple regenerations? Check loop count |
| Stage 3 (Writeup) | 0.5-2s | LLM latency for cheap model? |

### Challenge Loop Performance

- **Loop 1: improved, 8s** → First regeneration effective
- **Loop 2: improved, 7s** → Hypothesis is being refined
- **Loop 3: converged, 4s** → Good convergence, exited on 3rd loop
- → **Total 19s** → Reasonable cost for hypothesis validation

vs.

- **Loop 1: improved, 9s** → First regeneration worked
- **Loop 2: improved, 8s** → Hypothesis still being refined
- **Loop 3: no_change, 7s** → No progress, escalated
- → **Total 24s** → Higher cost, escalation needed

---

## Running with Custom Challenge Settings

Control challenge loop behavior via CLI flags:

### Default (challenge if confidence < 75%, max 3 loops)
```bash
python3 -m triage_pipeline.main --repo ~/myrepo --ticket-key TRIAG-123 --challenge
```

### Stricter (challenge if confidence < 90%, max 5 loops)
```bash
python3 -m triage_pipeline.main --repo ~/myrepo --ticket-key TRIAG-123 \
  --challenge \
  --challenge-confidence-threshold 0.90 \
  --challenge-max-loops 5
```

### More Lenient (challenge if confidence < 50%, max 2 loops)
```bash
python3 -m triage_pipeline.main --repo ~/myrepo --ticket-key TRIAG-123 \
  --challenge \
  --challenge-confidence-threshold 0.50 \
  --challenge-max-loops 2
```

---

## Gathering Timing Data Over Multiple Runs

For batch processing or timing analysis across many tickets:

```bash
#!/bin/bash

# Collect timing for all tickets
for ticket in TRIAG-100 TRIAG-101 TRIAG-102; do
  echo "Processing $ticket..."
  python3 -m triage_pipeline.main --repo ~/myrepo --ticket-key $ticket --challenge 2>&1 | \
    grep -E "Done in|Total end-to-end time"
done
```

This extracts just the timing lines for analysis in spreadsheets or dashboards.
