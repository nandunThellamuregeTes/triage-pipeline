# Triage Pipeline Evolution: Before & After Challenge Agent

This document compares the original triage pipeline (Stage 0–3) with the enhanced version that includes an iterative challenge loop (Stage 1.5).

---

## Before: Original Pipeline (Stage 0–3)

```
┌──────────────────────────────────────────────────────────────────┐
│                    ORIGINAL TRIAGE PIPELINE                      │
└──────────────────────────────────────────────────────────────────┘

                          INPUT
                           │
                           ▼
                  ┌─────────────────────┐
                  │   Stage 0           │
                  │  EXTRACTION         │
                  │  • Parse ticket     │
                  │  • Extract symptoms │
                  │  • Identify severity│
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │   Stage 1           │
                  │  ANALYSIS           │
                  │  Agent 1 (Analyst)  │
                  │  • No codebase      │
                  │  • Hypothesis       │
                  │  • Confidence: 0.62 │
                  │  • Verdict: bug     │
                  │  • Fix proposals    │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │   Stage 2           │
                  │  FINAL WRITE-UP     │
                  │  • jira_comment_md  │
                  │  • reporter_followup│
                  │  • next_steps       │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │   Stage 3           │
                  │  POST-OUTPUT        │
                  │  • Post to Jira     │
                  │  • Store audit log  │
                  └──────────┬──────────┘
                             │
                             ▼
                          OUTPUT

```

### Original Pipeline Characteristics:
- ✓ Single-pass analysis (Agent 1 → direct output)
- ✓ Fast, deterministic execution
- ✓ No feedback loop or iterative refinement
- ✗ **No validation of hypothesis against codebase**
- ✗ **Low-confidence findings not challenged**
- ✗ **No mechanism to catch reasoning flaws**
- ✗ **Confidence scores remain static**

**Typical Flow for TRIAG-5210:**
```
Stage 0: Extract symptoms (intermittent data loss, two editors)
         ↓
Stage 1: Agent 1 generates hypothesis
         "Race condition: stale outline field overwrites content"
         Confidence: 0.62 (lower—hard to reproduce)
         ↓
Stage 2: Write-up with caveats
         "This appears to be a race condition (likely but unverified)"
         ↓
Stage 3: Post to Jira
         Engineer takes it as-is, may need to verify independently
```

---

## After: Enhanced Pipeline with Challenge Loop (Stage 0–3 + Stage 1.5)

```
┌──────────────────────────────────────────────────────────────────┐
│              ENHANCED TRIAGE PIPELINE WITH CHALLENGE              │
└──────────────────────────────────────────────────────────────────┘

                          INPUT
                           │
                           ▼
                  ┌─────────────────────┐
                  │   Stage 0           │
                  │  EXTRACTION         │
                  │  • Parse ticket     │
                  │  • Extract symptoms │
                  │  • Identify severity│
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │   Stage 1           │
                  │  ANALYSIS           │
                  │  Agent 1 (Analyst)  │
                  │  • No codebase      │
                  │  • Hypothesis       │
                  │  • Confidence: 0.62 │
                  │  • Verdict: bug     │
                  │  • Fix proposals    │
                  │  ⚠️ Agent 1 is       │
                  │     UNAWARE of      │
                  │     challenge       │
                  └──────────┬──────────┘
                             │
         ╔═══════════════════▼═══════════════════╗
         ║  DECISION GATE: CHALLENGE?            ║
         ║  • --challenge flag?                  ║
         ║  • confidence < 0.75?                 ║
         ║  • verdict == "bug"?                  ║
         ╚═══════════════════╤═══════════════════╝
                             │
                ┌────────────┴────────────┐
                │ NO                      │ YES
                ▼                         ▼
          SKIP LOOP              ┌─────────────────────┐
                │                │ Stage 1.5           │
                │                │ CHALLENGE LOOP      │
                │                │ (max 3 iterations)  │
                │                └──────────┬──────────┘
                │                           │
                │                    ┌──────▼──────┐
                │                    │ N = 1       │
                │                    │             │
                │                    │ Agent 2     │
                │                    │ (Challenger)│
                │                    │ • HAS code  │
                │                    │ • Try to    │
                │                    │   DISPROVE  │
                │                    │   hypothesis│
                │                    └──────┬──────┘
                │                           │
                │                  ┌────────▼────────┐
                │                  │ VERDICT GATE    │
                │                  │ Can disprove?   │
                │                  │ (conf > 0.7)    │
                │                  └────┬────────┬───┘
                │                       │        │
                │                  YES  │        │  NO
                │          ┌───────────┘        └──────────┐
                │          ▼                                ▼
                │    ┌──────────────┐          ┌─────────────────┐
                │    │ Flaws Found  │          │ Hypothesis OK   │
                │    │              │          │ confidence ↑    │
                │    │ Agent 1 v2   │          │ (+0.05–0.10)    │
                │    │ Regenerates  │          │                 │
                │    └────┬─────────┘          │ EXIT LOOP       │
                │         │                    └────────┬────────┘
                │    ┌────▼─────────┐                   │
                │    │ Convergence? │                   │
                │    │ Improved?    │                   │
                │    │ N < 3?       │                   │
                │    └─┬────────┬───┘                   │
                │      │        │                       │
                │  YES │        │ NO / N ≥ 3            │
                │   ┌──┘        └──┐                    │
                │   ▼              ▼                    │
                │ Loop N+1    Escalate                  │
                │ (Agent 2)   (NEEDS_REVIEW)            │
                │   │              │                    │
                └───┼──────────────┼────────────────────┘
                    │              │
                    └──────┬───────┘
                           │
                           ▼
                  ┌─────────────────────┐
                  │   Stage 2           │
                  │  FINAL WRITE-UP     │
                  │  • jira_comment_md  │
                  │  • reporter_followup│
                  │  • next_steps       │
                  │  • challenge_history│
                  │    (if loop ran)    │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │   Stage 3           │
                  │  POST-OUTPUT        │
                  │  • Post to Jira     │
                  │  • Store write_up   │
                  │  • Attach history   │
                  │  • [Notify engineer]│
                  │    (if escalated)   │
                  └──────────┬──────────┘
                             │
                             ▼
                          OUTPUT

```

### Enhanced Pipeline Characteristics:
- ✓ **Two-stage analysis** (Agent 1 independent → Agent 2 challenges)
- ✓ **Codebase validation** (Agent 2 spot-checks code claims)
- ✓ **Iterative refinement** (feedback loop: Agent 2 → Agent 1 v2)
- ✓ **Bounded loop** (max 3 iterations, then escalate)
- ✓ **Confidence improvement** (increases +0.05–0.10 if hypothesis withstands challenge)
- ✓ **Adversarial reasoning** (Agent 2 tries to *disprove*, not validate)
- ✓ **Full audit trail** (all loop iterations recorded)
- ✓ **Human escalation path** (for true ambiguity or no convergence)
- ✓ **Selective application** (only runs on low-confidence findings, cost-conscious)

**Typical Flow for TRIAG-5210 (with challenge):**
```
Stage 0: Extract symptoms (intermittent data loss, two editors)
         ↓
Stage 1: Agent 1 generates hypothesis
         "Race condition: stale outline field overwrites content"
         Confidence: 0.62 (lower—hard to reproduce)
         ↓
Decision Gate: 0.62 < 0.75 ✓ && --challenge ✓ && verdict == "bug" ✓
              → ENTER LOOP
         ↓
Loop 1:
  Agent 2: "Challenge: Does grid editor's _saveDetail always send 'outline'?"
           "Checking code... Yes, but sometimes explicit, sometimes bundled."
           "Found gap: your hypothesis is too broad. Need to narrow to fieldless saves."
           "Confidence in disproof: 0.75"
           ↓
  Agent 1 v2: "Refined: Race is SPECIFIC to fieldless saves only."
              "The _saveDetail path bundles cached fields including stale 'outline'."
              "Confidence: 0.65 (more precise now)"
           ↓
  Convergence: Hypothesis IMPROVED → Loop again
         ↓
Loop 2:
  Agent 2: "New hypothesis is tighter. Checking alternatives..."
           "Can't find plausible alternative. Hypothesis withstands scrutiny."
           "Confidence in disproof: 0.60 (can't confidently disprove)"
           ↓
  Verdict: cannot_disprove → Hypothesis is VALID
           Confidence ↑ to 0.70 (0.65 + 0.05)
           → EXIT LOOP
         ↓
Stage 2: Final write-up
         "Race condition involving fieldless saves LIKELY causes data loss."
         "Confidence: 0.70 (improved from 0.62 after challenge validation)."
         "Challenge history: 2 loops, hypothesis refined but core idea held."
         ↓
Stage 3: Post to Jira with full challenge transcript
         Engineer has higher confidence + understanding of how hypothesis was validated
```

---

## Key Differences: Before vs. After

| Aspect | Before | After |
|--------|--------|-------|
| **Analysis Passes** | 1 (Agent 1) | 2 (Agent 1 + Agent 2) |
| **Codebase Access** | None | Agent 2 only |
| **Hypothesis Challenge** | None | Adversarial (Agent 2 tries to disprove) |
| **Feedback Loop** | None | Iterative (up to 3 loops) |
| **Confidence Calibration** | Static | Dynamic (improves if validated) |
| **Low-Confidence Handling** | Posted as-is | Challenged to improve or escalate |
| **Audit Trail** | Minimal | Full transcript of reasoning |
| **Human Escalation** | None | After 3 loops or no convergence |
| **Speed** | Fast | Slower (but only for low-confidence findings) |
| **Cost** | Low | Medium (Agent 2 iterations, but selective) |

---

## When Challenge Loop Runs vs. Skips

```
Challenge Loop RUNS if:
  ✓ User passes --challenge flag
  AND
  ✓ Agent 1's confidence < threshold (default 0.75)
  AND
  ✓ Verdict is "bug" (not "duplicate", "invalid", etc.)

Challenge Loop SKIPS if:
  ✓ No --challenge flag
  OR
  ✓ Confidence ≥ 0.75 (high confidence)
  OR
  ✓ Verdict is "duplicate" / "invalid" / etc. (obvious cases)
```

---

## CLI Usage

### Before (Original):
```bash
# Only one way to run
triage-pipeline analyze --ticket TRIAG-5210
```

### After (Enhanced):
```bash
# Fast path (no challenge)
triage-pipeline analyze --ticket TRIAG-5210

# Run with challenge (thorough)
triage-pipeline analyze --ticket TRIAG-5210 --challenge

# Custom thresholds
triage-pipeline analyze --ticket TRIAG-5210 --challenge \
  --confidence-threshold 0.8 --max-loops 2
```

---

## Output Difference

### Before (Original):
```json
{
  "verdict": "bug",
  "confidence": 0.62,
  "jira_comment_md": "...",
  "reporter_followup_md": "...",
  "suggested_next_steps": [...]
}
```

### After (Enhanced):
```json
{
  "verdict": "bug",
  "confidence": 0.70,  // Increased from 0.62 after validation
  "jira_comment_md": "...",
  "reporter_followup_md": "...",
  "suggested_next_steps": [...],
  "metadata": {
    "was_challenged": true,
    "escalated_to_human": false,
    "loops_run": 2,
    "challenge_history": [
      {
        "loop": 1,
        "agent2_verdict": "found flaws",
        "agent2_confidence_in_disproof": 0.75,
        "agent1_regenerated_hypothesis": "...",
        "convergence_status": "improved"
      },
      {
        "loop": 2,
        "agent2_verdict": "no flaws found",
        "agent2_confidence_in_disproof": 0.60,
        "convergence_status": "converged"
      }
    ]
  }
}
```

---

## Benefits Summary

### Original Pipeline:
- Fast, deterministic
- Good for obvious cases (duplicates, clear duplicates)
- Limited confidence calibration

### Enhanced Pipeline:
- **Improved accuracy** for medium-confidence findings (0.4–0.75)
- **Catches edge cases** (race conditions, heisenbug patterns)
- **Validates assumptions** against actual codebase
- **Self-correcting** (Agent 1 regenerates with better constraints)
- **Auditable** (full reasoning transcript)
- **Cost-conscious** (selective application, bounded loops)
- **Human escalation** path for true ambiguity

---

## Design Decision Points

The challenge loop is triggered by:
1. **User intent** (`--challenge` flag) — explicit opt-in
2. **Confidence threshold** (`< 0.75` default) — selective on low-confidence
3. **Verdict filter** (`verdict == "bug"`) — only for real issues, not duplicates

This keeps the pipeline efficient while improving quality where it matters most.
