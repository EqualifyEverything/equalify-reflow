# Fix Tests - Address Test Coverage Gaps

Execute targeted test improvements based on the test coverage review and prioritization guide.

## Arguments

`<LAYER>` - The layer to address: `api`, `services`, `workers`, `middleware`, `agents`, `models`, or `integration`

Example: `/fix-tests services`

---

## Phase 1: Load Context

### Read Prioritization Guide
1. Read `ai-docs/test-coverage-review/00-prioritization-guide.md`
2. Understand the ROI tiers:
   - **Tier 1**: High ROI, must add
   - **Tier 2**: Medium ROI, consider later
   - **Tier 3**: Low ROI, skip
   - **Tier 4**: Fix in code, not tests

### Read Executive Summary
1. Read `ai-docs/test-coverage-review/00-executive-summary.md`
2. Note the top 10 critical gaps
3. Identify which gaps apply to the requested layer

### Read Layer-Specific Review
Based on `<LAYER>` argument, read the corresponding review:

| Argument | Review File |
|----------|-------------|
| `api` | `ai-docs/test-coverage-review/01-api-layer-review.md` |
| `services` | `ai-docs/test-coverage-review/02-services-layer-review.md` |
| `workers` | `ai-docs/test-coverage-review/03-workers-layer-review.md` |
| `middleware` | `ai-docs/test-coverage-review/04-middleware-utils-review.md` |
| `agents` | `ai-docs/test-coverage-review/05-agents-layer-review.md` |
| `models` | `ai-docs/test-coverage-review/06-models-shared-review.md` |
| `integration` | `ai-docs/test-coverage-review/07-integration-e2e-review.md` |

### Report Context
Before proceeding, report:
- Layer being addressed
- Tier 1 items for this layer (from prioritization guide)
- Tier 2 items for this layer (optional, for later)
- Items to skip (Tier 3) and why
- Code fixes instead of tests (Tier 4)

---

## Phase 2: Filter by ROI

### Identify Tier 1 Tests Only
From the layer review, extract ONLY the Tier 1 (High ROI) recommendations.

**For each potential test, evaluate:**

1. **Production incident risk**: Would this bug cause downtime, data loss, or compliance issues?
2. **Debugging difficulty**: Would this be obvious from logs/metrics or take hours to trace?
3. **Test simplicity**: Can we catch this with <50 lines?
4. **Framework testing**: Are we testing our code or Pydantic/FastAPI/PydanticAI?

**Include if:**
- High incident risk AND hard to debug AND simple to test AND tests our code

**Exclude if:**
- Low incident risk OR easy to debug OR complex test OR tests framework

### Create Filtered Test List
Report:
```
# Tier 1 Tests for <LAYER>

## Will Implement
1. [Test name] - [Why it's Tier 1]
2. [Test name] - [Why it's Tier 1]

## Skipping (Tier 2/3)
1. [Test name] - [Why: low ROI / tests framework / easy to debug]

## Code Fixes Instead (Tier 4)
1. [Issue] - [Code fix approach]

Estimated effort: ~X lines, ~Y minutes
```

**Wait for user confirmation before proceeding.**

---

## Phase 3: Examine Existing Code

### Read Implementation Files
For each Tier 1 test, read the source file being tested:
- Understand the actual implementation
- Identify the specific functions/methods to test
- Note existing error handling patterns
- Find integration points

### Read Existing Test Files
Check if test files already exist:
- `tests/unit/<layer>/test_*.py`
- `tests/integration/<layer>/test_*.py`

If files exist:
- Understand existing test patterns
- Identify where new tests fit
- Note fixture usage

### Read Test Infrastructure
1. Read `tests/conftest.py` - Global fixtures
2. Read `tests/conftest_fixtures/` - Shared fixtures
3. Identify reusable mocks and factories

### Report Findings
```
# Implementation Analysis

## Source Files
- [file]: [key functions to test]

## Existing Tests
- [file]: [X tests exist, covering Y]

## Available Fixtures
- [fixture]: [what it provides]

## Test Patterns to Follow
- [pattern from existing tests]
```

---

## Phase 4: Plan Tests

### Design Minimal Effective Tests
For each Tier 1 item, design the simplest test that catches the bug:

**Template:**
```python
# Test: [name]
# Catches: [specific bug]
# Lines: ~[estimate]

def test_[name]():
    """[One line description of what bug this catches]."""
    # Setup: [minimal setup]
    # Act: [single action]
    # Assert: [specific assertion]
```

### Prefer Parameterized Tests
When testing multiple cases, use `@pytest.mark.parametrize`:

```python
@pytest.mark.parametrize("input,expected", [
    (case1, result1),
    (case2, result2),
])
def test_handles_cases(input, expected):
    assert function(input) == expected
```

One parameterized test > multiple similar tests.

### Report Test Plan
```
# Test Plan for <LAYER>

## Test 1: [name]
- File: tests/unit/<layer>/test_[name].py
- Function: test_[name]
- Lines: ~[X]
- Catches: [specific bug]

## Test 2: [name]
...

## Code Fixes (if any)
- File: src/[path]
- Change: [description]
- Lines: ~[X]

Total: ~[X] lines of test code, ~[Y] lines of code fixes

Ready to implement? [Yes/No]
```

**Wait for user confirmation before proceeding.**

---

## Phase 5: Implement

### Create/Update Test Files
1. Create new test files if needed
2. Add tests to existing files if appropriate
3. Follow existing patterns and conventions

### Implementation Rules

**DO:**
- Use existing fixtures from `tests/conftest.py`
- Follow existing test naming patterns
- Keep tests focused (one assertion per concept)
- Use `pytest.mark.asyncio` for async tests
- Add brief docstrings explaining what bug the test catches

**DON'T:**
- Create new fixtures when existing ones work
- Test framework behavior (Pydantic validation, etc.)
- Write integration tests when unit tests suffice
- Add tests for Tier 2/3 items

### Apply Code Fixes (Tier 4)
If the prioritization guide recommends code fixes:
1. Add Pydantic validators for state consistency
2. Use single source of truth for enums/types
3. Make implicit requirements explicit

### Run Tests
After implementation:
```bash
make test-fast  # Run unit tests
```

Verify:
- New tests pass
- No regressions in existing tests
- Type checking passes (`make typecheck` if available)

---

## Phase 6: Report Completion

### Summary Report
```
# Test Fixes Complete: <LAYER>

## Tests Added
| Test | File | Lines | Catches |
|------|------|-------|---------|
| test_X | tests/unit/.../test_x.py | ~20 | [bug] |

## Code Fixes Applied
| Fix | File | Lines | Prevents |
|-----|------|-------|----------|
| validator_X | src/.../model.py | ~5 | [invalid state] |

## Skipped (as planned)
- [item]: [reason per prioritization guide]

## Verification
- [ ] All new tests pass
- [ ] No regressions
- [ ] Type checking passes

## Next Steps
- Consider Tier 2 items: [list]
- Or move to next layer: `/fix-tests <next-layer>`
```

---

## Layer-Specific Guidance

### `/fix-tests services`
**Tier 1 Focus:**
- `test_pii_service.py` - PII routing (approval vs processing)
- Circuit breaker tests (if simple to add)

**Skip:**
- Comprehensive TTL tests (already good)
- Mock accuracy improvements

### `/fix-tests workers`
**Tier 1 Focus:**
- Shutdown requeueing for each worker
- Basic PIIWorker coverage

**Skip:**
- Metrics verification
- Concurrent job tests

### `/fix-tests middleware`
**Tier 1 Focus:**
- `test_rate_limit.py` - Fail-open, threshold
- `test_retry_helpers.py` - Error categorization

**Skip:**
- Logging middleware tests
- CORS tests

### `/fix-tests api`
**Tier 1 Focus:**
- skip_pii_scan flow (if quick)

**Skip:**
- Unit tests (integration tests sufficient)
- Response schema tests

### `/fix-tests agents`
**Skip most:**
- LLM response tests (PydanticAI handles)
- Prompt construction tests

**Maybe:**
- Basic routing tests if missing

### `/fix-tests models`
**Tier 4 Focus (code fixes):**
- State consistency validators
- Status type unification

**Skip:**
- Boundary tests (Pydantic handles)
- Unicode tests

### `/fix-tests integration`
**Tier 2:**
- One true E2E workflow (if time permits)

**Skip:**
- Reducing mocking (large effort)
- Realistic PDF fixtures

---

## Important Rules

### ⚠️ ROI First
- Always filter by prioritization guide
- If not Tier 1, don't implement
- Tier 4 = code fix, not test

### ✅ Minimal Tests
- Simplest test that catches the bug
- Parameterized > multiple tests
- ~50 lines max per test file addition

### 🚫 Never Test
- Framework behavior (Pydantic, PydanticAI, FastAPI)
- Happy paths already covered by integration tests
- Edge cases that rarely happen

### 📝 Document
- Docstring explains what bug test catches
- Report what was skipped and why

---

## Example Usage

```bash
# Address highest-priority gaps (middleware has rate limiting + retry)
/fix-tests middleware

# Address services (PII routing)
/fix-tests services

# Address workers (shutdown requeueing)
/fix-tests workers

# Skip agents (most are Tier 3)
# /fix-tests agents  # Not recommended per guide
```
