# TRIAG-5210 Ticket Content Extraction Analysis

## User's Concern
> "The report mentions to followup with the reporter and ask for exact reproduction steps. But the ticket TRIAG-5210 contains a section called steps to reproduce the issue. So I am not sure all the text in the ticket is even visible to the pipeline."

## What We Found

### Evidence from the Pipeline Checkpoint

**Stage 1 Extracted:**
- 3 repro_steps from the ticket:
  1. "Suspected trigger: User opens multiple browser tabs for the same area in SEQTA simultaneously"
  2. "Auto-save functionality appears to be involved in the incident"
  3. "Specific documented incident on 2026-08-13 at 07:50:40.618 +0800 involving staff ID 12949, programme ID 2728, lesson detail ID 582125"

**Stage 1 Flagged as Missing:**
- "Exact sequence of browser interactions not fully specified" ← **This is the key**
- "Unclear what specific UI actions trigger the corruption" ← **This is the key**
- (Plus 3 other missing items: logs, screenshots, frequency)

**Stage 3 Generated:**
- A reporter followup asking for "Exact reproduction steps" (point 2)

### The Apparent Redundancy
Stage 3 is asking for the SAME thing Stage 1 flagged as missing:
- Stage 1: "Exact sequence of browser interactions not fully specified"
- Stage 3: "Exact reproduction steps: The precise sequence of clicks/actions..."

This looks like asking for the same information twice.

---

## Root Cause Analysis: Three Possibilities

### Possibility 1: Custom Field Not Extracted ⚠️ LIKELY
**Most probable culprit**

Jira has a built-in "Steps to Reproduce" field (often a custom field). If TRIAG-5210's steps are in that field, the pipeline **does not extract them**.

**Current pipeline extracts:**
- `summary` (title)
- `description_md` (main description converted from ADF)
- `comments` (all comments in the ticket thread)
- `labels`, `priority`, `reporter`, `project`, etc.

**Does NOT extract:**
- Custom fields like "Steps to Reproduce", "Environment", "Reproduction Frequency", etc.

**To confirm:** Check if TRIAG-5210 has the steps in a dedicated "Steps to Reproduce" field in Jira (separate from the main description).

### Possibility 2: ADF Conversion Loses Content
The `adf_to_markdown()` converter might not properly handle certain Atlassian Document Format structures, causing:
- Sections to be lost or truncated
- Formatting that makes the LLM not recognize sections as "Steps to Reproduce"

**To confirm:** Run the pipeline with debug output to see what `description_md` actually contains.

### Possibility 3: Stage 1 LLM Didn't Recognize the Section
The steps ARE in `description_md`, but Stage 1's LLM missed them because:
- The system prompt doesn't explicitly tell it to look for "Steps to Reproduce" sections
- The formatting makes it hard to parse as repro_steps

**Current prompt says:**
> "Read every comment on the main ticket AND every linked ticket for technical_signals"

It focuses on "comments" and "technical_signals", not necessarily structured sections like "Steps to Reproduce".

---

## How Data Flows (Current Architecture)

```
Jira Issue (all fields)
    ↓
JiraRestAdapter._get_issue()
    ↓
Extract known fields:
  - summary → title
  - description (ADF) → adf_to_markdown() → description_md
  - comments → comments array
  - labels, priority, reporter, etc.
  - (Custom fields are IGNORED unless explicitly added)
    ↓
NormalizedTicket:
  - ticket_id
  - title
  - description_md (full description as Markdown)
  - comments (array of all comments)
  - reported_priority, labels, etc.
    ↓
Stage 1: build_user_content() creates JSON payload
    ↓
LLM extracts: repro_steps, technical_signals, etc.
    ↓
Stage 3 uses Stage 1's extracted facts
```

**Key constraint:** Stage 3 only knows what Stage 1 extracted. If Stage 1 doesn't receive it, Stage 3 will ask for it.

---

## What's Really Happening (Most Likely)

1. TRIAG-5210 has a "Steps to Reproduce" section in a **custom field** in Jira
2. The pipeline's JiraRestAdapter only extracts standard fields (`summary`, `description`, `comments`, etc.)
3. The custom field is ignored → Never sent to Stage 1
4. Stage 1 extracts whatever vague hints were in the description/comments
5. Stage 1 correctly flags "Exact sequence not specified" as missing
6. Stage 3 asks the reporter for better repro steps

**Result:** The information EXISTS in Jira, but the pipeline doesn't see it.

---

## Confirmation Steps Needed

To know for sure, we need you to check:

1. **Look at TRIAG-5210 in Jira UI:**
   - Is there a dedicated "Steps to Reproduce" field/section on the right sidebar?
   - Or is the "steps to reproduce" content in the main description?
   - If in a custom field, what's its field name/ID?

2. **What does that section say?**
   - Provide the actual text so we can see if it was missed

3. **How detailed is it?**
   - Specific step-by-step clicks?
   - Or vague high-level description?

---

## If It's a Custom Field Issue (Most Likely)

**The fix would be:**

1. Identify the custom field name/ID in Jira (e.g., `customfield_10123` = "Steps to Reproduce")
2. Add extraction logic to `JiraRestAdapter._get_issue()`:
   ```python
   steps_to_reproduce = fields.get('customfield_10123')  # Example ID
   if steps_to_reproduce:
       # Add to NormalizedTicket or prepend to description_md
   ```
3. Test that Stage 1 now sees it
4. Verify Stage 3 doesn't ask for repro steps if they're already provided

---

## Summary

**Current issue:** The pipeline may not be extracting custom fields from Jira, so "Steps to Reproduce" (if it's a custom field) never reaches the LLM.

**Next step:** You confirm where the steps are in Jira, and we'll add extraction for it if needed.
