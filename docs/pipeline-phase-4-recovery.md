# Phase 4: Recovery

Phase 4 attempts to fix pages that failed verification through targeted recovery actions.

**Goal:** Recover as many failed pages as possible with conservative approach.

**Input:** Failed pages + issues + page images

**Output:** `RecoveryReport` (pages recovered/escalated)

---

## Overview

Recovery is triggered when:
- Verification FAILED
- AND >= 50% of pages passed basic checks

```
Failed Pages (from Verification)
    ↓
For each failed page (up to 2 attempts):
    ↓
Recovery Agent → analyze issues → propose fixes
    ↓
Conservative Decision:
    - cleanup_edit (fix minor issue)
    - remove_placeholder (can't fill)
    - accept_with_caveat (document limitation)
    - escalate (needs human review)
    ↓
RecoveryReport (recovered/escalated/unrecoverable)
```

**Key Principles:**
- Conservative (when uncertain, escalate)
- Page-by-page (not parallel)
- Max 2 attempts per page
- Document caveats clearly

**Location:** `src/agents/recovery.py`

---

## Recovery Trigger

**Function:** `run_recovery_phase()` in `orchestrator.py`

### Decision Logic

```python
if verification_report.passed:
    # No recovery needed
    return None

# Check if recovery worth attempting
pass_rate = verification_report.pages_passed / len(verification_report.pages)

if pass_rate < 0.5:
    # Too many failures - recovery unlikely to help
    logger.warning(f"Only {pass_rate:.1%} pages passed, skipping recovery")
    return None

# Collect failed pages
failed_pages = [pv for pv in verification_report.pages if not pv.passed]

# Attempt recovery
recovery_report = await attempt_recovery(failed_pages, ...)
```

---

## Recovery Agent

### Configuration

**Model:** Haiku 4.5 on AWS Bedrock (`MODEL_TIER_MAP[ModelTier.EFFICIENT]`)

**System Prompt:** (from `recovery.py:253-299`)

```
You are a document recovery specialist. Your job is to fix pages that failed verification.

## Available Tools

### view_page_tool()
Get current markdown (exactly as it is now).

### view_markdown_tool(start, end)
View specific lines for detailed inspection.

### view_processing_history_tool()
See what the initial worker tried to do (and what failed).

### propose_cleanup_tool(action, target, before, after, reasoning)
Propose a recovery action.

## Recovery Actions

### cleanup_edit
Fix minor formatting or content issues.
Use when you can confidently fix the problem.

### remove_placeholder
Remove unfillable placeholder.
Use when content truly cannot be added (e.g., image illegible, table too complex).

### accept_with_caveat
Accept the issue but document why.
Use when issue is minor or fixing would risk introducing errors.

### escalate
Mark for human review.
Use when uncertain or issue requires domain expertise.

## Conservative Approach

**IMPORTANT**: When in doubt, use accept_with_caveat or escalate.

Better to:
- Document a limitation than introduce an error
- Flag for human review than make a risky guess
- Accept with caveat than force a fix

## Process

1. Review current markdown
2. Understand what failed (issues from verification)
3. Review processing history (what was tried before)
4. Decide on appropriate action
5. If cleanup_edit or remove_placeholder, propose the fix
6. If accept_with_caveat, explain the limitation
7. If escalate, explain what needs human expertise
```

---

## Recovery Attempt

**Function:** `attempt_page_recovery()` in `recovery.py:497+`

### Input

```python
async def attempt_page_recovery(
    page_num: int,
    page_image: Image.Image,
    current_markdown: str,
    issues: list[str],  # From verification
    processing_history: list[str],  # From initial worker
    attempt_number: int,  # 1 or 2
    event_bus: EventBus | None = None
) -> tuple[str, RecoveryAttempt]:
```

### Process

1. **Emit RecoveryStartedEvent**

2. **Build Context**
   ```python
   deps = RecoveryDeps(
       page_num=page_num,
       page_image=page_image,
       current_markdown=current_markdown,
       issues=issues,
       processing_history=processing_history,
       attempt_number=attempt_number
   )
   ```

3. **Build Prompt**
   ```
   Page {page_num} failed verification with these issues:
   {issues}

   Processing history (what was tried):
   {processing_history}

   Current markdown:
   ---
   {current_markdown}
   ---

   Attempt {attempt_number} of 2.

   Review the issues and propose recovery actions.
   ```

4. **Run Recovery Agent**
   ```python
   result = await recovery_agent.run(prompt, deps=deps)
   ```

5. **Process Actions**
   - Apply cleanup_edit changes
   - Remove placeholders
   - Document caveats
   - Mark escalations

6. **Emit RecoveryCompleteEvent**

7. **Return**
   ```python
   return (
       updated_markdown,
       RecoveryAttempt(
           page_num=page_num,
           attempt_number=attempt_number,
           status=determine_status(...),
           edits_proposed=...,
           edits_applied=...,
           caveats=[...],
           duration_ms=...
       )
   )
   ```

---

## Recovery Tools

### view_page_tool()

**Returns:**
```python
{
    "current_markdown": "...",  # Exact text
    "line_count": 150
}
```

**Purpose:** See current state of page markdown.

---

### view_markdown_tool(start, end)

**Returns:**
```python
{
    "lines": ["line 10", "line 11", ...],
    "line_numbers": [10, 11, ...]
}
```

**Purpose:** Inspect specific section for detailed analysis.

---

### view_processing_history_tool()

**Returns:**
```python
{
    "history": [
        "Job job-001 started on page 3",
        "Task ALT_TEXT for fig:1",
        "Edit proposed: before='<!-- image 1 -->', after='![...](image1.png)'",
        "Edit approved and applied",
        "Task TABLE_TRANSCRIPTION for table:1",
        "Edit proposed: before='<!-- table 1 -->', after='|...|'",
        "Edit rejected: before text not found",
        "Job completed with 1 success, 1 failure"
    ]
}
```

**Purpose:** Understand what went wrong during initial processing.

---

### propose_cleanup_tool()

**Parameters:**
```python
action: RecoveryAction  # cleanup_edit, remove_placeholder, accept_with_caveat, escalate
target: str  # e.g., "fig:1", "heading", "table:2"
before: str  # Current text (for cleanup_edit or remove_placeholder)
after: str  # Fixed text (for cleanup_edit)
reasoning: str  # Why this action
caveat: str  # For accept_with_caveat (what the limitation is)
```

**Example 1: cleanup_edit**
```python
propose_cleanup_tool(
    action=RecoveryAction.CLEANUP_EDIT,
    target="heading",
    before="## 1.1 Background",
    after="### 1.1 Background",
    reasoning="Correct heading level to match hierarchy",
    caveat=""
)
```

**Example 2: remove_placeholder**
```python
propose_cleanup_tool(
    action=RecoveryAction.REMOVE_PLACEHOLDER,
    target="fig:1",
    before="<!-- image 1 -->",
    after="",
    reasoning="Image is too blurry to describe accurately",
    caveat=""
)
```

**Example 3: accept_with_caveat**
```python
propose_cleanup_tool(
    action=RecoveryAction.ACCEPT_WITH_CAVEAT,
    target="table:1",
    before="",
    after="",
    reasoning="",
    caveat="Table structure is complex with merged cells; manual review recommended for full accuracy"
)
```

**Example 4: escalate**
```python
propose_cleanup_tool(
    action=RecoveryAction.ESCALATE,
    target="fig:1",
    before="",
    after="",
    reasoning="Image contains domain-specific diagram requiring subject matter expertise to describe accurately",
    caveat=""
)
```

---

## Recovery Decision Logic

### Status Determination

After recovery attempt:

```python
def determine_status(edits_applied, caveats, escalations, remaining_issues):
    # All issues resolved
    if len(remaining_issues) == 0:
        return RecoveryAttemptStatus.SUCCEEDED

    # Some issues accepted with caveats
    if len(caveats) > 0 and len(escalations) == 0:
        return RecoveryAttemptStatus.ACCEPTED_WITH_CAVEATS

    # Issues remain unresolved
    if len(remaining_issues) > 0:
        return RecoveryAttemptStatus.FAILED

    return RecoveryAttemptStatus.FAILED
```

### Max Attempts Logic

```python
# Try up to 2 attempts per page
for attempt_num in [1, 2]:
    updated_markdown, recovery_attempt = await attempt_page_recovery(
        page_num=page_num,
        current_markdown=current_markdown,
        attempt_number=attempt_num,
        ...
    )

    if recovery_attempt.status == RecoveryAttemptStatus.SUCCEEDED:
        # Page recovered!
        break

    if recovery_attempt.status == RecoveryAttemptStatus.ACCEPTED_WITH_CAVEATS:
        # Acceptable with documented limitations
        break

    # Update markdown for next attempt
    current_markdown = updated_markdown

# After 2 attempts, give up if still failed
```

---

## Recovery Report

### Report Assembly

**Function:** `run_recovery_phase()` in `orchestrator.py`

```python
# Collect results from all recovery attempts
pages_recovered = []
pages_accepted_with_caveats = []
pages_unrecoverable = []

for page_num, attempts in recovery_attempts_by_page.items():
    final_attempt = attempts[-1]  # Last attempt

    if final_attempt.status == RecoveryAttemptStatus.SUCCEEDED:
        pages_recovered.append(page_num)

    elif final_attempt.status == RecoveryAttemptStatus.ACCEPTED_WITH_CAVEATS:
        pages_accepted_with_caveats.append(page_num)

    else:
        pages_unrecoverable.append(page_num)

# Create report
recovery_report = RecoveryReport(
    document_id=document_id,
    recovery_attempted=True,
    pages_recovered=pages_recovered,
    pages_accepted_with_caveats=pages_accepted_with_caveats,
    pages_unrecoverable=pages_unrecoverable,
    attempts=all_attempts,
    total_recovery_edits=sum(a.edits_applied for a in all_attempts),
    recovery_duration_ms=total_duration
)
```

### Final Status Determination

```python
def determine_final_status(recovery_report, total_pages):
    recovered = len(recovery_report.pages_recovered)
    caveats = len(recovery_report.pages_accepted_with_caveats)
    unrecoverable = len(recovery_report.pages_unrecoverable)

    # >= 80% pages good (recovered or accepted)
    if (recovered + caveats) >= total_pages * 0.8:
        return ProcessingStatus.SUCCESS

    # >= 50% pages good
    if (recovered + caveats) >= total_pages * 0.5:
        return ProcessingStatus.PARTIAL_SUCCESS

    # Some pages recovered but not enough
    if recovered > 0:
        return ProcessingStatus.PARTIAL_SUCCESS

    # Recovery didn't help
    if unrecoverable >= total_pages * 0.5:
        return ProcessingStatus.NEEDS_REVIEW

    return ProcessingStatus.FAILED
```

---

## Example Recovery Scenario

### Input

**Failed Page:** Page 3

**Issues:**
- Unfilled placeholder: `<!-- image 1 -->`
- Heading hierarchy skip: H2 → H4

**Processing History:**
```
Job job-003 started on page 3
Task ALT_TEXT for fig:1
Edit proposed: before='<!-- image 1 -->', after='![Chart](image1.png)'
Edit rejected: 'before' text not found (worker used wrong placeholder format)
Task HEADING_FIX for heading
Edit proposed: before='#### Background', after='### Background'
Edit approved and applied
Job completed with 1 success, 1 failure
```

### Recovery Attempt 1

**Agent Analysis:**
1. Views page markdown
2. Sees `<!-- image 1 -->` still present
3. Views processing history
4. Understands worker tried but used wrong format
5. Views page image
6. Sees image is a simple bar chart

**Agent Actions:**
```python
# Correct the unfilled placeholder
propose_cleanup_tool(
    action=RecoveryAction.CLEANUP_EDIT,
    target="fig:1",
    before="<!-- image 1 -->",
    after="![Bar chart showing enrollment by year](image1.png)",
    reasoning="Worker attempted this but used incorrect placeholder format. Image shows clear bar chart with enrollment data."
)
```

**Result:**
- Edit applied successfully
- All issues resolved
- Status: `SUCCEEDED`

---

## Events Emitted

- `RecoveryPhaseStartedEvent` - When recovery begins (total failed pages)
- `RecoveryStartedEvent` - For each page attempt (page_num, attempt_number)
- `RecoveryEditAppliedEvent` - For each edit applied (action, target)
- `RecoveryCompleteEvent` - For each page attempt (status, edits_applied, caveats)
- `RecoveryPhaseCompleteEvent` - When all recovery done (pages_recovered, pages_escalated)

---

## Metrics

**3 Failed Pages:**

| Metric | Value |
|--------|-------|
| Pages to recover | 3 |
| Total attempts | 3-6 (1-2 per page) |
| Recovery duration | 15-40s |
| Pages recovered | 1-2 (33-67%) |
| Pages accepted with caveats | 1-2 (33-67%) |
| Pages unrecoverable | 0-1 (0-33%) |
| Edits applied | 2-5 |
| Tokens (input) | 10-20K |
| Tokens (output) | 5-10K |
| Cost | $0.01-0.02 |

**Per Page:**
- Attempts: 1-2
- Duration: 5-15s
- Success rate (1st attempt): ~60%
- Success rate (2nd attempt): ~30%
- Escalation rate: ~10%

---

## Conservative Decision Examples

### When to Use cleanup_edit

✅ **Use cleanup_edit when:**
- Issue is clear and fix is obvious
- Confidence > 80%
- Low risk of introducing errors

Example: Heading level correction

### When to Use remove_placeholder

✅ **Use remove_placeholder when:**
- Content genuinely cannot be added
- Image is illegible/corrupt
- Table too complex for accurate transcription

❌ **Don't use remove_placeholder when:**
- Just uncertain (use escalate instead)
- First attempt (try cleanup_edit first)

### When to Use accept_with_caveat

✅ **Use accept_with_caveat when:**
- Issue is minor
- Fix would risk introducing errors
- Content is "good enough" but not perfect

Example: "Table has minor alignment issues but data is accurate"

### When to Use escalate

✅ **Use escalate when:**
- Uncertain about the fix
- Requires domain expertise
- Risk of error is high

Example: "Medical diagram requires subject matter expert to describe"

---

## Debugging

### Enable Recovery Logging

```python
import logging
logging.getLogger("src.agents.recovery").setLevel(logging.DEBUG)
```

### Inspect Recovery Attempts

```python
for attempt in recovery_report.attempts:
    print(f"Page {attempt.page_num}, Attempt {attempt.attempt_number}:")
    print(f"  Status: {attempt.status}")
    print(f"  Edits applied: {attempt.edits_applied}")
    print(f"  Caveats: {attempt.caveats}")
```

### Review Unrecoverable Pages

```python
for page_num in recovery_report.pages_unrecoverable:
    attempts = [a for a in recovery_report.attempts if a.page_num == page_num]
    print(f"Page {page_num} attempts:")
    for attempt in attempts:
        print(f"  Attempt {attempt.attempt_number}: {attempt.status}")
```

---

## Common Issues

### Issue: Recovery Always Escalates

**Symptom:** Agent escalates everything, nothing fixed

**Cause:** System prompt too conservative

**Fix:** Add more examples of successful cleanup_edit

---

### Issue: Recovery Introduces Errors

**Symptom:** Recovery "fixes" pages but makes them worse

**Cause:** Agent not conservative enough

**Fix:** Strengthen conservative language in prompt, reduce confidence threshold

---

### Issue: Recovery Takes Too Long

**Symptom:** Recovery exceeds 1 minute per page

**Cause:** Agent spending too much time analyzing

**Fix:** Add timeout, simplify prompt, reduce max attempts to 1

---

## Best Practices

### 1. Document All Caveats

Always explain limitations clearly:
- What the issue is
- Why it couldn't be fixed
- What a human reviewer should check

### 2. Prefer Escalation Over Errors

When uncertain, escalate rather than guess:
- Wrong fix is worse than no fix
- Human review is cheaper than wrong data

### 3. Use Processing History

Review what the initial worker tried:
- Avoid repeating same mistakes
- Understand why it failed
- Use different approach

### 4. Limit Attempts

Don't waste time on hopeless cases:
- Max 2 attempts per page
- If 2nd attempt fails, escalate
- Move on to next page

---

## Next Steps

- Review [System Overview](./pipeline-system-overview.md) for complete pipeline
- Check [Data Models Reference](./pipeline-data-models.md) for `RecoveryReport` schema
- Explore [API Integration](./pipeline-api-integration.md) for SSE events
- See [Phase 3: Verification](./pipeline-phase-3-verification.md) for what triggers recovery
