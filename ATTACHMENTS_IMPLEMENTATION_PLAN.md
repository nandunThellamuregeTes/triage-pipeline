# Attachments with PII Redaction - Implementation Plan

**Branch:** `feature/attachments-with-pii-redaction`
**Date Started:** 2026-10-06
**Status:** Planning → Implementation

## Confirmed Requirements

### Security & Privacy
- ✅ Redact school name from all attachments (don't send to LLM)
- ✅ Redaction strategy: REDACT (keep attachment, mask sensitive values)
- ✅ Show user summary of what was redacted
- ✅ Do NOT redact from main ticket description (only attachments)

### Functionality
- ✅ Enable/disable via CLI argument: `--enable-attachments`
- ✅ Text file types: `.log, .txt, .csv, .json, .md, .sql, .xml`
- ✅ Image extraction via Claude vision API (for evidence context)
- ✅ PII detection: Presidio (open-source, $0 cost)

### Error Handling
- ✅ If attachment processing fails: skip that attachment, continue pipeline
- ✅ Don't halt pipeline due to attachment issues

---

## Implementation Phases

### Phase 1: Schema & Data Model (Hours: 1-2)
**Goal:** Add attachment support to core data structures

Files to modify:
- `triage_pipeline/schemas.py`
  - Add `Attachment` dataclass with fields: name, mime_type, content, is_redacted, redaction_summary
  - Add `attachments: list[Attachment]` to `NormalizedTicket`
  - Update `content_fingerprint()` to include attachment hashes

Tasks:
- [ ] Define Attachment dataclass
- [ ] Update NormalizedTicket schema
- [ ] Update fingerprinting logic
- [ ] Update tests/fixtures

---

### Phase 2: Jira Adapter Enhancement (Hours: 3-4)
**Goal:** Extract attachments from Jira tickets

Files to modify:
- `triage_pipeline/adapters/jira.py`
  - Fetch attachments from Jira API
  - Download attachment files with size limits (default: 10MB per file, 50MB per ticket)
  - Extract text content based on MIME type

New dependencies:
- `python-magic` or `python-magic-bin` (detect MIME types)

Tasks:
- [ ] Add attachment fetching to Jira adapter
- [ ] Implement download with size limits
- [ ] Handle download errors gracefully
- [ ] Extract text from `.log, .txt, .csv, .json, .md, .sql, .xml`
- [ ] Prepare image attachments for vision API
- [ ] Add error handling & logging

---

### Phase 3: PII Detection & Redaction (Hours: 4-5)
**Goal:** Detect and redact personal data before sending to LLM

New dependencies:
- `presidio-analyzer` (PII entity detection)
- `presidio-anonymizer` (PII redaction)

Files to modify:
- `triage_pipeline/pii_redaction.py` (new file)
  - Create `PIIRedactor` class
  - Implement school name detection (custom + Presidio)
  - Implement redaction logic (REDACT strategy)
  - Track what was redacted for summary

Tasks:
- [ ] Install Presidio dependencies
- [ ] Create PIIRedactor class with methods:
  - `detect_pii(text)` → list of detected entities
  - `redact_text(text)` → redacted text + summary
  - `redact_attachment(attachment)` → redacted attachment
- [ ] Custom patterns for school name detection
- [ ] Test with sample data (without real personal data)
- [ ] Handle edge cases (partial matches, false positives)

---

### Phase 4: Image Processing with Vision API (Hours: 2-3)
**Goal:** Extract text from images and redact PII

Files to modify:
- `triage_pipeline/pii_redaction.py` (extend)
  - Add `process_image_attachment(attachment)` method
  - Call Claude vision API to extract text
  - Apply PII redaction to extracted text
  - Store extracted text in attachment

Tasks:
- [ ] Implement image→text extraction using Claude API
- [ ] Apply PII redaction to extracted text
- [ ] Cache extracted text (avoid repeated API calls)
- [ ] Handle image processing errors
- [ ] Track API call costs/latency

---

### Phase 5: CLI Integration (Hours: 1-2)
**Goal:** Add `--enable-attachments` CLI flag

Files to modify:
- `triage_pipeline/main.py`
  - Add `--enable-attachments` argument to CLI
  - Pass flag through pipeline stages
  - Log attachment processing status

Tasks:
- [ ] Add argparse argument
- [ ] Pass through orchestrator to stages
- [ ] Add logging/debug output
- [ ] Update help text

---

### Phase 6: Stage Integration (Hours: 2-3)
**Goal:** Include attachments in LLM investigation

Files to modify:
- `triage_pipeline/stages/stage1_classify.py`
  - Include attachment summaries in classification prompt
  - Optional: Include attachment content if relevant
  
- `triage_pipeline/stages/stage2_investigate.py`
  - Include attachment content in technical_signals context
  - LLM can reference attachments in hypothesis

Tasks:
- [ ] Update Stage 1 to include attachments
- [ ] Update Stage 2 to include attachments
- [ ] Test with real ticket data
- [ ] Verify LLM uses attachment content appropriately

---

### Phase 7: Testing & Validation (Hours: 3-4)
**Goal:** Ensure attachments work correctly without breaking pipeline

Tests to create/modify:
- `tests/test_pii_redaction.py` (new)
  - Test school name detection
  - Test PII redaction on various text samples
  - Test edge cases (false positives, false negatives)
  - Test image text extraction
  
- `tests/test_schemas.py` (extend)
  - Test Attachment dataclass
  - Test NormalizedTicket with attachments
  - Test fingerprinting with attachments

- `tests/test_jira_adapter.py` (extend)
  - Mock Jira API with attachments
  - Test attachment download
  - Test error handling

- `tests/test_integration.py` (extend)
  - Test full pipeline with `--enable-attachments`
  - Test without `--enable-attachments` (backward compatibility)
  - Test redaction summary output

- `tests/fixtures/` (new)
  - Sample attachments for testing (.log, .txt, .json, images)
  - Sample tickets with attachments

Tasks:
- [ ] Create comprehensive test suite
- [ ] Test redaction accuracy on sample data
- [ ] Verify backward compatibility (--enable-attachments false)
- [ ] Test error scenarios
- [ ] Run all tests (target: 40+ new tests, no regressions)

---

### Phase 8: Documentation (Hours: 1-2)
**Goal:** Document attachment feature and redaction behavior

Files to create:
- `ATTACHMENTS_FEATURE_GUIDE.md`
  - User guide for `--enable-attachments`
  - What attachments are supported
  - PII redaction behavior
  - Cost/performance implications
  - Troubleshooting

- `ATTACHMENTS_IMPLEMENTATION.md`
  - Technical design
  - Architecture diagram
  - PII redaction flow
  - Testing summary
  - Known limitations

Tasks:
- [ ] Create user guide
- [ ] Create technical documentation
- [ ] Document API changes
- [ ] Document cost/performance

---

## Rollout Checklist

### Before merge to master:
- [ ] All 40+ tests pass
- [ ] No regressions in existing tests (must stay at 33+)
- [ ] Manual testing with sample tickets
- [ ] PII redaction tested with real school data patterns
- [ ] Backward compatibility verified (without --enable-attachments)
- [ ] Documentation complete
- [ ] Code review by owner

### After merge to master:
- [ ] Update README with new CLI option
- [ ] Add to CHANGELOG
- [ ] Tag release with feature
- [ ] Notify users of new capability

---

## Known Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|-----------|
| **Over-redaction** | Medium | Use REDACT strategy (keeps structure), test on real data |
| **False negatives in PII** | Medium | Combine Presidio + custom patterns, manual review first runs |
| **Image processing cost** | Low | Can disable with --enable-attachments false, monitor API costs |
| **Image quality issues** | Low | Vision API handles most image types, skip unreadable ones |
| **Attachment size explosion** | Medium | Enforce per-file (10MB) and per-ticket (50MB) limits |
| **Breaking existing pipeline** | Low | Feature behind flag, existing code unchanged |
| **Security regression** | HIGH | Test redaction thoroughly, never send unredacted school data |

---

## Resource Estimates

| Phase | Hours | Risk | Priority |
|-------|-------|------|----------|
| 1. Schema | 1-2 | Low | Must-have |
| 2. Jira adapter | 3-4 | Low | Must-have |
| 3. PII redaction | 4-5 | High | Must-have |
| 4. Image processing | 2-3 | Medium | Should-have |
| 5. CLI integration | 1-2 | Low | Must-have |
| 6. Stage integration | 2-3 | Medium | Must-have |
| 7. Testing | 3-4 | High | Must-have |
| 8. Documentation | 1-2 | Low | Must-have |
| **Total** | **~17-25 hours** | — | — |

---

## Success Criteria

- ✅ Pipeline runs with `--enable-attachments` flag
- ✅ Attachments extracted from Jira without errors
- ✅ School names redacted from all attachments
- ✅ Other PII redacted according to REDACT strategy
- ✅ Text attachments included in Stage 2 investigation
- ✅ Image attachments extracted and included via vision API
- ✅ Redaction summary shown to user
- ✅ Zero security regressions (school data never sent unredacted)
- ✅ All tests pass (40+ new tests, no regressions)
- ✅ Backward compatible (works without --enable-attachments)

---

## Next Steps

1. Confirm this plan with owner ✅
2. Start Phase 1 (Schema changes)
3. Iterate through phases 2-8
4. Create PR when complete
5. Merge to master after review & testing
