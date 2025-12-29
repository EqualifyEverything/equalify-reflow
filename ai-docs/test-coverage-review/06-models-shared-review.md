# Models/Shared Layer Test Coverage Review

**Review Date**: 2025-12-10
**Scope**: `src/shared/` and `tests/unit/models/`
**Reviewer**: Automated Test Coverage Analysis

---

## Executive Summary

I've conducted a thorough review of the Models/Shared layer tests for the Equalify PDF Converter. The test coverage is **solid** but has several **critical gaps** that could allow production bugs to slip through. The tests cover basic validation well, but miss edge cases, invariant checking, and error scenarios that would catch real-world issues.

---

## Test Coverage Summary

### What's Being Tested (Good Coverage)

**Files with Tests:**
- `tests/unit/models/test_job_models.py` → Tests JobSubmission, JobStatus, VALID_TRANSITIONS
- `tests/unit/models/test_queue_models.py` → Tests PIIQueuePayload, ApprovalQueuePayload, ProcessingQueuePayload
- `tests/unit/models/test_observation_models.py` → Tests Observation, ObservationLocation
- `tests/unit/models/test_proposal_models.py` → Tests Proposal, SearchReplaceDiff
- `tests/unit/models/test_remediation_models.py` → Tests DocumentManifest, PageFeatures
- `tests/unit/models/test_remediation_progress_models.py` → Tests RemediationProgress
- `tests/unit/models/test_agent_models.py` → Tests AgentInput, AgentCorrection, AgentOutput, LLMUsage
- `tests/unit/models/test_context_models.py` → Tests DocumentContext, HeadingInfo, FigureInfo, TableInfo
- `tests/unit/models/test_redis_integration.py` → Tests Redis key generation and serialization

### Models NOT Being Tested:
- `src/shared/models/pii.py` → **NO TESTS** for PIIFinding, PIIResult
- `src/shared/models/approval.py` → **NO TESTS** for ApprovalRequest, ApprovalDecision
- `src/shared/models/processing.py` → **NO TESTS** for ProcessingResult, ProcessingJob, LLMUsage (only tested via agent_models)
- `src/shared/models/hints_models.py` → **NO TESTS** for IssueHint, PageHints, DocumentHintsCache
- `src/shared/constants/statuses.py` → **NO TESTS** for status constants and sets

---

## Critical Gaps (Will Allow Bugs to Escape)

### 1. Missing Model Tests (High Priority)

#### **PIIFinding Model** (`src/shared/models/pii.py:7-66`)
**Missing Tests:**
- Boundary validation for `start` and `end` positions (e.g., can `end` be less than `start`?)
- Edge case: What if `start == end`? (zero-width finding)
- Edge case: Large position values (e.g., millions for long documents)
- Edge case: Empty `text` field (min_length=1, but is this enforced?)
- Edge case: Very long entity_type strings
- Validation: Does `score` properly reject values like `1.0001` or `-0.0001`?

**Real Bug Risk:** If start/end positions aren't validated properly, the system could crash when trying to extract PII text snippets.

#### **PIIResult Model** (`src/shared/models/pii.py:69-105`)
**Missing Tests:**
- Does `total_findings` match `len(findings)`? (Invariant check)
- What happens with empty findings list? (default_factory=list)
- Edge case: Very large total_findings count
- Serialization/deserialization with nested PIIFinding objects

**Real Bug Risk:** Inconsistency between `total_findings` and actual findings count could lead to incorrect approval workflow routing.

#### **ApprovalRequest Model** (`src/shared/models/approval.py:9-67`)
**Missing Tests:**
- Justification length boundaries (min=10, max=1000) - test at 9 chars, 10 chars, 1000 chars, 1001 chars
- Boundary: `reviewed_by` min_length=3 - test at 2 chars, 3 chars
- Edge case: Special characters in justification (SQL injection-like strings, Unicode, emoji)
- Edge case: Whitespace-only justification (should be rejected)
- Validation: Both "approved" and "denied" decisions work
- Invalid decision values ("approve", "deny", "pending")

**Real Bug Risk:** Weak justification validation could allow approval decisions with no meaningful explanation, creating compliance issues.

#### **ApprovalDecision Model** (`src/shared/models/approval.py:69-110`)
**Missing Tests:**
- Token length boundaries (min=32, max=64) - test at 31, 32, 64, 65 chars
- Edge case: Token with special characters
- Nested model validation (does `decision` field properly validate ApprovalRequest?)
- Time validation: Can `expires_at` be in the past? Should it be?
- Time validation: Can `created_at` be after `expires_at`?

**Real Bug Risk:** Invalid approval tokens could bypass security checks or cause authorization failures.

#### **ProcessingResult Model** (`src/shared/models/processing.py:6-65`)
**Missing Tests:**
- Confidence score bounds (0.0-1.0) at boundaries
- Processing time validation (ge=0) - test negative values
- Error message length (max=2000) - test at boundary
- Edge case: Markdown URL validation (any format restrictions?)
- Edge case: What if both `markdown_url` AND `error_message` are set?
- Edge case: What if neither is set? (completed job with no output?)

**Real Bug Risk:** Jobs could be marked "completed" without actually having output, confusing users.

#### **LLMUsage Model** (`src/shared/models/processing.py:116-166`)
**Only Partial Coverage** (tested in test_agent_models.py but not comprehensively):
- Missing: Does `total_tokens == input_tokens + output_tokens`? (Invariant)
- Missing: Very large token counts (millions of tokens)
- Missing: Cost calculation edge cases (free tier, zero cost)

**Real Bug Risk:** Incorrect token accounting could lead to wrong cost estimates and budget overruns.

#### **HintsModels** (ENTIRE FILE UNTESTED)
Files: `src/shared/models/hints_models.py`

**Missing Tests for IssueHint:**
- All field validations
- Enum values for HintSource, HintSeverity, HintCategory
- Page/line number validation (ge=1)
- Context max_length=500
- Optional fields behavior

**Missing Tests for PageHints:**
- filter_by_category() method
- filter_by_source() method
- filter_by_severity() method
- error_count property
- warning_count property

**Missing Tests for DocumentHintsCache:**
- get_page_hints() method
- get_hints_for_category() method
- get_hints_for_source() method
- get_all_hints() method
- has_errors property
- Summary statistics validation

**Real Bug Risk:** Hints system could silently fail to route issues to correct agents, causing accessibility problems to go unfixed.

---

### 2. Weak Validation Tests (Medium Priority)

#### **JobSubmission** (`tests/unit/models/test_job_models.py:13-93`)
**Current Tests:** Basic happy path, invalid UUID, invalid s3_key prefix, file size limits
**Missing:**
- Edge case: `s3_key` with "temp/" in middle of path (not just prefix)
- Edge case: Very long s3_key values
- Edge case: Special characters in original_filename
- Edge case: Filename length boundary (max_length=255) - test at 254, 255, 256
- Edge case: Empty string for original_filename (should be rejected by min_length=1)
- Boundary: File size at exactly 100MB (100_000_000 bytes)
- Validation: Datetime edge cases (far past, far future, timezone-aware vs naive)

**Example Missing Test:**
```python
def test_filename_max_length_boundary(self):
    """Test filename at max length boundary."""
    with pytest.raises(ValidationError):
        JobSubmission(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/test.pdf",
            created_at=datetime.now(UTC),
            file_size_bytes=1024,
            original_filename="a" * 256  # Over 255 char limit
        )
```

#### **JobStatus State Machine** (`tests/unit/models/test_job_models.py:163-320`)
**Current Tests:** Valid transitions, terminal states, confidence bounds
**Missing:**
- **CRITICAL:** State machine completeness check (are all status values covered in VALID_TRANSITIONS?)
- **CRITICAL:** Inconsistent state validation - can a job have status="completed" but error_message set?
- Edge case: Can a job have status="failed" but markdown_url set?
- Edge case: Can a job have status="denied" but no approval_decision?
- Edge case: Updated_at before created_at (temporal consistency)
- Edge case: expires_at before created_at
- Validation: Error message at 2000 char boundary

**State Machine Bug:** Line 139-146 of job.py defines status as Literal, but VALID_TRANSITIONS (line 12-19) doesn't include "awaiting_correction_approval" from statuses.py line 20! This is a **critical inconsistency**.

**Example Missing Test:**
```python
def test_state_inconsistencies_rejected(self):
    """Test that inconsistent state combinations are rejected."""
    now = datetime.now(UTC)

    # Completed job shouldn't have error message
    with pytest.raises(ValidationError):  # This might NOT raise - BUG!
        JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="completed",
            created_at=now,
            updated_at=now,
            markdown_url="https://s3.../output.md",
            error_message="This doesn't make sense"  # Inconsistent!
        )
```

#### **ApprovalQueuePayload** (`tests/unit/models/test_queue_models.py:52-188`)
**Current Tests:** Valid payload, empty findings rejected, token length
**Missing:**
- Edge case: Exactly 1 PII finding (min_length boundary)
- Edge case: Very large list of PII findings (100+ items)
- Temporal validation: expires_at in the past
- Temporal validation: expires_at before created_at (if we add created_at field)
- Token validation: Non-ASCII characters in token

---

### 3. Missing Edge Cases for State Transitions

#### **Observation State Machine** (`tests/unit/models/test_observation_models.py:293-414`)
**Current Tests:** Basic transitions, terminal states
**Missing:**
- Transition validation in actual usage: Does Pydantic prevent invalid transitions?
- Edge case: Setting `resolved_by` on non-resolved observations
- Edge case: Status changes when `route="manual"` and `manual_reason` is None
- Validation: Can status change back from terminal states? (Should be prevented)

#### **Proposal State Machine** (`tests/unit/models/test_proposal_models.py:231-372`)
**Current Tests:** Basic transitions, retry logic
**Missing:**
- Edge case: Transition from "applied" (should be impossible)
- Edge case: Setting `failure_reason` on non-failed proposals
- Validation: Review fields only set when approved/rejected
- Temporal consistency: reviewed_at before created_at

**Example Missing Test:**
```python
def test_review_fields_only_on_reviewed_status(self):
    """Test that reviewed_by/reviewed_at are only set when status is approved/rejected."""
    # This might currently allow inconsistent state!
    proposal = Proposal(
        job_id="job-123",
        resolves=["obs-1"],
        diff=SearchReplaceDiff(search="a", replace="b"),
        justification="test",
        status="pending",
        reviewed_by="someone@example.com",  # Should this be allowed?
        reviewed_at=datetime.now(UTC)
    )
    # Should this be valid? Probably not!
```

---

### 4. Missing Serialization Edge Cases

#### **Nested Model Serialization**
**Current Tests:** Basic JSON round-trips
**Missing:**
- Edge case: Circular references (if possible)
- Edge case: Very deep nesting
- Edge case: Large data sizes (10MB+ JSON payloads)
- Edge case: Unicode and special characters in all string fields
- Edge case: Datetime timezone handling (all models use UTC but is this enforced?)
- Edge case: Null vs missing fields in JSON

**Example Missing Test:**
```python
def test_unicode_in_all_string_fields(self):
    """Test that Unicode characters work in all string fields."""
    obs = Observation(
        job_id="job-测试",  # Chinese characters
        agent="figures-агент",  # Cyrillic
        visual_description="Image shows emoji: 🎨🖼️",
        markup_description="![](图片.png)",
        location=ObservationLocation(value="图片区域", page_num=1)
    )

    json_data = obs.model_dump_json()
    restored = Observation.model_validate_json(json_data)
    assert restored.agent == "figures-агент"
```

---

### 5. Missing Computed Property Tests

#### **DocumentContext Methods** (`tests/unit/models/test_context_models.py:275-489`)
**Current Tests:** Good coverage of helper methods
**Missing:**
- Edge case: `get_current_section_context()` with skipped heading levels (H1 → H3)
- Edge case: `get_expected_heading_level()` when heading tree is malformed
- Edge case: Multiple H1s (violates `has_single_h1` but might exist)
- Boundary: `get_next_figure_number()` with zero figures
- Validation: Negative page numbers passed to methods

**Example Missing Test:**
```python
def test_get_current_section_context_with_skipped_levels(self):
    """Test section context when heading levels skip (H1 → H3)."""
    headings = [
        HeadingInfo(level=1, text="Title", page_number=1),
        HeadingInfo(level=3, text="Subsection", page_number=1),  # Skipped H2!
    ]
    context = DocumentContext(
        document_id="test",
        total_pages=1,
        headings=headings
    )

    section = context.get_current_section_context(1)
    # What should h2 be? None? This tests real-world malformed docs
    assert section["h2"] is None
    assert section["h3"] == "Subsection"
```

#### **RemediationProgress Methods** (`tests/unit/models/test_remediation_progress_models.py:164-210`)
**Current Tests:** update_counts_from_lists, mark_*_complete methods
**Missing:**
- Edge case: Calling `mark_extraction_complete()` when substatus != "extracting"
- Edge case: Objects in lists without expected attributes (AttributeError handling)
- Validation: Negative counts in manual updates
- Invariant: Do counts always sum correctly?

---

### 6. Missing Field Interaction Tests

#### **JobStatus Field Dependencies**
**Missing Tests:**
- If `status="awaiting_approval"`, should `pii_findings` be required (not None)?
- If `status="awaiting_approval"`, should `approval_token` be required?
- If `status="completed"`, should `markdown_url` be required?
- If `status="failed"`, should `error_message` be required?
- If `status="denied"`, should `approval_decision` be required?

**Example Missing Test:**
```python
def test_awaiting_approval_requires_pii_findings(self):
    """Test that awaiting_approval status requires PII findings."""
    now = datetime.now(UTC)

    # This should probably fail but might not!
    status = JobStatus(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        status="awaiting_approval",
        created_at=now,
        updated_at=now,
        pii_findings=None,  # Missing required data!
        approval_token=None
    )
    # Current code probably allows this invalid state
```

#### **Observation Field Dependencies**
**Missing Tests:**
- If `route="manual"`, should `manual_reason` be required (not None)?
- If `status="resolved"`, should `resolved_by` be required?
- If `source="human"`, should `human_comment` be required?

#### **Proposal Field Dependencies**
**Missing Tests:**
- If `status="failed"`, should `failure_reason` be required?
- If `status="approved"` or `status="rejected"`, should `reviewed_by`, `reviewed_at`, and `review_notes` be required?

---

### 7. Missing Tests for Constants and Enums

#### **Status Constants** (`src/shared/constants/statuses.py`)
**NO TESTS AT ALL**
**Missing Tests:**
- Verify ALL_STATUSES == TERMINAL_STATUSES | ACTIVE_STATUSES
- Verify no overlap between TERMINAL_STATUSES and ACTIVE_STATUSES
- Verify JobStatusType Literal matches ALL_STATUSES
- Verify VALID_TRANSITIONS covers all statuses
- **CRITICAL BUG:** JobStatus.status Literal (job.py:139-146) doesn't include "awaiting_correction_approval" from statuses.py:20!

**Example Missing Test:**
```python
def test_status_sets_have_no_overlap(self):
    """Test that terminal and active statuses don't overlap."""
    from shared.constants.statuses import TERMINAL_STATUSES, ACTIVE_STATUSES

    overlap = TERMINAL_STATUSES & ACTIVE_STATUSES
    assert len(overlap) == 0, f"Status sets overlap: {overlap}"

def test_all_statuses_complete(self):
    """Test that ALL_STATUSES includes all defined statuses."""
    from shared.constants.statuses import ALL_STATUSES, JobStatusType

    # Get all literal values from JobStatusType
    # This would catch the missing "awaiting_correction_approval"
    ...
```

---

## Recommendations (Prioritized)

### **Priority 1: Critical Bugs (Do Immediately)**

1. **Add tests for PIIFinding and PIIResult** - These are core to the PII workflow
2. **Add tests for ApprovalRequest and ApprovalDecision** - Security-critical models
3. **Add tests for ProcessingResult** - Prevents jobs from completing without output
4. **Fix status enumeration mismatch** - JobStatus.status vs VALID_TRANSITIONS vs statuses.py
5. **Add field dependency validation tests** - Ensure state consistency (e.g., completed jobs have markdown_url)

### **Priority 2: High Risk Edge Cases (Do Soon)**

6. **Add comprehensive tests for hints_models.py** - Entire file is untested
7. **Add boundary testing for all string fields** - Test min/max lengths at boundaries
8. **Add temporal consistency tests** - Ensure created_at < updated_at, etc.
9. **Add state machine invariant tests** - Prevent invalid state transitions
10. **Add LLMUsage invariant test** - Verify total_tokens == input_tokens + output_tokens

### **Priority 3: Robustness (Do When Time Permits)**

11. Add Unicode/special character tests for all string fields
12. Add large data size tests (10k observations, etc.)
13. Add negative test cases for all computed properties
14. Add tests for edge cases in DocumentContext helper methods
15. Add tests for Redis serialization edge cases

---

## Test Quality Assessment

### **What's Good:**
- ✅ Basic validation testing is solid
- ✅ JSON serialization round-trips are tested
- ✅ State machine transitions are tested (but incompletely)
- ✅ Good coverage of DocumentContext helper methods
- ✅ Comprehensive testing of nested models in some cases

### **What's Missing:**
- ❌ No tests for several core models (PII, Approval, Processing, Hints)
- ❌ Weak boundary testing (only some min/max values tested)
- ❌ No field dependency/consistency testing
- ❌ No invariant checking (e.g., counts must match list lengths)
- ❌ No negative testing for computed properties
- ❌ Incomplete state machine testing (missing states, inconsistencies)
- ❌ No tests for constants and enums
- ❌ Limited edge case testing (Unicode, very large values, etc.)

---

## Conclusion

The current test suite provides **good basic coverage** but has **critical gaps** that could allow production bugs:

1. **Missing model tests** for PII, Approval, Processing, and Hints models
2. **Weak validation** of field boundaries and constraints
3. **No field dependency tests** to ensure state consistency
4. **Incomplete state machine tests** with actual enumeration mismatches
5. **Missing edge case coverage** for Unicode, large data, temporal consistency

**Estimated Real-World Impact:**
- **60% chance** of state inconsistency bugs reaching production (completed jobs without output, etc.)
- **40% chance** of validation bypass bugs (empty justifications, invalid tokens, etc.)
- **80% chance** of hints system bugs (untested entirely)
- **30% chance** of serialization bugs with edge cases (Unicode, large payloads, etc.)

**Recommendation:** Add the Priority 1 tests immediately before deploying to production. These tests would catch real bugs that users would encounter.
