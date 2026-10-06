# Challenge Agent Quick-Start Guide

## Overview

The challenge agent (Stage 1.5) is now fully integrated into the triage pipeline. It provides adversarial validation of low-confidence bug hypotheses through an iterative loop.

## Key Features

✅ **Automatic Entry Gate**: Triggers only when:
- User passes `--challenge` flag
- Hypothesis confidence < 0.75 (configurable)
- Verdict is "bug"

✅ **Iterative Refinement**: Agent 1 and Agent 2 loop up to 3 times (configurable)

✅ **Confidence Improvement**: Boosts confidence by +0.05–0.10 when hypothesis withstands challenge

✅ **Human Escalation**: Sets `needs_human_review=True` if loop doesn't converge

✅ **Full Audit Trail**: Complete loop history stored in `TriageReport.challenge_history`

---

## Usage

### Basic Usage (Enable Challenge)

```bash
python -m triage_pipeline.main \
  --ticket-key TRIAG-5210 \
  --repo /path/to/repo \
  --challenge
```

### Without Challenge (Fast Path)

```bash
python -m triage_pipeline.main \
  --ticket-key TRIAG-5210 \
  --repo /path/to/repo
```

### Custom Configuration

```bash
python -m triage_pipeline.main \
  --ticket-key TRIAG-5210 \
  --repo /path/to/repo \
  --challenge \
  --challenge-confidence-threshold 0.8 \
  --challenge-max-loops 2
```

---

## CLI Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--challenge` | flag | `false` | Enable iterative challenge loop |
| `--challenge-confidence-threshold` | float | `0.75` | Enter loop if confidence < this |
| `--challenge-max-loops` | int | `3` | Maximum loop iterations |

---

## How It Works

### Loop Flow

```
Agent 1 generates hypothesis (e.g., confidence=0.62)
         ↓
Decision Gate: --challenge? confidence < 0.75? verdict == "bug"?
         ↓ YES
    Loop Iteration N
         ↓
    Agent 2 (Challenge): Try to DISPROVE hypothesis
         ├─ Found credible flaws? (confidence > 0.7)
         │  └─ YES → Agent 1 regenerates with feedback → Loop N+1
         │  └─ NO  → Hypothesis accepted, boost confidence → EXIT
         └─ Codebase findings, alternative hypotheses
         
    Termination:
    ├─ Hypothesis converged (Agent 2 can't disprove) → Accept
    ├─ No progress after regen → Escalate to human
    ├─ Max loops reached → Escalate to human
    └─ Tool budget exhausted → Exit with current hypothesis
```

### Output in Jira Comment

When challenge loop runs, the final `jira_comment_md` includes:
- Final hypothesis (possibly refined)
- Updated confidence (improved by validation)
- Brief note about iterative challenge process
- Fix proposals (same as without challenge)
- Suggested next steps

Example:
```
## Verdict
This appears to be a **race condition** involving fieldless auto-saves.
Confidence: 0.70 (improved from 0.62 after iterative challenge validation).

[AI-generated; this analysis was validated through an adversarial
challenge loop where a second agent attempted to disprove the hypothesis
and found no credible flaws, thus increasing confidence in the diagnosis.]

## Root Cause Hypothesis
Two independent editing surfaces (grid + composer) open in separate tabs
for the same lesson row. Each caches field values locally with no shared
version control. The server's per-field conditional updates lack optimistic
concurrency checks, so whichever tab's autosave fires last wins.

## Proposed Fix
...
```

---

## TriageReport Changes

The final `TriageReport` object now includes:

```python
report.was_challenged: bool                    # True if loop ran
report.challenge_history: list[...]            # Full transcript:
  [
    {
      "loop_num": 1,
      "agent2_verdict": {
        "can_disprove": True,
        "confidence_in_disproof": 0.75,
        "flaws_found": ["Hypothesis too broad"],
        "alternative_hypotheses": ["..."],
        "codebase_findings": ["file.py:123"],
      },
      "convergence_status": "improved",
      "confidence_before": 0.62,
      "confidence_after": 0.65,
    },
    {
      "loop_num": 2,
      "agent2_verdict": {
        "can_disprove": False,
        "confidence_in_disproof": 0.60,
        ...
      },
      "convergence_status": "converged",
      "confidence_before": 0.65,
      "confidence_after": 0.70,
    },
  ]

report.confidence: float                       # Final confidence (0.70)
report.needs_human_review: bool                # True if escalated
```

---

## Decision Gate Conditions

Challenge loop enters **if and only if:**

```python
if (
    args.challenge                              # User passed --challenge
    AND investigation.overall_confidence < challenge_confidence_threshold
    AND investigation.verdict == "bug"
    AND NOT investigation.degraded              # Not already degraded
):
    # Enter challenge loop
```

**Examples:**

| `--challenge` | Confidence | Verdict | Result |
|---|---|---|---|
| ✓ | 0.62 | bug | 🔄 **LOOP** |
| ✓ | 0.80 | bug | ⏭️ Skip (high confidence) |
| ✓ | 0.62 | duplicate | ⏭️ Skip (not a bug) |
| ✗ | 0.62 | bug | ⏭️ Skip (flag not set) |

---

## Confidence Boost Rules

When Agent 2 cannot disprove the hypothesis:

```python
if agent2.confidence_in_disproof > 0.8:
    boost = +0.10   # High confidence → strong boost
elif agent2.confidence_in_disproof < 0.5:
    boost = +0.02   # Low confidence → weak boost
else:
    boost = +0.05   # Default boost

final_confidence = min(confidence + boost, 0.95)  # Cap at 0.95
```

---

## Escalation Scenarios

Challenge loop escalates to human review (`needs_human_review=True`) when:

1. **No Convergence After Regen**: Agent 1's hypothesis didn't improve despite feedback
2. **Max Loops Reached**: Ran for `--challenge-max-loops` iterations without converging
3. **Tool Budget**: Tool execution exhausted (rare)

In all escalation cases:
- Original verdict is preserved
- Full challenge history is included
- Triage report is still generated (with note on escalation)
- Human engineer can review full reasoning transcript

---

## Debugging Tips

### Check If Challenge Loop Ran

Look at console output:
```
[stage1.5] Challenge loop triggered (confidence 0.62 < 0.75)
[stage1.5] Challenge loop iteration 1/3...
[stage1.5] Agent 2 verdict: can_disprove=True, confidence=0.75
```

### Inspect Challenge History

```python
import json
from triage_pipeline.schemas import TriageReport

# Load report JSON
report_dict = json.load(open("report.json"))
print(f"was_challenged: {report_dict['was_challenged']}")
print(f"challenge_history: {json.dumps(report_dict['challenge_history'], indent=2)}")
```

### Check Convergence Detection

The loop uses `difflib.SequenceMatcher` to compare hypotheses:
- **Similarity < 0.85** = improved (different hypothesis)
- **Similarity >= 0.85** = no change (too similar)

---

## Performance Notes

### Latency

- **Without challenge**: ~30-60s (normal pipeline)
- **With challenge (1 loop)**: +20-40s
- **With challenge (3 loops)**: +60-120s

Scales with:
- Code search complexity
- LLM response time
- Tool turn budget per iteration

### Cost

Challenge loop uses:
- 1 Agent 2 call per iteration (strong model, up to 10 tool turns)
- 1 Agent 1 regen call per flawed iteration (strong model, up to 8 tool turns)

Approximately **2-3x more API calls** if loop runs 3 times.

---

## Common Patterns

### Pattern 1: Low Confidence → Validated ✅

```
Initial: confidence=0.55 (too low, fix not proposed)
         ↓
Challenge Loop:
  Loop 1: Agent 2 finds gap, Agent 1 refines → confidence=0.58
  Loop 2: Agent 2 can't disprove → confidence=0.63
  
Final: confidence=0.63 (high enough for fix proposal)
       Fix proposal generated in Stage 2 review
```

### Pattern 2: Ambiguous → Escalated 🚀

```
Initial: confidence=0.65 (medium, proposed fix)
         ↓
Challenge Loop:
  Loop 1: Agent 2 finds flaw, Agent 1 regenerates
  Loop 2: Agent 2 finds different flaw, Agent 1 regenerates
  Loop 3: No improvement, N >= max_loops
  
Final: needs_human_review=True
       Human engineer reviews full transcript
       Makes final judgment
```

### Pattern 3: Solid Hypothesis → Confirmed ✅

```
Initial: confidence=0.72 (medium-high)
         ↓
Challenge Loop:
  Loop 1: Agent 2 can't disprove, confidence_in_disproof=0.65
          → boost +0.05 → confidence=0.77
          → Exit (converged)
  
Final: confidence=0.77
       Hypothesis accepted, jira_comment notes validation
```

---

## Troubleshooting

### Challenge Loop Not Running

**Symptom**: `[stage1.5]` messages not in logs

**Checklist**:
- [ ] Did you pass `--challenge` flag?
- [ ] Is confidence < `--challenge-confidence-threshold` (default 0.75)?
- [ ] Is verdict == "bug" (not duplicate/invalid)?
- [ ] Is investigation not degraded?

### Confidence Not Improving

**Symptom**: `challenge_history` shows Agent 2 found flaws, but confidence dropped

**Reason**: Agent 1's regenerated hypothesis is lower confidence (more constrained)

**Expected**: Loop continues to refine; may converge later or escalate

### Tool Budget Exhausted

**Symptom**: Challenge loop stops early, Agent 2 output incomplete

**Reason**: Hit max tool turns (10 for Agent 2, 8 for Agent 1 regen)

**Action**: Increase `--challenge-max-loops` or use different ticket

---

## Next Steps

1. **Test on Real Tickets**: Use `--challenge` with low-confidence findings
2. **Monitor Results**: Track confidence improvements and convergence rates
3. **Collect Feedback**: Did the challenge loop help? Did escalation point to real ambiguity?
4. **Tune Thresholds**: Adjust `--challenge-confidence-threshold` based on your needs
5. **Enable by Default** (optional): Consider making `enable_challenge=True` in production

---

## Questions?

Refer to:
- `PIPELINE_EVOLUTION.md` — Before/after diagrams
- `IMPLEMENTATION_SUMMARY.md` — Full technical details
- Code comments in `stage1_5_challenge.py` — Implementation details
