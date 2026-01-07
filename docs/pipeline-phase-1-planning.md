# V5 Phase 1: Planning

Phase 1 analyzes the document structure and creates an execution plan without making any edits.

**Goal:** Build complete understanding of document structure and generate worker jobs.

**Input:** Raw page markdowns + page images (from Docling)

**Output:** `DocumentPlan` (structure + jobs)

---

## Overview

Planning happens in 4 sequential stages:

```
Stage 1: Quick Scan (deterministic, no LLM)
    ↓
Stage 2: Page Chain (sequential LLM analysis)
    ↓
Stage 3: Job Generation (deterministic)
    ↓
DocumentPlan (complete plan)
```

**Key Properties:**
- No edits made (analysis only)
- Single sequential pass through document
- Context chaining between pages
- Deterministic job generation

**Location:** `src/agents/planner.py`, `src/agents/page_chain.py`

---

## Stage 1: Quick Scan

**Purpose:** Fast extraction of basic page structure without LLM calls.

**Function:** `stage1_quick_scan()` in `planner.py:70-194`

### Process

For each page, extract via regex/heuristics:

1. **Headings**
   - Pattern: `^(#{1,6})\s+(.+)$`
   - Capture text and level (# count)

2. **Figures**
   - Count `![` patterns
   - Count `<!-- image` comments

3. **Tables**
   - Count `|.*|.*|` patterns (markdown tables)
   - Count `<!-- table` comments

4. **Word Count**
   - Split on whitespace
   - Approximate content density

5. **Citations**
   - Detect `[1]`, `[2]` patterns
   - Indicates references section

6. **Page Type**
   - Title: first page with single large heading
   - TOC: multiple headings, low word count
   - References: high citation density
   - Content: default
   - Blank: < 50 words

### Output

```python
PageSkeleton(
    page_num=3,
    headings=["Introduction", "Background"],
    heading_levels=[2, 3],
    figure_count=2,
    table_count=1,
    word_count=450,
    has_citations=True,
    page_type=PageType.CONTENT
)
```

### Events Emitted

- `PageScannedEvent` for each page

### Why Quick Scan?

Provides fast overview before expensive LLM analysis:
- Identifies blank pages (skip detailed analysis)
- Detects references/appendices (different handling)
- Counts work items (figures, tables)
- Estimates processing time

---

## Stage 2: Page Chain (Sequential Analysis)

**Purpose:** Deep LLM analysis with context chaining across pages.

**Function:** `run_page_chain()` in `page_chain.py:1-400+`

### Key Innovation: Context Chaining

Unlike parallel processing, pages are analyzed **sequentially** with accumulated context:

```python
class PageChainState:
    document_title: str | None       # Set on page 1
    document_type: DocumentType | None
    last_heading: HeadingInfo | None # From previous page
    outline: list[OutlineEntry]      # Built incrementally
    dictionary: set[str]             # Accumulated terms
    heading_fixes: list[HeadingFix]
    page_summaries: dict[int, str]
    figures_by_page: dict[int, list[FigureAnalysis]]
    tables_by_page: dict[int, list[TableAnalysis]]
```

**Why Sequential?**
- Heading levels depend on previous pages
- Dictionary grows as new terms discovered
- Section context flows page-to-page
- No contradictions (single pass, single view)

### Agent Configuration

**Model:** Haiku 4.5 on AWS Bedrock (`MODEL_TIER_MAP[ModelTier.EFFICIENT]`)

**System Prompt:** (from `page_chain.py:191-236`)

```
You are a document structure analyst processing one page at a time.

Your job is to analyze the current page and:
1. Identify all headings and determine their CORRECT level
2. Summarize what the page covers
3. Extract domain-specific terms for spell-checking
4. Describe any figures and tables

## Heading Level Rules

- Document title = H1 (level 1) - only ONE per document
- Major sections ("1", "2", "3") = H2 (level 2)
- Subsections ("1.1", "2.1") = H3 (level 3)
- Sub-subsections ("1.1.1", "2.1.1") = H4 (level 4)

IMPORTANT:
- If heading has section number, use it to determine level
- "1 Introduction" → level 2 (one number = H2)
- "1.1 Background" → level 3 (two numbers = H3)
- "Abstract", "References" without numbers → typically H2
- Document title is ONLY H1

## Context from Previous Pages

You'll receive the last heading from the previous page. Use this to:
- Ensure continuity (no unexpected jumps)
- Understand where we are in document structure

## Output Requirements

1. **Headings**: List ALL headings with current_level and correct_level
2. **Summary**: 2-3 sentences about page content
3. **Terms**: Domain-specific words for spell-check dictionary
4. **Figures**: Describe what each figure appears to show
5. **Tables**: Describe what data each table contains

For page 1 only: Also provide document_title and document_type.
```

**Output Model:** `PageAnalysisOutput`

```python
class PageAnalysisOutput(BaseModel):
    # Page 1 only
    document_title: str | None
    document_type: DocumentType

    # Every page
    headings: list[HeadingAnalysis]
    # - heading: str
    # - current_level: int (from markdown)
    # - correct_level: int (should be)
    # - reasoning: str (if changed)

    summary: str  # 2-3 sentences

    terms: list[str]  # Domain vocabulary

    figures: list[FigureAnalysis]
    # - figure_index: int
    # - appears_to_be: str (e.g., "bar chart")
    # - surrounding_context: str
    # - is_decorative: bool

    tables: list[TableAnalysis]
    # - table_index: int
    # - appears_to_contain: str
    # - row_count_estimate: int
    # - has_header: bool
```

### Prompt Construction

Each page receives:

1. **Context Section:**
   - Page number
   - Previous page's last heading (for continuity)
   - Current document structure (outline so far)
   - Accumulated dictionary terms

2. **Markdown Content:**
   - Full page markdown
   - Pre-extracted heading list (for reference)
   - Figure/table counts

3. **Instructions:**
   - Analyze headings (current vs. correct level)
   - Summarize page content
   - Extract terms
   - Describe figures/tables

**Example Prompt** (page 3 of 10):

```
This is page 3 of 10.
Previous page ended with: 'Introduction' (H2)

Current document outline:
  1. Abstract (H2, page 1)
  2. Introduction (H2, pages 2-3)

Dictionary so far: ["Python", "NumPy", "pandas", "machine learning"]

---

Page markdown:

## 1.1 Background

Machine learning has become increasingly important...

<!-- image 1 -->

...

---

Found headings:
  - '1.1 Background' (currently H2)

Found 1 figure, 0 tables.

Analyze this page.
```

### State Updates

After each page analysis:

1. **Update Document Title** (page 1 only)
2. **Update Document Type** (page 1 only)
3. **Process Headings:**
   - Compare current_level vs. correct_level
   - If different, add to `heading_fixes`
   - Update `last_heading` for next page
   - Extend `outline` with new entries
4. **Accumulate Terms:**
   - Add to `dictionary` set (case-insensitive)
5. **Store Page Analysis:**
   - Save summary
   - Index figures by page
   - Index tables by page

### Events Emitted

- `PageSummarizedEvent` for each page
- `StructureInferredEvent` when complete

### Conversion to Models

After all pages processed, convert `PageChainState` to:

1. **DocumentStructure** (`convert_chain_state_to_structure()`)
   - title, document_type, outline
   - heading_fixes
   - key_terms (from dictionary)

2. **dict[int, PagePlan]** (`convert_chain_state_to_page_plans()`)
   - summary, section_context
   - figures, tables
   - keywords (page-specific terms)

---

## Stage 3: Job Generation

**Purpose:** Create worker jobs from analysis results.

**Function:** `stage3_job_generation()` in `planner.py:600+`

### Process

For each page with work items:

1. **Collect Tasks:**
   ```python
   tasks = []

   # Heading fixes (from DocumentStructure)
   for fix in heading_fixes_for_this_page:
       tasks.append(Task(
           task_type=TaskType.HEADING_FIX,
           target="heading",
           context=fix.reason,
           priority=1  # High priority
       ))

   # Alt-text (from PagePlan)
   for figure in page_plan.figures:
       if not figure.is_decorative:
           tasks.append(Task(
               task_type=TaskType.ALT_TEXT,
               target=f"fig:{figure.figure_index}",
               context=figure.surrounding_text,
               priority=2
           ))

   # Tables (from PagePlan)
   for table in page_plan.tables:
       tasks.append(Task(
           task_type=TaskType.TABLE_TRANSCRIPTION,
           target=f"table:{table.table_index}",
           context=table.appears_to_contain,
           priority=2
       ))

   # OCR errors (from PagePlan)
   for error in page_plan.ocr_errors:
       tasks.append(Task(
           task_type=TaskType.OCR_FIX,
           target="text",
           context=error,
           priority=3
       ))
   ```

2. **Classify Job Type:**
   ```python
   if any(t.task_type == TaskType.HEADING_FIX for t in tasks):
       job_type = JobType.STRUCTURE  # High priority
   else:
       job_type = JobType.CONTENT    # Lower priority
   ```

3. **Build Job Context:**
   ```python
   context = JobContext(
       document_title=structure.title,
       document_type=structure.document_type,
       section_context=page_plan.section_context,
       dictionary=full_dictionary,
       relevant_outline=get_relevant_outline(page, outline)
   )
   ```

4. **Create Job:**
   ```python
   job = Job(
       job_id=str(uuid4()),
       job_type=job_type,
       priority=1 if job_type == JobType.STRUCTURE else 2,
       page=page_num,
       tasks=tasks,
       context=context,
       page_markdown=page_markdowns[page_num],
       status="pending"
   )
   ```

### Job Prioritization

```python
# Sort for execution order:
jobs.sort(key=lambda j: (j.priority, j.page))

# Result: All STRUCTURE jobs (priority=1) before CONTENT jobs (priority=2)
# Within same priority, ordered by page number
```

**Why Structure First?**
- Headings define document hierarchy
- Content workers need correct structure context
- Prevents cascading errors

### Events Emitted

- `JobCreatedEvent` for each job
- `PlanningCompleteEvent` with summary

---

## Stage 4: DocumentPlan Assembly

**Purpose:** Package all planning outputs into single model.

**Function:** `plan_document()` in `planner.py`

### Assembly

```python
plan = DocumentPlan(
    document_id=str(uuid4()),
    created_at=datetime.utcnow(),
    filename=filename,
    total_pages=len(page_markdowns),

    # From Stage 2
    structure=document_structure,
    pages=page_plans,

    # From Stage 3
    jobs=jobs,

    # Aggregated
    full_dictionary=list(dictionary_set),

    # Stats
    planning_duration_ms=total_time,
    planning_tokens_input=sum_input_tokens,
    planning_tokens_output=sum_output_tokens
)
```

### What Workers Receive

Each worker gets:

1. **Their Job:**
   - job_id, tasks, page_markdown
   - Slim context (only what they need)

2. **NOT Included:**
   - Other jobs (don't need to know)
   - Full document plan (too large)
   - Other pages' markdowns (not their concern)

**Token Efficiency:**
- DocumentPlan: ~10-50KB (all analysis)
- Job.context: ~1-5KB (scoped to page)

---

## Data Flow

```
page_markdowns: dict[int, str]
page_images: dict[int, Image.Image]
    ↓
┌─────────────────────────────────────┐
│ Stage 1: Quick Scan                 │
│ (deterministic, fast)               │
└────────────────┬────────────────────┘
                 ↓
         PageSkeleton[]
                 ↓
┌─────────────────────────────────────┐
│ Stage 2: Page Chain                 │
│ (LLM, sequential with state)        │
│                                     │
│ For each page:                      │
│   - Build prompt with context       │
│   - Call LLM                        │
│   - Update PageChainState           │
│   - Emit PageSummarizedEvent        │
└────────────────┬────────────────────┘
                 ↓
         PageChainState
                 ↓
    ┌────────────────────┬──────────────────────┐
    ↓                    ↓                      ↓
DocumentStructure   dict[int, PagePlan]   full_dictionary
    ↓                    ↓                      ↓
┌─────────────────────────────────────┐
│ Stage 3: Job Generation             │
│ (deterministic)                     │
│                                     │
│ For each page with work:            │
│   - Collect tasks                   │
│   - Build JobContext                │
│   - Create Job                      │
│   - Emit JobCreatedEvent            │
└────────────────┬────────────────────┘
                 ↓
              Job[]
                 ↓
┌─────────────────────────────────────┐
│ Stage 4: Assemble DocumentPlan      │
└────────────────┬────────────────────┘
                 ↓
          DocumentPlan
         (ready for execution)
```

---

## Typical Metrics

**10-page Research Paper:**

| Stage | Duration | Tokens In | Tokens Out |
|-------|----------|-----------|------------|
| Stage 1 | 100ms | 0 | 0 |
| Stage 2 | 15-30s | 15-20K | 5-10K |
| Stage 3 | 100ms | 0 | 0 |
| **Total** | **~25s** | **~18K** | **~8K** |

**Cost:** ~$0.01-0.015 per document

**Jobs Generated:** Typically 1-3 jobs per page (15-30 total)

---

## Key Design Decisions

### Why Sequential Page Analysis?

**Alternative:** Parallel analysis (all pages at once)

**Problems with Parallel:**
- Inconsistent heading levels (no shared context)
- Duplicate dictionary entries
- Conflicting document titles
- No flow/continuity understanding

**Benefits of Sequential:**
- Consistent heading hierarchy
- Single source of truth
- Natural continuity
- Growing dictionary (no duplicates)

**Tradeoff:** Slower (25s vs. 5s), but much higher quality

---

### Why Separate Quick Scan?

**Benefits:**
- Instant page count/structure overview
- Skip blank pages
- Identify special sections (refs, appendices)
- Estimate processing time

**Cost:** Minimal (~100ms)

---

### Why Deterministic Job Generation?

**Alternative:** LLM-based job planning

**Problems with LLM:**
- Unpredictable job count
- May miss work items
- May create duplicate jobs
- Higher cost

**Benefits of Deterministic:**
- Predictable job count
- Guaranteed coverage
- No duplicates
- Zero cost (no LLM call)

---

## Error Handling

### LLM Failures

```python
try:
    result = await agent.run(prompt)
except Exception as e:
    logger.error(f"Page {page_num} analysis failed: {e}")
    # Use fallback: PageAnalysisOutput with minimal info
    result = PageAnalysisOutput(
        document_title=None,
        document_type=DocumentType.OTHER,
        headings=[],
        summary=f"Page {page_num} (analysis failed)",
        terms=[],
        figures=[],
        tables=[]
    )
```

**Impact:** Page analysis degraded, but planning continues.

---

### Invalid Heading Levels

```python
# LLM might return invalid level (e.g., 0 or 7)
if not (1 <= heading.correct_level <= 6):
    logger.warning(f"Invalid level {heading.correct_level}, using markdown level")
    heading.correct_level = heading.current_level
```

---

### Empty Pages

Quick scan detects blank pages:

```python
if skeleton.word_count < 50:
    # Skip detailed analysis
    continue
```

---

## Testing

### Unit Tests

Test each stage independently:

```python
def test_quick_scan():
    markdown = "## Introduction\n\nSome text..."
    skeleton = quick_scan_page(page_num=1, markdown=markdown)
    assert skeleton.headings == ["Introduction"]
    assert skeleton.heading_levels == [2]

def test_heading_level_inference():
    # Mock LLM response
    output = PageAnalysisOutput(
        headings=[HeadingAnalysis(
            heading="1.1 Background",
            current_level=2,
            correct_level=3,
            reasoning="Has two section numbers → H3"
        )],
        ...
    )
    # Verify conversion to HeadingFix
    assert len(heading_fixes) == 1
    assert heading_fixes[0].should_be_level == 3

def test_job_generation():
    page_plan = PagePlan(
        page_num=3,
        figures=[FigureContext(figure_index=1, ...)],
        tables=[TableContext(table_index=1, ...)]
    )
    jobs = generate_jobs(page_plan, ...)
    assert len(jobs) >= 1
    assert any(t.task_type == TaskType.ALT_TEXT for job in jobs for t in job.tasks)
```

### Integration Tests

Test full planning pipeline:

```python
async def test_full_planning():
    page_markdowns = load_test_document()
    plan = await plan_document(
        page_markdowns=page_markdowns,
        page_images={},
        filename="test.pdf"
    )

    assert plan.structure.title != ""
    assert len(plan.pages) == len(page_markdowns)
    assert len(plan.jobs) > 0
    assert plan.planning_tokens_input > 0
```

---

## Debugging

### Enable Verbose Logging

```python
import logging
logging.getLogger("src.agents.v5.planner").setLevel(logging.DEBUG)
logging.getLogger("src.agents.v5.page_chain").setLevel(logging.DEBUG)
```

### Inspect PageChainState

```python
# Add breakpoint in page_chain.py after each page
print(f"Page {page_num}:")
print(f"  Last heading: {state.last_heading}")
print(f"  Outline entries: {len(state.outline)}")
print(f"  Dictionary size: {len(state.dictionary)}")
print(f"  Heading fixes: {len(state.heading_fixes)}")
```

### Visualize Outline

```python
def print_outline(entries, indent=0):
    for entry in entries:
        print(f"{'  ' * indent}- {entry.heading} (H{entry.level}, pg {entry.page_start}-{entry.page_end})")
        print_outline(entry.children, indent + 1)

print_outline(plan.structure.outline)
```

---

## Common Issues

### Issue: Inconsistent Heading Levels

**Symptom:** Worker jobs fail because heading levels don't match plan

**Cause:** Page chain state not properly maintained

**Fix:** Verify `last_heading` updates after each page

---

### Issue: High Token Usage

**Symptom:** Planning costs > $0.05 per document

**Cause:** Very long pages or excessive context

**Fix:**
- Truncate page markdown if > 4K tokens
- Limit outline context to relevant sections only

---

### Issue: Jobs Missing Work Items

**Symptom:** Figures/tables not transcribed

**Cause:** Job generation logic not detecting all items

**Fix:** Review PagePlan.figures/tables population

---

## Next Steps

- Review [Phase 2: Execution](./pipeline-phase-2-execution.md) to see how jobs are processed
- Check [Data Models Reference](./pipeline-data-models.md) for complete schema
- Explore [API Integration](./pipeline-api-integration.md) for SSE event details
