# PRD-003: ParagraphAgent Core Implementation

## Overview

| Field | Value |
|-------|-------|
| **PRD Number** | 003 |
| **Title** | ParagraphAgent Core - Parent Agent with Subagent Tools |
| **Effort** | 2-3 days |
| **Priority** | High |
| **Dependencies** | PRD-001 (Foundation), PRD-002 (Subagents) |
| **Blocks** | PRD-004 (Detection), PRD-005 (Integration) |

---

## Problem Statement

We need a parent agent (ParagraphAgent) that orchestrates the subagent tools created in PRD-002. The parent agent:

1. Receives per-page jobs with paragraph-related tasks
2. Uses view/analysis tools to understand context
3. Calls subagent tools for specialized recommendations
4. Reviews subagent confidence and decides whether to apply
5. Uses `propose_edit()` to apply changes (with validation)
6. Flags low-confidence edits with `needs_review=True`

This follows the same pattern as the existing Worker agent but delegates complex decisions to specialized subagents.

---

## Success Criteria

1. ParagraphAgent handles all 5 per-page paragraph task types
2. Agent uses subagent tools (not direct generation like Worker)
3. Confidence thresholds determine auto-apply vs needs_review
4. All edits go through `propose_edit()` validation gate
5. Agent can view page images and read context
6. Integration test: agent processes a page with mixed paragraph issues

---

## Technical Requirements

### 1. ParagraphAgent System Prompt

**File:** `src/agents/paragraph_agent.py`

```python
PARAGRAPH_AGENT_SYSTEM_PROMPT = """You are a document text flow specialist.

Your job is to fix text structure issues: page breaks, footnotes, citations, lists, and typography.

## Your Domain

You handle:
- Page break artifacts (---, split words like de-precate)
- Footnote placement and linking
- Citation references to bibliography
- List structure (nesting, numbering, bullets)
- Typography semantics (bold/italic that conveys meaning)

You do NOT handle:
- Images/figures (handled by Worker)
- Tables (handled by Worker)
- Heading levels (handled in planning)
- Cross-page paragraph merges (handled in separate pass)

## Available Tools

### View Tools
- view_page(): See page image and current markdown

### Analysis Tools
- find_text(pattern): Find exact text in markdown
- read_context(start_line, end_line): Read specific markdown lines

### Subagent Tools

These call specialized LLM subagents that return recommendations with confidence scores.
YOU decide whether to apply their recommendations.

- remove_page_artifacts(text_region): Clean up ---, split words
  → Returns: {cleaned_text, artifacts_removed, words_rejoined, confidence, reasoning}

- correct_footnote(): Fix footnote placement and linking
  → Returns: {corrected_markdown, footnotes_fixed, confidence, reasoning}

- fix_citation_links(): Link citations to bibliography
  → Returns: {corrected_markdown, citations_linked, bibliography_found, confidence, reasoning}

- fix_list_semantics(list_markdown): Fix list structure
  → Returns: {corrected_markdown, issues_fixed, confidence, reasoning}

- fix_typography(text_region): Add semantic bold/italic/code
  → Returns: {corrected_markdown, formatting_added, confidence, reasoning}

### Edit Tool
- propose_edit(before, after, reasoning, needs_review): Submit your edit for validation

## Workflow

1. View the page to understand context
2. For each task:
   a. Read the relevant text region
   b. Call the appropriate subagent tool
   c. Review the subagent's recommendation
   d. Based on confidence:
      - If confidence >= 0.8: propose_edit(needs_review=False)
      - If confidence 0.5-0.8: propose_edit(needs_review=True)
      - If confidence < 0.5: skip the edit, note in your output
   e. If you disagree with subagent, use your judgment

## Important Rules

1. ALWAYS view the page image before making decisions
2. Subagent recommendations are SUGGESTIONS - you have final judgment
3. When in doubt about author intent, preserve original
4. Page artifacts (---) are almost always extraction errors
5. Low confidence = flag for human review, don't skip entirely

## Output

List which tasks you completed:
- Applied (auto): edits applied with high confidence
- Applied (review): edits applied but flagged for review
- Skipped: edits skipped due to very low confidence
- Failed: tasks that encountered errors
"""
```

### 2. Agent Dependencies

```python
@dataclass
class ParagraphAgentDeps:
    """Dependencies for ParagraphAgent tools."""
    
    job: Job
    page_image: Image.Image
    current_markdown: str
    
    # For edits
    pending_edits: list[EditProposal] = field(default_factory=list)
    validated_edits: list[LedgerEntry] = field(default_factory=list)
    
    # For citations (need full document)
    full_document_markdown: str | None = None
    
    # Context
    dictionary: list[str] = field(default_factory=list)
    event_bus: EventBus | None = None
```

### 3. Tool Implementations

#### 3.1 View Tool

```python
async def view_page_tool(ctx: RunContext[ParagraphAgentDeps]) -> ViewResult:
    """View page image and current markdown."""
    deps = ctx.deps
    return ViewResult(
        success=True,
        description=f"Page {deps.job.page} image is shown above.",
        markdown_content=deps.current_markdown,
    )
```

#### 3.2 Analysis Tools

```python
async def find_text_tool(
    ctx: RunContext[ParagraphAgentDeps],
    pattern: str,
) -> FindTextResult:
    """Find text in markdown."""
    # Same implementation as Worker's find_text_tool
    ...

async def read_context_tool(
    ctx: RunContext[ParagraphAgentDeps],
    start_line: int,
    end_line: int,
) -> ReadResult:
    """Read specific lines of markdown."""
    # Same implementation as Worker's read_section_tool
    ...
```

#### 3.3 Subagent Tools

```python
async def remove_page_artifacts_tool(
    ctx: RunContext[ParagraphAgentDeps],
    text_region: str,
) -> PageArtifactResult:
    """Clean up page break artifacts and split words.
    
    Invokes a specialized subagent that analyzes the text and page image
    to identify and remove extraction artifacts.
    
    Args:
        text_region: The markdown text that may contain artifacts
        
    Returns:
        PageArtifactResult with cleaned text and confidence score
    """
    from .subagents.page_artifacts import invoke_page_artifact_subagent
    
    result = await invoke_page_artifact_subagent(
        text_region=text_region,
        page_image=ctx.deps.page_image,
    )
    
    return result


async def correct_footnote_tool(
    ctx: RunContext[ParagraphAgentDeps],
) -> FootnoteResult:
    """Fix footnote placement and linking.
    
    Invokes a specialized subagent that finds footnote markers,
    locates definitions, and creates proper markdown linking.
    
    Returns:
        FootnoteResult with corrected markdown and confidence score
    """
    from .subagents.footnotes import invoke_footnote_subagent
    
    result = await invoke_footnote_subagent(
        page_markdown=ctx.deps.current_markdown,
        page_image=ctx.deps.page_image,
    )
    
    return result


async def fix_citation_links_tool(
    ctx: RunContext[ParagraphAgentDeps],
) -> CitationResult:
    """Link citations to bibliography entries.
    
    Invokes a specialized subagent that finds citation markers
    and matches them to references. Uses full document context
    to locate the bibliography section.
    
    Returns:
        CitationResult with linked citations and confidence score
    """
    from .subagents.citations import invoke_citation_subagent
    
    result = await invoke_citation_subagent(
        page_markdown=ctx.deps.current_markdown,
        full_document=ctx.deps.full_document_markdown,
        page_image=ctx.deps.page_image,
    )
    
    return result


async def fix_list_semantics_tool(
    ctx: RunContext[ParagraphAgentDeps],
    list_markdown: str,
) -> ListResult:
    """Fix list structure (nesting, numbering, bullets).
    
    Invokes a specialized subagent that compares the visual
    list layout to the markdown structure.
    
    Args:
        list_markdown: The list section to analyze
        
    Returns:
        ListResult with corrected structure and confidence score
    """
    from .subagents.lists import invoke_list_subagent
    
    result = await invoke_list_subagent(
        list_markdown=list_markdown,
        page_image=ctx.deps.page_image,
    )
    
    return result


async def fix_typography_tool(
    ctx: RunContext[ParagraphAgentDeps],
    text_region: str,
) -> TypographyResult:
    """Add semantic typography markup (bold, italic, code).
    
    Invokes a specialized subagent that compares visual
    formatting to markdown and identifies semantic formatting.
    
    Args:
        text_region: The text to analyze for formatting
        
    Returns:
        TypographyResult with formatted text and confidence score
    """
    from .subagents.typography import invoke_typography_subagent
    
    result = await invoke_typography_subagent(
        text_region=text_region,
        page_image=ctx.deps.page_image,
    )
    
    return result
```

#### 3.4 Propose Edit Tool

```python
async def propose_edit_tool(
    ctx: RunContext[ParagraphAgentDeps],
    before: str,
    after: str,
    reasoning: str,
    needs_review: bool = False,
    task_type: str = "paragraph_fix",
) -> ProposeEditResult:
    """Propose an edit to the markdown.
    
    The edit goes through validation. If approved, it's applied and
    recorded in the ledger.
    
    Args:
        before: Exact text to replace (must exist in markdown)
        after: New text to replace it with
        reasoning: Why this edit is needed
        needs_review: If True, flags edit for human review
        task_type: Type of edit for ledger
        
    Returns:
        ProposeEditResult with acceptance status
    """
    deps = ctx.deps
    
    # Parse task type
    try:
        task_type_enum = TaskType(task_type)
    except ValueError:
        task_type_enum = TaskType.FORMAT_FIX
    
    proposal = EditProposal(
        target=f"paragraph:{deps.job.page}",
        task_type=task_type_enum,
        before=before,
        after=after,
        reasoning=reasoning,
    )
    
    # Validate
    result = validate_edit(
        proposal=proposal,
        dictionary=deps.dictionary,
        current_markdown=deps.current_markdown,
    )
    
    if not result.approved:
        return ProposeEditResult(
            accepted=False,
            feedback=result.feedback,
        )
    
    # Apply the edit
    if before in deps.current_markdown:
        deps.current_markdown = deps.current_markdown.replace(before, after, 1)
        
        # Create ledger entry with needs_review flag
        entry = LedgerEntry(
            job_id=deps.job.job_id,
            page=deps.job.page,
            action=task_type_enum,
            target=f"paragraph:{deps.job.page}",
            before=before,
            after=after,
            reasoning=reasoning,
            confidence=0.9 if not needs_review else 0.6,
            validated=True,
            needs_review=needs_review,  # NEW: Flag for human review
        )
        deps.validated_edits.append(entry)
        
        return ProposeEditResult(accepted=True, applied=True)
    
    return ProposeEditResult(
        accepted=True,
        feedback="Text not found in markdown",
        applied=False,
    )
```

### 4. Agent Definition

```python
class ParagraphAgentOutput(BaseModel):
    """Output from ParagraphAgent."""
    
    tasks_applied_auto: list[str] = Field(default_factory=list)
    tasks_applied_review: list[str] = Field(default_factory=list)
    tasks_skipped: list[str] = Field(default_factory=list)
    tasks_failed: list[str] = Field(default_factory=list)
    summary: str = Field(default="")


_paragraph_agent: Agent[ParagraphAgentDeps, ParagraphAgentOutput] | None = None


def _get_paragraph_agent() -> Agent[ParagraphAgentDeps, ParagraphAgentOutput]:
    """Get or create ParagraphAgent."""
    global _paragraph_agent
    
    if _paragraph_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        
        _paragraph_agent = Agent(
            model=model,
            deps_type=ParagraphAgentDeps,
            output_type=ParagraphAgentOutput,
            system_prompt=PARAGRAPH_AGENT_SYSTEM_PROMPT,
        )
        
        # Register tools
        _paragraph_agent.tool(view_page_tool)
        _paragraph_agent.tool(find_text_tool)
        _paragraph_agent.tool(read_context_tool)
        _paragraph_agent.tool(remove_page_artifacts_tool)
        _paragraph_agent.tool(correct_footnote_tool)
        _paragraph_agent.tool(fix_citation_links_tool)
        _paragraph_agent.tool(fix_list_semantics_tool)
        _paragraph_agent.tool(fix_typography_tool)
        _paragraph_agent.tool(propose_edit_tool)
        
        logger.info("ParagraphAgent initialized")
    
    return _paragraph_agent
```

### 5. Job Execution Function

```python
async def execute_with_paragraph_agent(
    job: Job,
    page_image: Image.Image,
    current_markdown: str,
    full_document_markdown: str,
    ledger: Ledger,
    event_bus: EventBus | None = None,
) -> JobResult:
    """Execute a paragraph job using ParagraphAgent.
    
    Args:
        job: The paragraph job to execute
        page_image: Image of the page
        current_markdown: Current markdown for this page
        full_document_markdown: Full document (for citations)
        ledger: Ledger to append entries
        event_bus: Optional event bus
        
    Returns:
        JobResult with updated markdown
    """
    start_time = time.time()
    
    # Create dependencies
    deps = ParagraphAgentDeps(
        job=job,
        page_image=page_image,
        current_markdown=current_markdown,
        full_document_markdown=full_document_markdown,
        event_bus=event_bus,
    )
    
    # Build task prompt
    task_descriptions = "\n".join(
        f"- {t.task_type.value}: {t.context}"
        for t in job.tasks
    )
    
    prompt = f"""Process these paragraph tasks for page {job.page}:

{task_descriptions}

View the page, call the appropriate subagent tools, and apply edits based on confidence.
"""
    
    # Prepare message with page image
    buffer = BytesIO()
    page_image.save(buffer, format="PNG")
    image_content = BinaryContent(data=buffer.getvalue(), media_type="image/png")
    
    agent = _get_paragraph_agent()
    
    try:
        result = await agent.run(
            [f"Page {job.page} image:", image_content, prompt],
            deps=deps,
        )
        
        output = result.output
        usage = result.usage()
        
        # Add ledger entries
        for entry in deps.validated_edits:
            ledger.append(entry)
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        return JobResult(
            job_id=job.job_id,
            success=len(output.tasks_failed) == 0,
            updated_markdown=deps.current_markdown,
            ledger_entries=deps.validated_edits,
            tasks_completed=len(output.tasks_applied_auto) + len(output.tasks_applied_review),
            tasks_failed=len(output.tasks_failed),
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            duration_ms=duration_ms,
        )
        
    except Exception as e:
        logger.error(f"ParagraphAgent failed: {e}")
        return JobResult(
            job_id=job.job_id,
            success=False,
            updated_markdown=current_markdown,
            ledger_entries=[],
            tasks_completed=0,
            tasks_failed=len(job.tasks),
            input_tokens=0,
            output_tokens=0,
            duration_ms=int((time.time() - start_time) * 1000),
            error=str(e),
        )
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                      PARAGRAPH AGENT FLOW                            │
│                                                                       │
│  Job (PARAGRAPH type)                                                 │
│  ├── page: 3                                                         │
│  └── tasks: [PAGE_ARTIFACT_REMOVAL, FOOTNOTE_CORRECTION]             │
│                                                                       │
│            ▼                                                         │
│                                                                       │
│  execute_with_paragraph_agent()                                      │
│  ├── Creates ParagraphAgentDeps                                      │
│  ├── Sends page image + task prompt to agent                         │
│  └── Collects results                                                │
│                                                                       │
│            ▼                                                         │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  ParagraphAgent (Claude Haiku)                                   │ │
│  │                                                                   │ │
│  │  1. view_page() → sees image + markdown                          │ │
│  │                                                                   │ │
│  │  2. remove_page_artifacts("text with ---")                       │ │
│  │     └── Subagent returns: {cleaned_text, confidence: 0.92}      │ │
│  │                                                                   │ │
│  │  3. Agent reviews: confidence >= 0.8 ✓                           │ │
│  │     └── propose_edit(before, after, needs_review=False)          │ │
│  │                                                                   │ │
│  │  4. correct_footnote()                                           │ │
│  │     └── Subagent returns: {corrected_md, confidence: 0.65}      │ │
│  │                                                                   │ │
│  │  5. Agent reviews: confidence < 0.8                              │ │
│  │     └── propose_edit(before, after, needs_review=True)           │ │
│  │                                                                   │ │
│  │  6. Returns ParagraphAgentOutput                                 │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│            ▼                                                         │
│                                                                       │
│  JobResult                                                            │
│  ├── updated_markdown: "cleaned text..."                             │
│  ├── ledger_entries: [entry1 (auto), entry2 (needs_review)]         │
│  └── tasks_completed: 2                                              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Deliverables

| File | Action | Description |
|------|--------|-------------|
| `src/agents/paragraph_agent.py` | Create | Full ParagraphAgent implementation |
| `tests/unit/agents/test_paragraph_agent.py` | Create | Unit tests for agent |
| `tests/integration/agents/test_paragraph_agent_integration.py` | Create | Integration tests |

---

## Acceptance Criteria

- [ ] ParagraphAgent system prompt is comprehensive
- [ ] All 5 per-page subagent tools are implemented
- [ ] View and analysis tools work correctly
- [ ] `propose_edit` handles `needs_review` flag
- [ ] Agent respects confidence thresholds
- [ ] `execute_with_paragraph_agent()` returns proper JobResult
- [ ] Ledger entries include `needs_review` flag when appropriate
- [ ] Unit tests pass
- [ ] Integration test with mock subagents passes

---

## Definition of Done

1. ParagraphAgent can process a page with multiple paragraph tasks
2. Subagent tool calls work end-to-end
3. Low-confidence edits are flagged for review
4. All edits go through validation gate
5. Tests demonstrate the confidence-based workflow
6. Code follows existing Worker agent patterns

---

## Implementation Notes

### Confidence Thresholds

```python
from src.agents.subagents import (
    CONFIDENCE_AUTO_APPLY,      # 0.8
    CONFIDENCE_APPLY_WITH_REVIEW,  # 0.5
)

# In agent's decision logic:
if result.confidence >= CONFIDENCE_AUTO_APPLY:
    propose_edit(..., needs_review=False)
elif result.confidence >= CONFIDENCE_APPLY_WITH_REVIEW:
    propose_edit(..., needs_review=True)
else:
    # Skip, log to tasks_skipped
```

### Full Document Context

For citation linking, the agent needs full document context:

```python
# In orchestrator, when creating job:
full_doc = "\n\n".join(page_markdowns[p] for p in sorted(page_markdowns.keys()))

# Pass to execute_with_paragraph_agent
await execute_with_paragraph_agent(
    job=job,
    ...,
    full_document_markdown=full_doc,
)
```

### Error Handling

If a subagent fails, the parent agent should:
1. Log the error
2. Add to `tasks_failed` output
3. Continue with remaining tasks
4. Not crash the entire job

### Code Comment Standards

- **DO NOT include PRD numbers in code comments** - Comments like "PRD-001" or "(PRD-003)" should never appear in source code
- Comments should describe *what* and *why*, not *when* or *which PRD*
