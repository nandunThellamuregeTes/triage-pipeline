# Attachment Handling Analysis - Current State vs. Requirements

**Date:** 2026-10-06  
**Status:** Discussion (No code changes yet)

---

## The Question

**Does the pipeline include attachments (images, screenshots, stacktraces, database queries) in the investigation?**

---

## Current State Analysis

### 1. What the Pipeline Currently Extracts

**From Jira API:**
- ✅ Ticket ID, title, description (ADF → Markdown)
- ✅ Comments (all of them)
- ✅ Priority, labels, reporter
- ✅ Linked tickets (one level deep)
- ✅ Project/product information
- ❌ **Attachments are NOT fetched**

**Code Evidence:**
```python
# triage_pipeline/adapters/jira.py - fetch() method
return NormalizedTicket(
    ticket_id=issue.get("key", self.ticket_key),
    source="jira_api",
    product=project.get("name") or project.get("key", ""),
    title=fields.get("summary", ""),
    description_md=adf_to_markdown(fields.get("description")),
    comments=self._comments_md(issue, self.ticket_key),
    reported_priority=priority.get("name") if priority else None,
    reported_labels=list(fields.get("labels", []) or []),
    reporter=reporter.get("displayName") if reporter else None,
    linked_tickets=linked_tickets,
)
```

**Missing:** No extraction of `fields["attachment"]` field from Jira API response

### 2. NormalizedTicket Schema

**Current fields:**
```python
@dataclass
class NormalizedTicket:
    ticket_id: str
    source: Literal["jira_api", "rovo_export", "manual"]
    product: str
    title: str
    description_md: str
    comments: list[str]
    reported_priority: Optional[str]
    reported_labels: list[str]
    reporter: Optional[str]
    linked_tickets: list[LinkedTicket]
```

**Missing:** No `attachments` or `media` field

### 3. What Gets Passed to LLM Agents

**Stage 1 (Classify):**
```python
payload = {
    "ticket_id": ticket.ticket_id,
    "product": ticket.product,
    "title": ticket.title,
    "description_md": ticket.description_md,
    "comments": ticket.comments,
    "reported_priority": ticket.reported_priority,
    "reported_labels": ticket.reported_labels,
    "linked_tickets": [
        # ... linked ticket details
    ],
    "duplicate_candidates_considered": [...]
}
```

**Stage 2 (Investigate):**
```python
user_content = json.dumps({
    "ticket_title": ticket_title,
    "symptoms": ticket_facts.symptoms,
    "repro_steps": ticket_facts.repro_steps,
    "stack_trace_or_error": ticket_facts.stack_trace_or_error,  # ← Text only
    "technical_signals": ticket_facts.technical_signals,
    "prefetched_search_results": prefetched,
})
```

**Missing:** No attachments, images, or screenshots

### 4. How Attachments Are Currently Handled

| Source | Handling |
|--------|----------|
| Jira API attachments | ❌ Not fetched at all |
| Images in descriptions | ❌ Lost (ADF → Markdown strips media) |
| Screenshots | ❌ Not accessible |
| Database query results | ❌ Only if in text comments |
| Error stacktraces | ✅ Extracted to text field if in description |

---

## The Problem

### What Information Is Lost

When a reporter attaches:
- **Screenshots** of error messages or UI state
- **Database query output** files
- **Network traces** or logs
- **Stack trace images** instead of text
- **ERR logs** or performance graphs
- **Diff files** or config examples

**Current behavior:** These attachments are **completely ignored** by the pipeline.

**Impact:**
- LLM agents only see the text description and comments
- Visual information (screenshots, graphs) is lost
- Structured data files (JSON, CSV queries) are inaccessible
- Stacktrace images cannot be OCR'd or analyzed

### Example Scenario

**Jira Ticket:**
```
Title: "Database queries timing out on /api/users endpoint"

Description:
  "See attached: screenshot of slow query plan, error logs, and test results."

Attachments:
  1. query_plan.png (screenshot of EXPLAIN output)
  2. error_logs.txt (server error logs)
  3. test_results.csv (performance benchmarks)
  4. stack_trace.jpg (error screenshot from production)
```

**What Pipeline Sees:**
```
Title: "Database queries timing out on /api/users endpoint"
Description: "See attached: screenshot of slow query plan, error logs, and test results."
Comments: []
Attachments: ❌ None (ignored entirely)
```

**What's Missing:**
- The actual query execution plan
- Real error messages from logs
- Performance benchmark data
- Stack trace details

---

## Three Approaches to Consider

### Option A: Extract Text-Based Attachments Only

**Scope:** .txt, .log, .csv, .json, .md files  
**Complexity:** Low  
**Cost:** Small (text parsing)  

**Implementation:**
1. Fetch attachments from Jira API
2. Filter for text MIME types only
3. Download and read text files
4. Include in `TicketFacts` as `technical_signals` or new `extracted_attachments` field
5. Pass to LLM agents

**Pros:**
- Low implementation effort
- Immediate value for logs, queries, error traces
- No binary/image handling complexity
- Safe (text only, no security issues)

**Cons:**
- Misses images with critical visual information
- Would need file size limits to avoid huge downloads
- Requires text file type detection

### Option B: Full Attachment Support (Text + Images)

**Scope:** All files, with OCR for images  
**Complexity:** High  
**Cost:** Large (image processing, OCR)  

**Implementation:**
1. Fetch all attachments
2. For text files: extract and embed in JSON
3. For images: 
   - Use vision API (Claude vision, GPT-4V) to analyze
   - Generate detailed text descriptions
   - Include descriptions in context
4. Create `Attachment` dataclass in schemas
5. Pass to LLM agents with descriptions

**Pros:**
- Captures all attachment types
- Images analyzed by vision model
- Screenshots of errors/UI state understood

**Cons:**
- Much higher complexity
- Additional API calls (vision models)
- Token usage increase
- Cost per ticket increases
- OCR quality depends on image clarity
- Larger processing time

### Option C: Hybrid (Smart Filtering)

**Scope:** Text files + images, but smart decisions  
**Complexity:** Medium  
**Cost:** Medium  

**Implementation:**
1. Fetch attachments
2. **Smart filtering:**
   - Always include: .txt, .log, .csv, .json, .md, .sh, .sql
   - Conditionally include images only if:
     - File size < threshold
     - Filename suggests stacktrace/error/graph (e.g., "stack*", "error*")
     - Total attachment size < budget
   - Skip: binaries, executables, archives, large files
3. For images: Use vision API with selective extraction
4. Include descriptions in `TicketFacts.technical_signals`
5. Pass to LLM

**Pros:**
- Covers common use cases (logs, queries, error traces)
- Gets visual stacktraces and error graphs
- Controlled cost (selective, not all images)
- Reasonable complexity

**Cons:**
- Need heuristics for "important" images
- Still requires vision API calls
- More logic to maintain

---

## Data Model Changes Required

### For Option A (Text Only)

```python
@dataclass
class Attachment:
    filename: str
    mime_type: str
    content: str  # Text content only

@dataclass
class NormalizedTicket:
    # ... existing fields ...
    attachments: list[Attachment] = field(default_factory=list)
    
# In TicketFacts
extracted_attachments: list[str] = field(default_factory=list)
# e.g., ["From query_plan.sql: SELECT * FROM users WHERE id = ?"]
```

### For Option B/C (Full Support)

```python
@dataclass
class Attachment:
    filename: str
    mime_type: str
    size_bytes: int
    content: Optional[str]  # Text content if text file
    description: Optional[str]  # AI-generated description if image

@dataclass
class NormalizedTicket:
    # ... existing fields ...
    attachments: list[Attachment] = field(default_factory=list)
```

---

## Impact on Pipeline

### Jira Adapter Changes
- Modify `_get_issue()` to expand `"fields": "attachment"`
- Add `_extract_attachments()` method
- Update `fetch()` to download and process attachments

### Stage 1 (Classify)
- Include attachment descriptions in user content
- Pass to LLM for verdict classification

### Stage 2 (Investigate)
- Include extracted attachment content in `technical_signals`
- May reveal actual error location/cause without code search

### Stage 3 (Writeup)
- Reference attachments in reasoning
- Link to original attachment if helpful

### Cache/Fingerprinting
- Update `content_fingerprint()` to include attachment hashes
- Cache needs to invalidate if attachments change

---

## Implementation Effort

| Aspect | Option A | Option B | Option C |
|--------|----------|----------|----------|
| Schema changes | 1 hour | 2 hours | 2 hours |
| Jira adapter | 2 hours | 3 hours | 2.5 hours |
| LLM integration | 1 hour | 1.5 hours | 1.5 hours |
| Error handling | 1 hour | 2 hours | 1.5 hours |
| Testing | 1 hour | 2 hours | 1.5 hours |
| **Total** | **6 hours** | **11 hours** | **9 hours** |

---

## My Recommendation

**Start with Option A (Text-Only Attachments)** because:

1. **High ROI:** Most useful info (logs, queries, errors) are text
2. **Low risk:** No vision API needed, simpler to implement
3. **Fast:** Can ship in ~6 hours
4. **Covers common cases:**
   - Database query output files
   - Server/error logs
   - SQL files
   - Stack traces (if text)
   - Performance metrics (CSV)

**Future enhancement:** Add Option C features if:
- Users request image stacktraces
- Error traces are frequently screenshots instead of text
- Vision API budget available

---

## Questions to Answer Before Implementation

1. **Is this actually needed?** Do tickets in your Jira regularly have attachments with valuable debugging info?

2. **What types?** What file types do your reporters typically attach?
   - Text logs? → Option A is perfect
   - Mostly images? → Need Option B/C
   - Mix? → Option C best

3. **Security:** Are attachments potentially sensitive (customer data, internal IPs)?
   - Risk of feeding to LLM?
   - Need redaction/filtering?

4. **Scale:** How large are typical attachments?
   - Can pipeline handle 10MB total per ticket?
   - Or need to keep < 1MB?

5. **Priority:** How important is this vs. other features?
   - Or just good to have?

---

## Next Steps

1. **User feedback:** Do your tickets actually have important attachments?
2. **Audit current tickets:** Sample 10-20 recent tickets, count attachments and types
3. **Decide option:** A (text), B (full), or C (smart hybrid)
4. **Implement:** If decided, can start immediately

Would you like to proceed? If yes, which option interests you most?
