# Comprehensive Test Review: Agents Layer (PydanticAI + AWS Bedrock)

**Review Date**: 2025-12-10
**Scope**: `src/agents/` and `tests/unit/agents/`
**Reviewer**: Automated Test Coverage Analysis

---

## Executive Summary

After deep analysis of the agents layer implementation (`src/agents/`) and unit tests (`tests/unit/agents/`), I've identified **critical gaps** in test coverage that could allow production failures to slip through. While the tests cover happy paths well, they completely miss the reality of working with LLMs: **unpredictable outputs, API failures, token limits, and edge cases in response parsing**.

---

## Critical Gaps in Test Coverage

### 1. **LLM Response Variation Testing - COMPLETELY MISSING**

**The Problem:** All tests mock perfect, well-formed LLM responses. Real LLMs return variable, sometimes malformed data.

**Missing Test Scenarios:**

#### **Malformed Structured Outputs**
- **What's NOT tested:** What happens when Claude returns JSON with missing required fields?
  - Example: `AnalysisOutput` without `heading_tree` (required field)
  - Example: `ImageAnalysis` with `image_index=0` (violates `ge=1` constraint)

- **File:** No tests exist for Pydantic validation failures during LLM response parsing
- **Impact:** Runtime crashes when PydanticAI fails to parse LLM output into structured models
- **Real scenario:** Claude might hallucinate fields, skip required data, or return malformed nested structures

**Evidence from test files:**
```python
# test_analysis_agent.py:564 - Always returns perfect AnalysisOutput
mock_result.output = AnalysisOutput(
    document_title="Test",
    heading_tree=HeadingTree(document_title="Test"),  # Always perfect
    page_features=[AnalysisPageFeatures(page_num=1)],  # Always valid
)
```

**Missing test:**
```python
@pytest.mark.asyncio
async def test_analyze_handles_llm_missing_required_fields():
    """Test handling when LLM returns incomplete structured output."""
    # Mock LLM returning output missing 'heading_tree'
    # Should either retry or raise meaningful error
```

#### **Unexpected Field Values**
- **What's NOT tested:** LLM returning values outside expected ranges
  - `confidence=1.5` (should be 0.0-1.0)
  - `page_num=-1` (should be >= 1)
  - `layout_type="three_column"` (not in allowed literals)

**Missing test:**
```python
def test_analysis_output_invalid_confidence_from_llm():
    """Test that invalid confidence from LLM is caught and handled."""
    # What happens if Claude returns {"confidence": 1.8}?
```

---

### 2. **Token Limit Scenarios - NOT TESTED**

**The Problem:** Bedrock has hard token limits. No tests verify behavior when limits are hit.

**Missing Scenarios:**

#### **Input Token Overflow**
- **Location:** `analysis_agent.py:259` - Sends ALL page images in one request
- **What's NOT tested:** What if 50-page PDF exceeds max input tokens?
- **Real scenario:** Large documents could exceed Bedrock's input limit (~200K tokens for Sonnet)

```python
# analysis_agent.py:259
messages = self._build_image_messages(pages)  # Could be massive
result = await agent.run(messages, model_settings={...})
```

**No test like:**
```python
@pytest.mark.asyncio
async def test_analyze_handles_input_token_limit():
    """Test handling when input exceeds model context window."""
    # Create 100+ page document
    # Should either batch or raise clear error
```

#### **Output Token Truncation**
- **Location:** `extraction_agent.py:74` - Sets `max_tokens=16384`
- **What's NOT tested:** What if markdown output exceeds this limit?
- **Impact:** Truncated markdown, incomplete document extraction

**Missing test:**
```python
@pytest.mark.asyncio
async def test_extract_handles_output_token_truncation():
    """Test when extracted markdown exceeds max_tokens."""
    # Mock response that hits token limit
    # Should detect truncation and warn/retry
```

---

### 3. **Retry Logic - SUPERFICIAL TESTING**

**The Problem:** Tests verify retries are configured but don't test actual retry behavior.

#### **Validation Failure Retries**
- **Location:** `base_agent.py:189` - `retries=self.config.max_retries`
- **What's NOT tested:** Does PydanticAI actually retry when output validation fails?
- **Current tests:** Only check that `max_retries` parameter is passed

```python
# test_base_agent.py:291 - Only checks parameter was set
assert call_kwargs[1]["retries"] == 3  # But does it actually retry?
```

**Missing test:**
```python
@pytest.mark.asyncio
async def test_agent_retries_on_validation_failure():
    """Test agent retries when LLM returns invalid structured output."""
    call_count = 0
    async def mock_run_with_failures(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            # Return invalid output (missing required field)
            return MockResult(output={})  # Invalid
        return MockResult(output=ValidOutput())

    # Should retry 2 times then succeed
    assert call_count == 3
```

#### **API Failure Retries**
- **What's NOT tested:** Bedrock API failures (throttling, 500 errors, timeouts)
- **Real scenario:** Bedrock throttling during high load
- **Impact:** No verification that transient errors are retried

---

### 4. **Prompt Construction Edge Cases - NOT TESTED**

**The Problem:** Tests mock pre-formatted prompts but don't verify prompt building logic.

#### **Extraction Agent Prompt Building**
**Location:** `extraction_agent.py:197-205`
```python
user_prompt = self.prompts["user_prompt"].format(
    total_pages=manifest.total_pages,
    document_title=manifest.document_title,
    document_type=manifest.document_type,
    heading_tree=heading_tree_text,
    page_features=page_features_text,
    layout_notes=manifest.analysis_notes or "No additional notes.",
)
```

**What's NOT tested:**
- What if `document_title` contains `{` or `}` (breaks `.format()`)?
- What if `analysis_notes` is `None` but code expects string?
- What if `heading_tree_json` is invalid JSON?

**Missing test:**
```python
@pytest.mark.asyncio
async def test_extract_handles_special_chars_in_title():
    """Test extraction when document title contains format string chars."""
    manifest = DocumentManifest(
        document_title="Cost Analysis {2024}",  # Contains braces
        ...
    )
    # Should escape or handle without KeyError
```

#### **Structure Agent Heading Tree Parsing**
**Location:** `structure_agent.py:188-211`
```python
tree = json.loads(heading_tree_json)  # Can fail
```

**Current test:** Only tests valid JSON and completely invalid JSON
**Missing:** Partially valid JSON (valid syntax but unexpected structure)

```python
def test_summarize_heading_tree_with_unexpected_structure():
    """Test handling heading tree with unexpected keys."""
    # Valid JSON but wrong structure
    tree_json = '{"unexpected_key": "value", "sections": "not_a_list"}'
    # Should handle gracefully, not crash
```

---

### 5. **Multimodal Input Handling - MINIMAL TESTING**

**The Problem:** Image handling is superficially tested with fake data.

#### **Image Decoding Errors**
**Location:** `figures_agent.py:132`
```python
image_bytes = base64.b64decode(page.image_base64)
```

**What's NOT tested:**
- Invalid base64 strings (wrong encoding, corrupted data)
- Empty image data
- Non-PNG formats (code assumes PNG: `media_type="image/png"`)

**Missing test:**
```python
@pytest.mark.asyncio
async def test_analyze_handles_invalid_base64():
    """Test handling when page.image_base64 is invalid."""
    pages = [PageData(page_num=1, image_base64="!!!invalid!!!")]
    # Should raise clear error or skip page gracefully
```

#### **Large Image Handling**
**What's NOT tested:** Images that are too large for Bedrock vision API
- **Real scenario:** High-res PDF pages exceeding Bedrock's 5MB per image limit
- **Impact:** API rejection with unclear error

---

### 6. **Error Handling Consistency - PARTIALLY TESTED**

**Good:** Tests verify agents continue on page-level errors
**Missing:** Verification of error messages, logging, and error types

#### **Figures Agent Error Handling**
**Location:** `figures_agent.py:153-158`
```python
except Exception as e:
    logger.error(f"FiguresAgent failed on page {page.page_num}: {e}", exc_info=True)
    # Continue with other pages
```

**Current test:** `test_analyze_continues_on_page_error` verifies continuation
**Missing:** Verification that error is logged with correct context

```python
@pytest.mark.asyncio
async def test_analyze_logs_page_errors_with_context(caplog):
    """Test that page errors are logged with full context."""
    # Trigger error on page 2
    # Verify log contains: job_id, page_num, document_title, error type
```

---

### 7. **Agent Router Edge Cases - PARTIALLY COVERED**

**Good coverage:** Empty lists, missing agents, page filtering
**Missing:** Complex failure scenarios

#### **Mixed Agent Failures**
**What's NOT tested:** Some agents succeed, some fail - are partial results handled correctly?

**Current test:** `test_continues_on_agent_failure` only tests 1 failure
**Missing:** All combinations of success/failure across 4 agents

```python
@pytest.mark.asyncio
async def test_partial_agent_failures_with_observations():
    """Test handling when multiple agents fail but some succeed."""
    # figures: success (2 observations)
    # tables: failure (exception)
    # structure: success (1 observation)
    # typography: failure (timeout)
    # Should return 3 observations total, log 2 failures
```

---

### 8. **Cost Calculation Edge Cases - MINIMAL TESTING**

**Good coverage:** Basic cost calculations with known pricing
**Missing:** Real-world cost scenarios

#### **Zero Token Edge Case**
**Location:** `test_base_agent.py:227` - Tests zero tokens
**Missing:** What about `None` token counts from Bedrock?

```python
# base_agent.py:268
input_tokens = usage.input_tokens or 0  # Handles None
output_tokens = usage.output_tokens or 0
```

**The test exists but could be more thorough:**
```python
def test_usage_with_none_tokens():
    """Test cost calculation when Bedrock returns None for token counts."""
    mock_usage = MagicMock()
    mock_usage.input_tokens = None
    mock_usage.output_tokens = None
    # Should default to 0 and not crash
```

#### **Cost Tracking Accuracy**
**What's NOT tested:** Are costs accurately accumulated across multiple agent calls?
- **Scenario:** Document processed by 4 agents, each making 5 LLM calls
- **Missing:** Verification that total cost = sum of all individual costs

---

### 9. **Observation Generation Edge Cases - PARTIALLY TESTED**

**Good coverage:** Severity mapping, confidence routing
**Missing:** Observation deduplication, invalid observation data

#### **Duplicate Observations**
**What's NOT tested:** Multiple agents detecting the same issue
- **Scenario:** Both `StructureAgent` and `TypographyAgent` flag same heading issue
- **Missing:** Deduplication logic or at least awareness of duplicates

#### **Observation ID Uniqueness**
**Location:** All agents use `str(uuid4())` for observation IDs
**What's NOT tested:** Are UUIDs actually unique across concurrent agent runs?
- **Unlikely but possible:** UUID collision in distributed system
- **Missing:** Verification of ID uniqueness in integration tests

---

### 10. **YAML Prompt Loading - SUPERFICIALLY TESTED**

**Good coverage:** File not found, valid YAML
**Missing:** Malformed YAML, missing required keys

#### **Malformed YAML**
**Location:** `base_agent.py:129`
```python
prompts = yaml.safe_load(f)  # Can fail
```

**Current tests:** Only test FileNotFoundError
**Missing:** YAML syntax errors, encoding issues

```python
def test_load_prompts_handles_invalid_yaml():
    """Test handling when YAML file is malformed."""
    prompts_file.write_text("invalid: yaml: syntax:")
    # Should fall back to defaults, not crash
```

#### **Missing Required Prompt Keys**
**What's NOT tested:** YAML file exists but missing `system_prompt`

```python
def test_load_prompts_missing_system_prompt():
    """Test when YAML is valid but missing required keys."""
    prompts_file.write_text("other_key: value")
    # Should either use default or raise clear error
```

---

## What's Being Tested Well

### Strengths

1. **Pydantic Model Validation** (`test_analysis_agent.py:115-265`)
   - Excellent coverage of field constraints, required fields, defaults
   - Tests invalid values for enums, numeric ranges, string lengths

2. **Agent Initialization** (all `test_*_agent.py` files)
   - Model tier selection verified (Sonnet vs Haiku)
   - Configuration values checked
   - Prompt loading fallbacks tested

3. **Basic Agent Workflows**
   - Happy path end-to-end flows work
   - Observation conversion logic tested
   - Page filtering correctly verified

4. **Cost Calculation** (`test_base_agent.py:197-263`)
   - Pricing tiers correctly mapped
   - Basic cost math verified
   - Zero token edge case covered

5. **Agent Router** (`test_agent_router.py`)
   - Excellent coverage of page filtering logic
   - Agent registration well tested
   - Error continuation verified

---

## Specific Test Recommendations

### High Priority (Must Add)

1. **LLM Response Malformation Tests**
```python
# Add to test_analysis_agent.py
@pytest.mark.asyncio
async def test_analyze_handles_llm_invalid_confidence():
    """Test handling when LLM returns confidence > 1.0."""
    mock_result.output = AnalysisOutput(
        heading_tree=HeadingTree(document_title="Test"),
        page_features=[],
        confidence=1.5  # Invalid - should fail validation
    )
    # Verify ValidationError is caught and handled
```

2. **Token Limit Tests**
```python
# Add to test_extraction_agent.py
@pytest.mark.asyncio
async def test_extract_warns_on_large_document():
    """Test extraction with document likely to exceed token limits."""
    pages = [PageData(page_num=i, image_base64=large_image)
             for i in range(100)]  # Likely over 200K tokens
    # Should either batch, warn, or fail gracefully
```

3. **Retry Behavior Tests**
```python
# Add to test_base_agent.py
@pytest.mark.asyncio
async def test_run_agent_retries_on_pydantic_validation_error():
    """Test that agent retries when output validation fails."""
    attempts = []
    async def mock_run(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            # Return invalid output
            raise ValidationError(...)
        return valid_result
    # Verify 3 attempts made
```

4. **Image Decoding Error Tests**
```python
# Add to test_figures_agent.py
@pytest.mark.asyncio
async def test_analyze_handles_corrupt_base64():
    """Test handling of corrupted base64 image data."""
    pages = [PageData(page_num=1, image_base64="not-valid-base64!!!")]
    # Should log error and continue or fail gracefully
```

### Medium Priority (Should Add)

5. **Prompt Construction Edge Cases**
```python
# Add to test_extraction_agent.py
async def test_extract_escapes_special_chars_in_manifest():
    """Test extraction handles special characters in manifest fields."""
    manifest.document_title = "Cost {Analysis} 2024"
    manifest.analysis_notes = "Notes with {{ braces }}"
    # Should not raise KeyError from .format()
```

6. **Large Output Handling**
```python
# Add to test_extraction_agent.py
async def test_extract_handles_output_exceeding_max_tokens():
    """Test detection when markdown output is truncated."""
    # Mock response that hit max_tokens limit
    # Should warn or indicate truncation
```

7. **Error Message Quality Tests**
```python
# Add to all agent tests
async def test_analyze_error_includes_context(caplog):
    """Test error messages include job_id, page_num, document_title."""
    # Trigger error
    # Verify log contains all debugging context
```

### Low Priority (Nice to Have)

8. **Concurrent Agent Execution**
```python
# Add to test_agent_router.py
async def test_concurrent_agent_execution_no_race_conditions():
    """Test that concurrent agent calls don't interfere."""
    # Run 4 agents simultaneously on same document
    # Verify observations don't overlap or corrupt
```

9. **Memory Usage Tests**
```python
# Add integration test
async def test_large_document_memory_usage():
    """Test memory usage stays reasonable for large documents."""
    # Process 200-page document
    # Verify memory doesn't grow unbounded
```

---

## Critical Production Scenarios NOT Covered

### 1. **Bedrock API Specific Errors**
Real Bedrock errors not simulated:
- `ThrottlingException` (rate limits)
- `ModelTimeoutException`
- `ValidationException` (invalid parameters)
- `AccessDeniedException` (IAM issues)

**Why this matters:** Code has no Bedrock-specific error handling
**Impact:** Generic exceptions with unclear error messages

### 2. **Vision API Edge Cases**
Not tested for Bedrock Converse API vision:
- Multiple images per message (do all agents support this?)
- Image format validation (code assumes PNG)
- Image size limits (5MB per Bedrock docs)

### 3. **Prompt Token Estimation**
No tests verify input token counts before API call:
- **Risk:** Sending requests that will definitely fail
- **Missing:** Pre-flight token count estimation

### 4. **Streaming Responses**
Code doesn't use streaming (Bedrock supports it):
- **Missing:** Tests for streaming vs non-streaming
- **Impact:** May not detect if Bedrock returns partial responses

---

## Architecture-Level Concerns

### 1. **No Integration Tests with Real Bedrock**
All tests mock Bedrock completely. Consider:
- **End-to-end test** with real Bedrock in CI/CD (expensive but valuable)
- **Contract tests** verifying mock responses match real API

### 2. **Error Recovery Strategies Unclear**
When LLM fails, what happens?
- Do agents fail fast or try fallbacks?
- Are partial results saved?
- Can jobs resume after failure?

**Tests should verify:** Failure modes and recovery paths

### 3. **Observability Gaps**
Tests verify logging exists but not:
- Structured log format (JSON logs?)
- Trace IDs for distributed tracing
- Metrics emission (Prometheus/CloudWatch)

---

## Test Quality Metrics

| Category | Current Coverage | Missing Coverage | Risk Level |
|----------|------------------|------------------|------------|
| Model Validation | ✅ Excellent | - | Low |
| Happy Paths | ✅ Good | - | Low |
| LLM Response Variation | ❌ None | Malformed outputs, edge cases | **CRITICAL** |
| Token Limits | ❌ None | Input/output overflow | **HIGH** |
| Retry Logic | ⚠️ Config Only | Actual retry behavior | **HIGH** |
| Error Handling | ⚠️ Partial | Error messages, types | Medium |
| Prompt Construction | ⚠️ Partial | Edge cases, escaping | Medium |
| Image Handling | ⚠️ Minimal | Decoding errors, formats | **HIGH** |
| Cost Calculation | ✅ Good | None usage scenarios | Low |
| Agent Routing | ✅ Excellent | Concurrent execution | Low |

---

## Conclusion

**These tests will catch:** Configuration errors, basic logic bugs, Pydantic validation issues

**These tests will NOT catch:** LLM output variability, API failures, token limit issues, image corruption, prompt injection edge cases

**Biggest Risk:** Production will encounter LLM response formats never seen in tests. The mocks return perfect, well-formed data. Real Claude will return surprising variations.

**Recommendation:** Before production, add at minimum the 5 high-priority test categories. Consider a "chaos testing" suite that intentionally sends malformed data through the pipeline to verify resilience.
