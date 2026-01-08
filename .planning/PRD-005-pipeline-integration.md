# PRD-005: Pipeline Integration & Cross-Page Merge Pass

## Overview

| Field | Value |
|-------|-------|
| **PRD Number** | 005 |
| **Title** | Pipeline Integration - Job Routing, Paragraph Jobs, and Cross-Page Merge Pass |
| **Effort** | 2-3 days |
| **Priority** | High |
| **Dependencies** | PRD-001, PRD-002, PRD-003, PRD-004 |
| **Blocks** | None (final PRD) |

---

## Problem Statement

With all components built, we need to integrate ParagraphAgent into the main pipeline:

1. Generate `PARAGRAPH` jobs from detected issues
2. Route `PARAGRAPH` jobs to ParagraphAgent (not Worker)
3. Add a cross-page merge pass after all per-page jobs complete
4. Ensure ledger entries with `needs_review` are properly surfaced

---

## Success Criteria

1. `PARAGRAPH` jobs are created from detected paragraph issues
2. Orchestrator routes `PARAGRAPH` jobs to ParagraphAgent
3. Cross-page merge pass runs after per-page jobs
4. `needs_review` entries appear in ledger response
5. Full pipeline works end-to-end with paragraph fixes
6. Existing Worker pipeline unchanged (figures, tables, headings)

---

## Technical Requirements

### 1. Job Generation for Paragraph Tasks

**File:** `src/agents/planner.py`

Extend `stage4_generate_jobs()` to create `PARAGRAPH` jobs:

```python
def stage4_generate_jobs(
    page_markdowns: dict[int, str],
    page_plans: dict[int, PagePlan],
    structure: DocumentStructure,
    event_bus: EventBus | None = None,
) -> list[Job]:
    """Generate worker jobs from page plans."""
    jobs: list[Job] = []
    
    # === EXISTING: Structure jobs (heading fixes) ===
    # [unchanged code]
    
    # === EXISTING: Content jobs (figures, tables) ===
    # [unchanged code]
    
    # === NEW: Paragraph jobs ===
    for page_num, plan in sorted(page_plans.items()):
        paragraph_tasks: list[Task] = []
        
        # Page artifact tasks
        for artifact in plan.page_artifacts:
            paragraph_tasks.append(Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target=f"artifact:{page_num}:{artifact.line_number}",
                context=f"{artifact.artifact_type}: {artifact.text[:50]}",
                priority=1,
            ))
        
        # Footnote tasks
        for fn in plan.footnote_issues:
            paragraph_tasks.append(Task(
                task_type=TaskType.FOOTNOTE_CORRECTION,
                target=f"footnote:{fn.marker}",
                context=fn.issue_type,
                priority=1,
            ))
        
        # Citation tasks
        for cite in plan.citation_issues:
            paragraph_tasks.append(Task(
                task_type=TaskType.CITATION_LINKING,
                target=f"citation:{cite.marker}",
                context=cite.issue_type,
                priority=2,
            ))
        
        # List tasks
        for lst in plan.list_issues:
            paragraph_tasks.append(Task(
                task_type=TaskType.LIST_FIX,
                target=f"list:{lst.location}",
                context=f"{lst.issue_type}: {lst.description}",
                priority=2,
            ))
        
        # Typography tasks
        for typo in plan.typography_issues:
            paragraph_tasks.append(Task(
                task_type=TaskType.TYPOGRAPHY_FIX,
                target=f"typography:{typo.text[:20]}",
                context=f"{typo.formatting_type}: {typo.semantic_purpose}",
                priority=3,
            ))
        
        # Create job if there are tasks
        if paragraph_tasks:
            context = JobContext(
                document_title=structure.title,
                document_type=structure.document_type,
                section_context=plan.section_context,
                dictionary=structure.key_terms + plan.keywords,
                relevant_outline=_get_relevant_outline(page_num, structure.outline),
            )
            
            job = Job(
                job_type=JobType.PARAGRAPH,
                priority=page_num + 100,  # Run after content jobs
                page=page_num,
                tasks=paragraph_tasks,
                context=context,
                page_markdown=page_markdowns.get(page_num, ""),
            )
            jobs.append(job)
            
            if event_bus:
                event_bus.emit(
                    JobCreatedEvent(
                        document_id=event_bus.document_id,
                        job_id=job.job_id,
                        job_type=job.job_type.value,
                        page=job.page,
                        task_count=len(job.tasks),
                        task_types=[t.task_type.value for t in job.tasks],
                    )
                )
    
    return jobs
```

### 2. Job Routing in Orchestrator

**File:** `src/agents/orchestrator.py`

Update job execution to route `PARAGRAPH` jobs:

```python
from .paragraph_agent import execute_with_paragraph_agent

async def execute_jobs_parallel(
    jobs: list[Job],
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict,
    page_width: float,
    ledger: Ledger,
    max_concurrent: int = 3,
    event_bus: EventBus | None = None,
) -> list[JobResult]:
    """Execute jobs using appropriate agents."""
    
    # Full document markdown for citation linking
    full_document_markdown = "\n\n".join(
        page_markdowns[p] for p in sorted(page_markdowns.keys())
    )
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def execute_single_job(job: Job) -> JobResult:
        async with semaphore:
            if job.job_type == JobType.PARAGRAPH:
                # NEW: Route to ParagraphAgent
                return await execute_with_paragraph_agent(
                    job=job,
                    page_image=page_images.get(job.page),
                    current_markdown=page_markdowns.get(job.page, ""),
                    full_document_markdown=full_document_markdown,
                    ledger=ledger,
                    event_bus=event_bus,
                )
            else:
                # Existing: Route to Worker
                return await execute_job(
                    job=job,
                    page_image=page_images.get(job.page),
                    element_bboxes=element_bboxes,
                    page_width=page_width,
                    ledger=ledger,
                    event_bus=event_bus,
                )
    
    # Execute all jobs
    tasks = [execute_single_job(job) for job in jobs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Update page_markdowns with results
    job_results = []
    for job, result in zip(jobs, results):
        if isinstance(result, Exception):
            logger.error(f"Job {job.job_id} failed: {result}")
            job_results.append(JobResult(
                job_id=job.job_id,
                success=False,
                updated_markdown=page_markdowns.get(job.page, ""),
                ledger_entries=[],
                tasks_completed=0,
                tasks_failed=len(job.tasks),
                input_tokens=0,
                output_tokens=0,
                duration_ms=0,
                error=str(result),
            ))
        else:
            page_markdowns[job.page] = result.updated_markdown
            job_results.append(result)
    
    return job_results
```

### 3. Cross-Page Merge Pass

**File:** `src/agents/orchestrator.py`

Add a new function for the merge pass:

```python
from .subagents.paragraph_merge import invoke_paragraph_merge_subagent

async def merge_cross_page_paragraphs(
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    plan: DocumentPlan,
    ledger: Ledger,
    event_bus: EventBus | None = None,
) -> dict[int, str]:
    """Merge paragraphs that are split across page boundaries.
    
    This runs AFTER all per-page jobs complete, on stable markdown.
    
    Args:
        page_markdowns: Current markdown for each page
        page_images: Images for each page
        plan: DocumentPlan with page continuation flags
        ledger: Ledger for recording changes
        event_bus: Optional event bus
        
    Returns:
        Updated page_markdowns dict
    """
    pages_with_continuation = [
        page_num
        for page_num, page_plan in plan.pages.items()
        if page_plan.has_page_continuation
    ]
    
    if not pages_with_continuation:
        logger.info("No cross-page merges needed")
        return page_markdowns
    
    logger.info(f"Processing {len(pages_with_continuation)} cross-page merges")
    
    for page_num in sorted(pages_with_continuation):
        next_page = page_num + 1
        
        if next_page not in page_markdowns:
            logger.warning(f"Page {next_page} not found for merge with page {page_num}")
            continue
        
        # Get page boundaries
        page1_md = page_markdowns[page_num]
        page2_md = page_markdowns[next_page]
        
        # Extract last ~200 chars of page 1, first ~200 of page 2
        page1_end = page1_md[-300:] if len(page1_md) > 300 else page1_md
        page2_start = page2_md[:300] if len(page2_md) > 300 else page2_md
        
        # Get images for both pages
        page1_image = page_images.get(page_num)
        page2_image = page_images.get(next_page)
        
        if not page1_image or not page2_image:
            logger.warning(f"Missing images for merge between pages {page_num}-{next_page}")
            continue
        
        try:
            # Call merge subagent
            result = await invoke_paragraph_merge_subagent(
                page1_end_text=page1_end,
                page2_start_text=page2_start,
                page1_image=page1_image,
                page2_image=page2_image,
            )
            
            if not result.should_merge:
                logger.info(f"Merge not needed for pages {page_num}-{next_page}: {result.reasoning}")
                continue
            
            if result.confidence < 0.5:
                logger.warning(
                    f"Skipping low-confidence merge for pages {page_num}-{next_page}: "
                    f"confidence={result.confidence}"
                )
                continue
            
            # Apply the merge
            needs_review = result.confidence < 0.8
            
            # Remove chars from end of page 1
            if result.page1_remove_chars > 0:
                old_end = page1_md[-result.page1_remove_chars:]
                page_markdowns[page_num] = page1_md[:-result.page1_remove_chars]
                
                ledger.append(LedgerEntry(
                    job_id=f"merge:{page_num}-{next_page}",
                    page=page_num,
                    action=TaskType.PARAGRAPH_MERGE,
                    target=f"page_end:{page_num}",
                    before=old_end,
                    after="",
                    reasoning=f"Removed for merge: {result.reasoning}",
                    confidence=result.confidence,
                    validated=True,
                    needs_review=needs_review,
                ))
            
            # Replace start of page 2 with merged text
            if result.page2_remove_chars > 0:
                old_start = page2_md[:result.page2_remove_chars]
                new_start = result.merged_text
                page_markdowns[next_page] = new_start + page2_md[result.page2_remove_chars:]
                
                ledger.append(LedgerEntry(
                    job_id=f"merge:{page_num}-{next_page}",
                    page=next_page,
                    action=TaskType.PARAGRAPH_MERGE,
                    target=f"page_start:{next_page}",
                    before=old_start,
                    after=new_start,
                    reasoning=f"Merged paragraph: {result.reasoning}",
                    confidence=result.confidence,
                    validated=True,
                    needs_review=needs_review,
                ))
            
            logger.info(
                f"Merged pages {page_num}-{next_page} "
                f"(method: {result.join_method}, confidence: {result.confidence})"
            )
            
            if event_bus:
                event_bus.emit(
                    EditCommittedEvent(
                        document_id=event_bus.document_id,
                        ledger_entry=ledger[-1],
                        content_preview=result.merged_text[:100],
                    )
                )
                
        except Exception as e:
            logger.error(f"Merge failed for pages {page_num}-{next_page}: {e}")
            continue
    
    return page_markdowns
```

### 4. Update Main Pipeline

**File:** `src/agents/orchestrator.py`

In `process_document_v5()`, add the merge pass:

```python
async def process_document_v5(...) -> ProcessingResult:
    # ... existing code through job execution ...
    
    # =================================================================
    # Phase 3a: Execute per-page jobs (existing + ParagraphAgent)
    # =================================================================
    job_results = await execute_jobs_parallel(
        jobs=jobs,
        page_markdowns=page_markdowns,  # Gets mutated
        page_images=page_images,
        element_bboxes=element_bboxes,
        page_width=page_width,
        ledger=ledger,
        event_bus=event_bus,
    )
    
    # =================================================================
    # Phase 3b: Cross-page merge pass (NEW)
    # =================================================================
    page_markdowns = await merge_cross_page_paragraphs(
        page_markdowns=page_markdowns,
        page_images=page_images,
        plan=plan,
        ledger=ledger,
        event_bus=event_bus,
    )
    
    # =================================================================
    # Phase 4: Issue fixer (existing)
    # =================================================================
    final_markdowns, fixes_applied, fixes_failed = await detect_and_fix_issues_async(
        page_markdowns, plan, page_images
    )
    
    # ... rest of pipeline unchanged ...
```

### 5. Surface needs_review in API

**File:** `src/api/documents.py`

Update ledger response to include review flag:

```python
class LedgerEntryResponse(BaseModel):
    """Response for a single ledger entry."""
    
    entry_id: str
    page: int
    action: str
    target: str
    before: str
    after: str
    reasoning: str
    confidence: float
    timestamp: str
    needs_review: bool = False  # NEW


def _build_ledger_response(ledger: Ledger) -> LedgerResponse:
    """Build ledger response from Ledger object."""
    entries = []
    for entry in ledger.entries:
        entries.append(LedgerEntryResponse(
            entry_id=entry.entry_id,
            page=entry.page,
            action=entry.action.value,
            target=entry.target,
            before=entry.before,
            after=entry.after,
            reasoning=entry.reasoning,
            confidence=entry.confidence,
            timestamp=entry.timestamp.isoformat(),
            needs_review=entry.needs_review,  # NEW
        ))
    
    # Group by page
    # ... existing grouping logic ...
    
    # Add review summary
    review_count = sum(1 for e in entries if e.needs_review)
    
    return LedgerResponse(
        entries=entries,
        # ... existing fields ...
        entries_needing_review=review_count,  # NEW
    )
```

---

## Architecture Diagram: Complete Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    COMPLETE PIPELINE FLOW                            │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: Extraction (Docling)                                       │
│  PDF → page_markdowns: dict[int, str]                               │
│       page_images: dict[int, Image]                                 │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2: Planning                                                   │
│                                                                       │
│  PageChainAgent analyzes each page:                                  │
│  ├── Headings, figures, tables (existing)                           │
│  └── Page artifacts, footnotes, citations, lists, typography (NEW)  │
│                                                                       │
│  stage4_generate_jobs() creates:                                     │
│  ├── STRUCTURE jobs (heading fixes)                                  │
│  ├── CONTENT jobs (figures, tables)                                  │
│  └── PARAGRAPH jobs (NEW)                                           │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 3a: Per-Page Job Execution                                    │
│                                                                       │
│  execute_jobs_parallel() routes jobs:                                │
│                                                                       │
│  ┌─────────────────────┐         ┌─────────────────────┐            │
│  │     Worker Agent    │         │   ParagraphAgent    │            │
│  │  (STRUCTURE/CONTENT)│         │    (PARAGRAPH)      │            │
│  │                     │         │                     │            │
│  │  - Heading fixes    │         │  Subagent tools:    │            │
│  │  - Alt-text         │         │  - page_artifacts   │            │
│  │  - Table transcribe │         │  - footnotes        │            │
│  │                     │         │  - citations        │            │
│  │                     │         │  - lists            │            │
│  │                     │         │  - typography       │            │
│  └──────────┬──────────┘         └──────────┬──────────┘            │
│             │                               │                        │
│             └───────────────┬───────────────┘                        │
│                             ▼                                        │
│                   page_markdowns updated                             │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 3b: Cross-Page Merge Pass (NEW)                              │
│                                                                       │
│  merge_cross_page_paragraphs():                                      │
│  ├── Find pages with has_page_continuation=True                     │
│  ├── For each boundary:                                             │
│  │   ├── Call paragraph_merge subagent                              │
│  │   ├── If should_merge and confidence >= 0.5:                     │
│  │   │   └── Apply merge, record in ledger                          │
│  │   └── Flag needs_review if confidence < 0.8                      │
│  └── Return updated page_markdowns                                   │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 4: Issue Fixer (existing)                                     │
│  Final cleanup for any remaining issues                              │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 5: Verification (existing)                                    │
│  Quality checks on final output                                      │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 6: Assembly & Response                                        │
│                                                                       │
│  final_markdown = join(page_markdowns)                               │
│                                                                       │
│  Ledger includes:                                                    │
│  ├── All edits with confidence scores                               │
│  └── needs_review flags for low-confidence edits                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Deliverables

| File | Action | Description |
|------|--------|-------------|
| `src/agents/planner.py` | Modify | Generate PARAGRAPH jobs |
| `src/agents/orchestrator.py` | Modify | Route jobs, add merge pass |
| `src/agents/__init__.py` | Modify | Export ParagraphAgent |
| `src/api/documents.py` | Modify | Surface needs_review in response |
| `src/api/schemas.py` | Modify | Add needs_review to LedgerEntryResponse |
| `tests/e2e/test_paragraph_pipeline.py` | Create | End-to-end test |

---

## Acceptance Criteria

- [ ] PARAGRAPH jobs created from detected issues
- [ ] Jobs routed correctly (Worker vs ParagraphAgent)
- [ ] Cross-page merge pass runs after per-page jobs
- [ ] Merge uses paragraph_merge subagent
- [ ] Low-confidence edits flagged with needs_review
- [ ] API response includes needs_review flag
- [ ] Existing Worker pipeline unchanged
- [ ] End-to-end test passes

---

## Definition of Done

1. Full pipeline works with paragraph fixes
2. Cross-page merges applied correctly
3. Ledger shows all changes with review flags
4. API returns needs_review count
5. All existing tests pass
6. New E2E test with paragraph issues passes
7. Manual test on real PDF with known issues

---

## Implementation Notes

### Job Ordering

```python
# Priority ensures correct execution order:
STRUCTURE jobs:  priority = 1           # Run first
CONTENT jobs:    priority = page_num + 1  # Run by page
PARAGRAPH jobs:  priority = page_num + 100  # Run after content
```

### Merge Pass Timing

The merge pass runs AFTER all per-page jobs because:
1. Page artifacts should be removed first
2. Merged text should be clean
3. We don't want to merge corrupted text

### Error Handling

If a merge fails:
1. Log the error
2. Skip that boundary
3. Continue with remaining merges
4. Don't fail the entire pipeline

### Human Review Integration

The `needs_review` flag integrates with your existing human review branch:
1. API returns `entries_needing_review` count
2. Each entry has `needs_review` boolean
3. Human review UI can filter by this flag
4. Reviewer can approve/reject flagged edits
