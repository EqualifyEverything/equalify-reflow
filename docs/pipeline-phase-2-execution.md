# V5 Phase 2: Execution

Phase 2 executes worker jobs in parallel to make document edits with validation gates.

**Goal:** Complete all accessibility improvements planned in Phase 1.

**Input:** `DocumentPlan` (from Phase 1) + page images

**Output:** Updated markdowns + `Ledger` (audit trail)

---

## Overview

Execution happens through parallel worker agents:

```
Job[] (sorted by priority)
    ↓
Parallel Workers (max 3 concurrent)
    ↓
Worker Agent → propose_edit() → Validation Gate
    ↓                            ↓
    └─(approved)─→ LedgerEntry → Updated Markdown
    ↓
    └─(rejected)─→ Feedback → Agent Revises
```

**Key Properties:**
- Jobs run in parallel (STRUCTURE before CONTENT)
- All edits validated before applying
- Complete audit trail in ledger
- Agents get feedback for rejected edits

**Location:** `src/agents/v5/worker.py`, `src/agents/v5/validation.py`

---

## Execution Flow

### 1. Job Sorting

**Function:** `execute_jobs_parallel()` in `worker.py:1203-1263`

```python
# Sort: STRUCTURE jobs first, then by page number
sorted_jobs = sorted(jobs, key=lambda j: (j.priority, j.page))

# Result order:
# 1. All JobType.STRUCTURE (priority=1)
#    - Heading fixes
#    - Document hierarchy corrections
# 2. All JobType.CONTENT (priority=2)
#    - Alt-text
#    - Table transcriptions
```

**Why Structure First?**
- Content workers need correct heading hierarchy
- Section context depends on proper structure
- Prevents cascading errors

---

### 2. Parallel Execution

```python
# Limit concurrency to avoid overload
semaphore = asyncio.Semaphore(max_concurrent_jobs)  # default: 3

async def execute_with_limit(job):
    async with semaphore:
        return await execute_job(job, ...)

# Execute all jobs
results = await asyncio.gather(*[execute_with_limit(j) for j in sorted_jobs])
```

**Why Limit Concurrency?**
- LLM rate limiting
- Memory management
- Stable processing

---

### 3. Single Job Execution

**Function:** `execute_job()` in `worker.py:1050-1200`

```python
async def execute_job(
    job: Job,
    page_images: dict[int, Image.Image],
    element_boxes: dict[...],
    page_width: float,
    ledger: Ledger,
    event_bus: EventBus | None
) -> JobResult:
    # 1. Emit JobStartedEvent

    # 2. Create worker dependencies
    deps = WorkerDeps(
        job=job,
        page_image=page_images[job.page],
        current_markdown=job.page_markdown,
        element_boxes=element_boxes,
        page_width=page_width,
        ledger=ledger
    )

    # 3. Build prompt with context

    # 4. Run worker agent
    result = await worker_agent.run(prompt, deps=deps)

    # 5. Apply any validated edits to markdown

    # 6. Emit JobCompletedEvent or JobFailedEvent

    # 7. Return JobResult
```

---

## Worker Agent

### Configuration

**Model:** Haiku 4.5 on AWS Bedrock (`MODEL_TIER_MAP[ModelTier.EFFICIENT]`)

**Tools Available:**
- `view_page()` - See full page image + markdown
- `view_figure(figure_index)` - See specific figure
- `view_table(table_index)` - See specific table
- `read_section(start_line, end_line)` - Read markdown lines
- `find_text(search_pattern)` - Find exact text (CRITICAL)
- `get_table_markdown(table_index)` - Get exact table markdown
- `propose_edit(...)` - Submit edit for validation

### System Prompt

**Location:** `worker.py:171-296`

**Key Instructions:**

```
You are a document accessibility worker. Your job is to complete assigned tasks.

## Available Tools

### find_text(search_pattern)
**IMPORTANT**: Use this to find the EXACT text you need to replace.
Returns the exact_match field which you MUST copy as your 'before' field.

### propose_edit(target, before, after, reasoning)
Propose an edit. Goes through validation:
- If approved: edit is applied
- If rejected: you get feedback to revise

## CRITICAL: The 'before' Field MUST Be Exact

**The #1 cause of edit rejection is incorrect 'before' text.**

Your 'before' field MUST be an EXACT substring in the current markdown.
- DO NOT guess or reconstruct text
- DO NOT paraphrase or modify whitespace
- DO use find_text() to get exact placeholder
- DO copy the exact_match value

### Workflow for Any Edit:
1. Use find_text() to locate placeholder/text
2. Copy EXACT 'exact_match' value
3. Use that as your 'before' field
4. Write replacement as 'after' field

## Task Types

### ALT_TEXT
1. find_text("<!-- image") or find_text("![](")
2. View figure with view_figure()
3. Determine if decorative or informative
4. Write concise alt-text (max 125 chars)
5. Propose: ![alt text](image.png)

### TABLE_TRANSCRIPTION
1. get_table_markdown(N) for EXACT current markdown
2. View table with view_table(N)
3. Transcribe as markdown table (pipes, headers)
4. Propose with exact before text

### HEADING_FIX
1. find_text() to locate heading
2. Copy EXACT heading line
3. Change only # prefix (not text)
4. Example: "## Intro" → "### Intro"

### OCR_FIX / SPELLING_FIX
1. find_text() with misspelled word
2. Copy exact_match
3. Propose correction
```

### Prompt Construction

Each worker receives:

```
Document: {document_title} ({document_type})
Page: {page_num}
Section: {section_context}

Document Outline:
{relevant_outline_entries}

Dictionary Terms:
{dictionary}

Tasks to Complete:
1. [ALT_TEXT] fig:1 - {context}
2. [TABLE_TRANSCRIPTION] table:1 - {context}
...

Current Page Markdown:
---
{page_markdown}
---

Complete all tasks using the tools available.
```

---

## Tool Implementations

### view_page()

**Returns:**
```python
ViewResult(
    success=True,
    description="Page N image shown. Use markdown_content for exact text.",
    markdown_content=current_markdown  # EXACT text
)
```

**Purpose:** See page image + get exact markdown for copying text.

---

### view_figure(figure_index)

**Implementation:** `view_figure_tool()` in `worker.py`

```python
async def view_figure_tool(ctx: RunContext[WorkerDeps], figure_index: int):
    # Crop figure from page image using bounding box
    bbox = element_boxes.get((page, "image", figure_index))
    if bbox:
        figure_image = page_image.crop(bbox)
        # Return cropped image (shown to agent)
        return ViewResult(
            success=True,
            description=f"Figure {figure_index} shown above"
        )
```

**Purpose:** Agent sees the visual content before writing alt-text.

---

### view_table(table_index)

**Implementation:** Similar to view_figure

**Purpose:** Agent sees table structure before transcribing.

---

### find_text(search_pattern)

**Implementation:** `find_text_tool()` in `worker.py`

```python
async def find_text_tool(ctx: RunContext[WorkerDeps], search_pattern: str):
    markdown = ctx.deps.current_markdown

    # Find first occurrence
    index = markdown.find(search_pattern)

    if index >= 0:
        # Extract line containing match
        lines = markdown.split('\n')
        line_num = markdown[:index].count('\n') + 1
        exact_line = lines[line_num - 1]

        return FindTextResult(
            found=True,
            line_number=line_num,
            exact_match=exact_line,  # CRITICAL: agent copies this
            context_before="...",
            context_after="..."
        )
    else:
        return FindTextResult(
            found=False,
            message=f"Pattern '{search_pattern}' not found"
        )
```

**Purpose:** Agent gets EXACT text to use as `before` field.

---

### get_table_markdown(table_index)

**Implementation:** `get_table_markdown_tool()` in `worker.py`

```python
async def get_table_markdown_tool(ctx: RunContext[WorkerDeps], table_index: int):
    markdown = ctx.deps.current_markdown
    lines = markdown.split('\n')

    # Detect table format:
    # 1. Comment: <!-- table N -->
    # 2. Markdown table: |...|...|
    # 3. Text fragment: "Table N: ..."

    # Find table boundaries
    start_line, end_line = detect_table_boundaries(lines, table_index)

    # Extract exact markdown
    table_markdown = '\n'.join(lines[start_line-1:end_line])

    return GetTableResult(
        success=True,
        markdown=table_markdown,  # EXACT text for 'before'
        start_line=start_line,
        end_line=end_line,
        format_type=format_type
    )
```

**Purpose:** Get exact table text for robust replacement.

**Why Important?**
- Tables can span multiple lines
- May have various formats (comments, markdown, text)
- Agent needs exact boundaries

---

### propose_edit()

**Implementation:** `propose_edit_tool()` in `worker.py`

```python
async def propose_edit_tool(
    ctx: RunContext[WorkerDeps],
    target: str,
    before: str,
    after: str,
    reasoning: str,
    replace_line_start: int | None = None,
    replace_line_end: int | None = None
):
    # 1. Create EditProposal
    proposal = EditProposal(
        target=target,
        task_type=determine_task_type(target),
        before=before,
        after=after,
        reasoning=reasoning
    )

    # 2. Validate edit
    validation = validate_edit(
        proposal=proposal,
        current_markdown=ctx.deps.current_markdown,
        dictionary=ctx.deps.job.context.dictionary
    )

    # 3. Emit EditProposedEvent
    event_bus.emit(EditProposedEvent(...))

    # 4. Emit EditValidatedEvent
    event_bus.emit(EditValidatedEvent(
        approved=validation.approved,
        feedback=validation.feedback
    ))

    # 5. If approved, apply edit
    if validation.approved:
        # Create ledger entry
        entry = LedgerEntry(
            job_id=ctx.deps.job.job_id,
            page=ctx.deps.job.page,
            action=proposal.task_type,
            target=target,
            before=before,
            after=after,
            reasoning=reasoning,
            confidence=0.9,
            validated=True,
            validation_feedback=None
        )

        # Append to ledger
        ctx.deps.ledger.append(entry)

        # Apply edit to markdown
        if replace_line_start and replace_line_end:
            # Multi-line replacement (for tables)
            ctx.deps.current_markdown = replace_lines(
                ctx.deps.current_markdown,
                replace_line_start,
                replace_line_end,
                after
            )
        else:
            # Simple substring replacement
            ctx.deps.current_markdown = ctx.deps.current_markdown.replace(
                before, after, 1
            )

        # Emit EditCommittedEvent
        event_bus.emit(EditCommittedEvent(
            entry_id=entry.entry_id,
            target=target,
            action=proposal.task_type
        ))

        return ProposeResult(
            approved=True,
            message="Edit applied successfully"
        )

    else:
        # Return feedback to agent
        return ProposeResult(
            approved=False,
            message=validation.feedback
        )
```

**Key Features:**
- Validation gate before application
- Ledger entry for audit trail
- Real-time event streaming
- Feedback loop for rejected edits

---

## Validation Gate

**Location:** `src/agents/v5/validation.py`

**Function:** `validate_edit()`

### Validation Layers

#### 1. Consistency Check

```python
def _check_consistency(proposal, markdown):
    issues = []

    # Check: before text exists
    if proposal.before not in markdown:
        issues.append(f"'before' text not found in markdown")

    # Check: before != after
    if proposal.before == proposal.after:
        issues.append("'before' and 'after' are identical")

    # Check: edit size reasonable (< 10KB)
    if len(proposal.after) > 10000:
        issues.append("Edit too large (> 10KB)")

    # Check: format valid (for specific task types)
    if proposal.task_type == TaskType.ALT_TEXT:
        if not re.match(r'!\[.*?\]\(.+?\)', proposal.after):
            issues.append("Invalid markdown image syntax")

    if proposal.task_type == TaskType.TABLE_TRANSCRIPTION:
        if '|' not in proposal.after:
            issues.append("Table must have pipe separators")

    return issues
```

#### 2. Markdown Linting

```python
def _check_markdown(proposal):
    issues = []
    text = proposal.after

    # Check: heading syntax
    invalid_headings = re.findall(r'^#{7,}', text, re.MULTILINE)
    if invalid_headings:
        issues.append("Invalid heading level (max 6 #)")

    # Check: unclosed brackets
    if text.count('[') != text.count(']'):
        issues.append("Unclosed square brackets")
    if text.count('(') != text.count(')'):
        issues.append("Unclosed parentheses")

    # Check: table completeness
    if '|' in text:
        lines = text.split('\n')
        col_counts = [line.count('|') for line in lines if '|' in line]
        if len(set(col_counts)) > 1:
            issues.append("Inconsistent table column count")

    # Check: excessive blank lines
    if '\n\n\n\n' in text:
        issues.append("Excessive blank lines (> 3)")

    return issues
```

#### 3. Spell Check

```python
def _check_spelling(proposal, dictionary):
    issues = []
    text = proposal.after

    # Extract words (alphanumeric only)
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text)

    spell = SpellChecker()
    spell.word_frequency.load_words(dictionary)  # Add document terms

    for word in words:
        if word.lower() not in spell:
            misspelled = word
            suggestion = spell.correction(word)
            issues.append(SpellIssue(
                word=misspelled,
                suggestion=suggestion,
                in_dictionary=False
            ))

    # Limit to first 5 issues (avoid overwhelming)
    return issues[:5]
```

### Validation Decision

```python
def validate_edit(proposal, current_markdown, dictionary):
    spell_issues = _check_spelling(proposal, dictionary)
    lint_issues = _check_markdown(proposal)
    consistency_issues = _check_consistency(proposal, current_markdown)

    # Critical failures (reject immediately)
    if consistency_issues:
        return ValidationResult(
            approved=False,
            edit=proposal,
            spell_issues=[],
            lint_issues=[],
            consistency_issues=consistency_issues,
            feedback=f"Edit rejected: {'; '.join(consistency_issues)}"
        )

    # Lint issues (reject)
    if lint_issues:
        return ValidationResult(
            approved=False,
            edit=proposal,
            spell_issues=[],
            lint_issues=lint_issues,
            consistency_issues=[],
            feedback=f"Markdown errors: {'; '.join(lint_issues)}"
        )

    # Spelling issues (warn but approve)
    # Note: Spelling is advisory, not blocking
    return ValidationResult(
        approved=True,
        edit=proposal,
        spell_issues=spell_issues,
        lint_issues=[],
        consistency_issues=[],
        feedback=None
    )
```

**Key Decision:** Spelling issues don't block edits (too many false positives).

---

## Agent Iteration Loop

If an edit is rejected, agent receives feedback and can try again:

```python
# Agent conversation:
Agent: propose_edit(target="fig:1", before="<!-- image 1 -->", ...)
Tool: { "approved": false, "message": "'before' text not found" }

Agent: Let me use find_text() first...
Agent: find_text("<!-- image")
Tool: { "found": true, "exact_match": "<!-- image 1 -->" }

Agent: Now I have the exact text!
Agent: propose_edit(target="fig:1", before="<!-- image 1 -->", ...)
Tool: { "approved": true, "message": "Edit applied successfully" }
```

**Max Iterations:** Agent can retry until it succeeds or gives up.

**Typical Flow:**
- 90% of edits approved on first try
- 8% approved on second try (after feedback)
- 2% fail (agent gives up or runs out of tokens)

---

## Typical Job Examples

### Example 1: Alt-Text Job

**Input:**
```python
Job(
    job_type=JobType.CONTENT,
    page=3,
    tasks=[
        Task(
            task_type=TaskType.ALT_TEXT,
            target="fig:1",
            context="Bar chart showing enrollment trends"
        )
    ],
    page_markdown="...\n<!-- image 1 -->\n..."
)
```

**Agent Actions:**
1. `find_text("<!-- image")` → Get exact placeholder
2. `view_figure(1)` → See the figure
3. Analyze: "Bar chart with 5 bars showing years 2020-2024"
4. `propose_edit(target="fig:1", before="<!-- image 1 -->", after="![Bar chart showing enrollment by year from 2020-2024](image1.png)", reasoning="...")`

**Result:**
```python
LedgerEntry(
    action=TaskType.ALT_TEXT,
    target="fig:1",
    before="<!-- image 1 -->",
    after="![Bar chart showing enrollment by year from 2020-2024](image1.png)",
    reasoning="Figure shows a bar chart with enrollment data over 5 years",
    confidence=0.95,
    validated=True
)
```

---

### Example 2: Table Transcription Job

**Input:**
```python
Task(
    task_type=TaskType.TABLE_TRANSCRIPTION,
    target="table:1",
    context="Course grades table"
)
```

**Agent Actions:**
1. `get_table_markdown(1)` → Get exact table markdown
2. `view_table(1)` → See table structure
3. Transcribe: Read cells, format as markdown table
4. `propose_edit(before=<exact table markdown>, after=<transcribed table>, replace_line_start=..., replace_line_end=...)`

**Result:**
```python
LedgerEntry(
    action=TaskType.TABLE_TRANSCRIPTION,
    target="table:1",
    before="<!-- table 1 -->",
    after="| Course | Credits | Grade |\n|--------|---------|-------|\n| CS 101 | 3 | A |\n| MATH 220 | 4 | B+ |",
    ...
)
```

---

## Metrics

**10-page Document (25 jobs):**

| Metric | Value |
|--------|-------|
| Jobs total | 25 |
| STRUCTURE jobs | 5 |
| CONTENT jobs | 20 |
| Concurrent workers | 3 |
| Execution duration | 45-75s |
| Edits proposed | 50-60 |
| Edits approved | 47-55 (95%) |
| Edits rejected | 3-5 (5%) |
| Tokens (input) | 50-100K |
| Tokens (output) | 20-40K |
| Cost | $0.03-0.06 |

**Per Job:**
- Duration: 2-5s
- Edits proposed: 2-3
- Tool calls: 5-10

---

## Error Handling

### Agent Failures

```python
try:
    result = await worker_agent.run(prompt, deps=deps)
except Exception as e:
    logger.error(f"Job {job.job_id} failed: {e}")
    return JobResult(
        job_id=job.job_id,
        success=False,
        error=str(e),
        ...
    )
```

### Validation Rejection Loop

If agent keeps proposing invalid edits:
- Max 10 iterations
- After 10 rejections, mark job as failed

### Partial Success

If some tasks complete but others fail:
- Apply successful edits
- Mark job as partial success
- Include error details in JobResult

---

## Debugging

### Enable Agent Thinking Events

```python
# In worker.py
emit_thinking_events = True  # Default: False

# Emits AgentThinkingEvent for each agent message
# Shows reasoning, tool calls, responses
```

### Inspect Ledger

```python
# After execution
for entry in ledger.entries:
    if not entry.validated:
        print(f"Rejected: {entry.target} - {entry.validation_feedback}")
```

### Review Failed Jobs

```python
failed_jobs = [r for r in results if not r.success]
for job_result in failed_jobs:
    print(f"Job {job_result.job_id}: {job_result.error}")
```

---

## Common Issues

### Issue: High Rejection Rate

**Symptom:** > 20% of edits rejected

**Causes:**
- Agent not using `find_text()` consistently
- System prompt unclear
- Validation rules too strict

**Fix:** Review system prompt, add examples

---

### Issue: Agent Infinite Loop

**Symptom:** Job never completes, keeps retrying same edit

**Cause:** Agent not understanding feedback

**Fix:** Improve feedback clarity, add iteration limit

---

### Issue: Tables Malformed

**Symptom:** Table transcription creates invalid markdown

**Cause:** Agent not counting columns correctly

**Fix:** Add column count to validation, provide clearer examples

---

## Next Steps

- Review [Phase 3: Verification](./pipeline-phase-3-verification.md) for quality checks
- Check [Data Models Reference](./pipeline-data-models.md) for `LedgerEntry` schema
- Explore [Phase 4: Recovery](./pipeline-phase-4-recovery.md) for error recovery
