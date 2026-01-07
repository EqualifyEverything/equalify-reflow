# V5 Data Models Reference

Complete reference for all data structures used in the V5 pipeline.

**Location:** `src/agents/v5/models.py`

---

## Table of Contents

- [Enums](#enums)
- [Planning Phase Models](#planning-phase-models)
- [Job Execution Models](#job-execution-models)
- [Ledger Models](#ledger-models)
- [Validation Models](#validation-models)
- [Verification Models](#verification-models)
- [Recovery Models](#recovery-models)
- [Processing Result](#processing-result)

---

## Enums

### DocumentType

```python
class DocumentType(str, Enum):
    PAPER = "paper"
    SYLLABUS = "syllabus"
    SLIDES = "slides"
    MANUAL = "manual"
    REPORT = "report"
    ARTICLE = "article"
    OTHER = "other"
```

Detected during planning phase to inform structure analysis and processing strategy.

---

### TaskType

```python
class TaskType(str, Enum):
    ALT_TEXT = "alt_text"
    TABLE_TRANSCRIPTION = "table_transcription"
    HEADING_FIX = "heading_fix"
    OCR_FIX = "ocr_fix"
    FORMAT_FIX = "format_fix"
    SPELLING_FIX = "spelling_fix"
    PAGELESS_OPTIMIZATION = "pageless_optimization"
```

Types of tasks that worker agents can perform. Each task type has specific validation rules and processing logic.

---

### JobType

```python
class JobType(str, Enum):
    STRUCTURE = "structure"  # Heading fixes - run first
    CONTENT = "content"      # Alt-text, tables - run after
```

Job categorization for prioritization. STRUCTURE jobs always execute before CONTENT jobs to establish proper document hierarchy first.

---

### PageType

```python
class PageType(str, Enum):
    TITLE = "title"
    TOC = "toc"
    CONTENT = "content"
    REFERENCES = "references"
    APPENDIX = "appendix"
    BLANK = "blank"
```

Detected during quick scan to understand document structure.

---

### RecoveryAction

```python
class RecoveryAction(str, Enum):
    CLEANUP_EDIT = "cleanup_edit"
    REMOVE_PLACEHOLDER = "remove_placeholder"
    ACCEPT_WITH_CAVEAT = "accept_with_caveat"
    ESCALATE = "escalate"
```

Actions the recovery agent can take when fixing failed pages.

---

### ProcessingStatus

```python
class ProcessingStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
```

Final processing status after recovery attempts.

---

## Planning Phase Models

### PageSkeleton

**Purpose:** Quick page analysis without LLM (regex/heuristics)

**Location:** `src/agents/v5/models.py:78-111`

```python
class PageSkeleton(BaseModel):
    page_num: int  # 1-indexed

    # Extracted via regex
    headings: list[str]
    heading_levels: list[int]  # # count (1-6)

    # Counted from placeholders
    figure_count: int
    table_count: int

    # Heuristics
    word_count: int
    has_citations: bool
    page_type: PageType
```

**Usage:** Stage 1 of planning (quick scan)

**Example:**
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

---

### OutlineEntry

**Purpose:** Hierarchical document outline (TOC structure)

**Location:** `src/agents/v5/models.py:118-134`

```python
class OutlineEntry(BaseModel):
    heading: str
    level: int  # 1-6
    page_start: int
    page_end: int
    children: list[OutlineEntry]  # Recursive
```

**Usage:** Represents the authoritative document structure

**Example:**
```python
OutlineEntry(
    heading="2. Methodology",
    level=2,
    page_start=5,
    page_end=10,
    children=[
        OutlineEntry(
            heading="2.1 Data Collection",
            level=3,
            page_start=5,
            page_end=7,
            children=[]
        ),
        OutlineEntry(
            heading="2.2 Analysis",
            level=3,
            page_start=7,
            page_end=10,
            children=[]
        )
    ]
)
```

---

### HeadingFix

**Purpose:** Record a heading that needs level correction

**Location:** `src/agents/v5/models.py:136-145`

```python
class HeadingFix(BaseModel):
    page: int
    line: int  # 0 if unknown
    current_text: str
    current_level: int
    should_be_level: int
    reason: str
```

**Example:**
```python
HeadingFix(
    page=3,
    line=42,
    current_text="Introduction",
    current_level=2,
    should_be_level=1,
    reason="Document title should be H1"
)
```

---

### DocumentStructure

**Purpose:** Complete document structure inference from Stage 2

**Location:** `src/agents/v5/models.py:147-180`

```python
class DocumentStructure(BaseModel):
    title: str
    document_type: DocumentType

    # Hierarchical TOC
    outline: list[OutlineEntry]

    # Corrections needed
    heading_fixes: list[HeadingFix]

    # Dictionary for spell-checking
    key_terms: list[str]
    acronyms: dict[str, str]
```

**Example:**
```python
DocumentStructure(
    title="Deep Learning for Image Recognition",
    document_type=DocumentType.PAPER,
    outline=[...],
    heading_fixes=[...],
    key_terms=["CNN", "ResNet", "ImageNet"],
    acronyms={"CNN": "Convolutional Neural Network"}
)
```

---

### FigureContext

**Purpose:** Context for a figure needing alt-text

**Location:** `src/agents/v5/models.py:187-207`

```python
class FigureContext(BaseModel):
    figure_index: int  # 1-indexed on page
    location: str  # e.g., "after heading 2.1"
    appears_to_be: str  # e.g., "architecture diagram"
    surrounding_text: str  # Caption or nearby text
    is_decorative: bool
```

**Example:**
```python
FigureContext(
    figure_index=1,
    location="after Introduction heading",
    appears_to_be="bar chart",
    surrounding_text="Figure 1: Student enrollment by year",
    is_decorative=False
)
```

---

### TableContext

**Purpose:** Context for a table needing transcription

**Location:** `src/agents/v5/models.py:209-228`

```python
class TableContext(BaseModel):
    table_index: int  # 1-indexed on page
    location: str
    appears_to_be: str  # e.g., "comparison table"
    appears_to_contain: str  # What data
    caption: str | None
    estimated_rows: int
    estimated_cols: int
    row_count: int  # alias
    has_header: bool
```

**Example:**
```python
TableContext(
    table_index=1,
    location="after Data Collection section",
    appears_to_be="data table",
    appears_to_contain="sample statistics by group",
    caption="Table 1: Descriptive Statistics",
    estimated_rows=5,
    estimated_cols=4,
    has_header=True
)
```

---

### PagePlan

**Purpose:** Detailed analysis of a single page from Stage 3

**Location:** `src/agents/v5/models.py:230-265`

```python
class PagePlan(BaseModel):
    page_num: int

    # LLM analysis
    summary: str  # 1-2 sentences
    keywords: list[str]  # Page-specific terms
    section_context: str  # e.g., "Part of 2.1 Architecture"

    # Work items
    figures: list[FigureContext]
    tables: list[TableContext]

    # Issues
    ocr_errors: list[str]
    formatting_issues: list[str]
```

**Example:**
```python
PagePlan(
    page_num=5,
    summary="Describes the neural network architecture with diagrams.",
    keywords=["convolutional layer", "pooling", "activation function"],
    section_context="Part of 2. Methodology",
    figures=[FigureContext(...)],
    tables=[],
    ocr_errors=["neur al" should be "neural"],
    formatting_issues=[]
)
```

---

### DocumentPlan

**Purpose:** Complete planning phase output (single source of truth)

**Location:** `src/agents/v5/models.py:344-383`

```python
class DocumentPlan(BaseModel):
    document_id: str
    created_at: datetime

    # Source info
    filename: str
    total_pages: int

    # From Stage 2
    structure: DocumentStructure

    # From Stage 3
    pages: dict[int, PagePlan]  # indexed by page number

    # From Stage 4
    jobs: list[Job]

    # Aggregated dictionary
    full_dictionary: list[str]

    # Stats
    planning_duration_ms: int
    planning_tokens_input: int
    planning_tokens_output: int
```

**Usage:** Workers follow this plan, they don't question it.

**Data Flow:**
```
Quick Scan → Page Chain → Job Generation → DocumentPlan
```

---

## Job Execution Models

### Task

**Purpose:** Single unit of work within a job

**Location:** `src/agents/v5/models.py:272-283`

```python
class Task(BaseModel):
    task_id: str
    task_type: TaskType
    target: str  # e.g., "fig:1", "table:2", "heading"
    context: str  # Relevant context
    priority: int  # 1-3 (1=highest)
```

**Example:**
```python
Task(
    task_id="task-abc123",
    task_type=TaskType.ALT_TEXT,
    target="fig:1",
    context="Figure appears after Introduction, shows bar chart",
    priority=1
)
```

---

### JobContext

**Purpose:** Slim context slice for worker agent

**Location:** `src/agents/v5/models.py:285-307`

```python
class JobContext(BaseModel):
    document_title: str
    document_type: DocumentType
    section_context: str  # Where this page fits
    dictionary: list[str]  # For spell-checking

    # Only relevant outline entries
    relevant_outline: list[OutlineEntry]
```

**Purpose:** Keeps token usage bounded by only providing what the worker needs.

**Example:**
```python
JobContext(
    document_title="Deep Learning for Image Recognition",
    document_type=DocumentType.PAPER,
    section_context="Part of 2.1 Data Collection",
    dictionary=["CNN", "ResNet", "ImageNet"],
    relevant_outline=[...]
)
```

---

### Job

**Purpose:** Scoped unit of work for a worker agent

**Location:** `src/agents/v5/models.py:309-337`

```python
class Job(BaseModel):
    job_id: str
    job_type: JobType  # STRUCTURE or CONTENT
    priority: int  # Lower = higher priority

    page: int
    tasks: list[Task]
    context: JobContext
    page_markdown: str  # Current markdown

    # Status tracking
    status: Literal["pending", "running", "completed", "failed"]
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
```

**Key Properties:**
- Jobs are independent (can run in parallel)
- Each job handles one page
- STRUCTURE jobs run before CONTENT jobs

**Example:**
```python
Job(
    job_id="job-001",
    job_type=JobType.STRUCTURE,
    priority=1,
    page=3,
    tasks=[Task(...)],
    context=JobContext(...),
    page_markdown="# Introduction\n\n...",
    status="pending"
)
```

---

### JobResult

**Purpose:** Result of executing a job

**Location:** `src/agents/v5/worker.py` (not in models.py, defined inline)

```python
class JobResult(BaseModel):
    job_id: str
    success: bool
    updated_markdown: str  # Final markdown after edits
    ledger_entries: list[LedgerEntry]
    tasks_completed: int
    tasks_failed: int
    input_tokens: int
    output_tokens: int
    duration_ms: int
    error: str | None
```

---

## Ledger Models

### LedgerEntry

**Purpose:** Immutable record of a change

**Location:** `src/agents/v5/models.py:390-421`

```python
class LedgerEntry(BaseModel):
    entry_id: str
    job_id: str
    page: int
    timestamp: datetime

    # What changed
    action: TaskType
    target: str  # e.g., "fig:1"

    before: str  # Original text/placeholder
    after: str  # New content

    reasoning: str
    confidence: float  # 0.0-1.0

    # Validation status
    validated: bool
    validation_feedback: str | None
```

**Key Properties:**
- Append-only (never modified)
- Complete audit trail
- Streamed in real-time

**Example:**
```python
LedgerEntry(
    entry_id="entry-123",
    job_id="job-001",
    page=3,
    timestamp=datetime.utcnow(),
    action=TaskType.ALT_TEXT,
    target="fig:1",
    before="<!-- image 1 -->",
    after="![Bar chart showing enrollment trends](image1.png)",
    reasoning="Figure shows bar chart with enrollment data",
    confidence=0.95,
    validated=True,
    validation_feedback=None
)
```

---

### Ledger

**Purpose:** Append-only log of all changes

**Location:** `src/agents/v5/models.py:423-449`

```python
class Ledger(BaseModel):
    document_id: str
    entries: list[LedgerEntry]
    created_at: datetime

    def append(self, entry: LedgerEntry) -> None
    def get_page_entries(self, page: int) -> list[LedgerEntry]
    def get_job_entries(self, job_id: str) -> list[LedgerEntry]

    @property
    def total_edits(self) -> int  # Count of validated edits
```

**Usage:**
```python
ledger = Ledger(document_id="doc-123")
ledger.append(LedgerEntry(...))
page_3_edits = ledger.get_page_entries(page=3)
print(f"Total edits: {ledger.total_edits}")
```

---

## Validation Models

### EditProposal

**Purpose:** Proposed edit from worker (not yet applied)

**Location:** `src/agents/v5/models.py:456-464`

```python
class EditProposal(BaseModel):
    target: str
    task_type: TaskType
    before: str  # Must exist in current markdown
    after: str  # Must differ from before
    reasoning: str
```

**Example:**
```python
EditProposal(
    target="fig:1",
    task_type=TaskType.ALT_TEXT,
    before="<!-- image 1 -->",
    after="![Bar chart showing enrollment](image1.png)",
    reasoning="Figure shows enrollment data"
)
```

---

### SpellIssue

**Purpose:** Spelling issue found during validation

**Location:** `src/agents/v5/models.py:466-475`

```python
class SpellIssue(BaseModel):
    word: str
    suggestion: str
    in_dictionary: bool
```

**Example:**
```python
SpellIssue(
    word="neur al",
    suggestion="neural",
    in_dictionary=False
)
```

---

### ValidationResult

**Purpose:** Result of validating a proposed edit

**Location:** `src/agents/v5/models.py:477-493`

```python
class ValidationResult(BaseModel):
    approved: bool
    edit: EditProposal

    # If not approved, why?
    spell_issues: list[SpellIssue]
    lint_issues: list[str]
    consistency_issues: list[str]

    # Feedback to agent
    feedback: str | None
```

**Example (Approved):**
```python
ValidationResult(
    approved=True,
    edit=EditProposal(...),
    spell_issues=[],
    lint_issues=[],
    consistency_issues=[],
    feedback=None
)
```

**Example (Rejected):**
```python
ValidationResult(
    approved=False,
    edit=EditProposal(...),
    spell_issues=[SpellIssue(...)],
    lint_issues=["Unclosed bracket in markdown"],
    consistency_issues=["before text not found in markdown"],
    feedback="Edit rejected: 'before' text not found. Use find_text() tool."
)
```

---

## Verification Models

### PageVerification

**Purpose:** Verification result for a single page

**Location:** `src/agents/v5/models.py:500-510`

```python
class PageVerification(BaseModel):
    page_num: int
    passed: bool
    issues: list[str]
    confidence: float  # 0.0-1.0
```

**Example:**
```python
PageVerification(
    page_num=3,
    passed=False,
    issues=[
        "Unfilled placeholder: <!-- image 1 -->",
        "Heading hierarchy skip: H2 → H4"
    ],
    confidence=0.9
)
```

---

### VerificationReport

**Purpose:** Final verification report for complete document

**Location:** `src/agents/v5/models.py:512-532`

```python
class VerificationReport(BaseModel):
    document_id: str
    passed: bool  # Overall pass/fail

    pages: list[PageVerification]

    # Aggregated issues
    total_issues: int
    critical_issues: list[str]
    warnings: list[str]

    # Stats
    pages_passed: int
    pages_failed: int
    verification_duration_ms: int
```

**Pass Criteria:**
- No critical issues
- >= 80% pages passed

**Example:**
```python
VerificationReport(
    document_id="doc-123",
    passed=False,
    pages=[PageVerification(...), ...],
    total_issues=15,
    critical_issues=[
        "Page 3: Unfilled placeholder",
        "Page 5: Missing alt-text"
    ],
    warnings=["Page 7: Minor formatting issue"],
    pages_passed=8,
    pages_failed=2,
    verification_duration_ms=5000
)
```

---

## Recovery Models

### RecoveryAttemptStatus

**Purpose:** Status of recovery attempt

**Location:** `src/agents/v5/models.py:539-547`

```python
class RecoveryAttemptStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ACCEPTED_WITH_CAVEATS = "accepted_with_caveats"
```

---

### RecoveryEdit

**Purpose:** Single recovery edit

**Location:** `src/agents/v5/models.py:577-591`

```python
class RecoveryEdit(BaseModel):
    edit_id: str
    page: int
    action: RecoveryAction
    target: str
    before: str
    after: str
    reasoning: str
    confidence: float  # 0.0-1.0
```

**Example:**
```python
RecoveryEdit(
    edit_id="edit-001",
    page=3,
    action=RecoveryAction.REMOVE_PLACEHOLDER,
    target="fig:1",
    before="<!-- image 1 -->",
    after="",
    reasoning="Image cannot be described from available context",
    confidence=0.7
)
```

---

### RecoveryAttempt

**Purpose:** Record of recovery attempt on a page

**Location:** `src/agents/v5/models.py:593-617`

```python
class RecoveryAttempt(BaseModel):
    attempt_id: str
    page_num: int
    attempt_number: int  # 1 or 2
    status: RecoveryAttemptStatus
    original_issues: list[str]
    edits_proposed: int
    edits_applied: int
    caveats: list[str]  # For accepted-with-caveats
    duration_ms: int
```

**Example:**
```python
RecoveryAttempt(
    attempt_id="attempt-001",
    page_num=3,
    attempt_number=1,
    status=RecoveryAttemptStatus.SUCCEEDED,
    original_issues=[
        "Unfilled placeholder: <!-- image 1 -->",
        "Empty alt-text"
    ],
    edits_proposed=2,
    edits_applied=2,
    caveats=[],
    duration_ms=15000
)
```

---

### RecoveryReport

**Purpose:** Complete recovery phase report

**Location:** `src/agents/v5/models.py:619-651`

```python
class RecoveryReport(BaseModel):
    document_id: str
    recovery_attempted: bool

    pages_recovered: list[int]
    pages_accepted_with_caveats: list[int]
    pages_unrecoverable: list[int]

    attempts: list[RecoveryAttempt]
    total_recovery_edits: int
    recovery_duration_ms: int
```

**Example:**
```python
RecoveryReport(
    document_id="doc-123",
    recovery_attempted=True,
    pages_recovered=[3, 5],
    pages_accepted_with_caveats=[7],
    pages_unrecoverable=[],
    attempts=[RecoveryAttempt(...), ...],
    total_recovery_edits=5,
    recovery_duration_ms=30000
)
```

---

## Processing Result

### ProcessingResult

**Purpose:** Final result of V5 pipeline

**Location:** `src/agents/v5/models.py:790-822`

```python
class ProcessingResult(BaseModel):
    document_id: str
    success: bool

    # Outputs
    final_markdown: str
    ledger: Ledger
    verification: VerificationReport

    # Stats
    total_pages: int
    total_edits: int
    total_jobs: int

    # Cost tracking
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float

    # Timing
    total_duration_ms: int
    planning_duration_ms: int
    execution_duration_ms: int
    verification_duration_ms: int

    # Recovery (optional)
    recovery_report: RecoveryReport | None
```

**Complete Example:**
```python
ProcessingResult(
    document_id="doc-123",
    success=True,
    final_markdown="# Document Title\n\n## Introduction\n...",
    ledger=Ledger(...),
    verification=VerificationReport(...),
    total_pages=10,
    total_edits=47,
    total_jobs=25,
    total_input_tokens=75000,
    total_output_tokens=35000,
    total_cost=0.0823,
    total_duration_ms=125000,
    planning_duration_ms=25000,
    execution_duration_ms=75000,
    verification_duration_ms=5000,
    recovery_report=None
)
```

---

## Data Flow Diagram

```
PDF Upload
    ↓
Docling Conversion
    ↓
page_markdowns: dict[int, str]
page_images: dict[int, Image.Image]
    ↓
┌─────────────────────────────────────┐
│ PLANNING PHASE                      │
├─────────────────────────────────────┤
│ Stage 1: PageSkeleton[] (quick scan)│
│ Stage 2: DocumentStructure          │
│ Stage 3: PagePlan[] (page chain)    │
│ Stage 4: Job[] (job generation)     │
└─────────────────┬───────────────────┘
                  ↓
            DocumentPlan
                  ↓
┌─────────────────────────────────────┐
│ EXECUTION PHASE                     │
├─────────────────────────────────────┤
│ Job[] → Worker Agents (parallel)    │
│ EditProposal → ValidationResult     │
│ Validated → LedgerEntry             │
│ Apply edits → updated markdowns     │
└─────────────────┬───────────────────┘
                  ↓
        dict[int, str] (final_markdowns)
        Ledger (complete change log)
                  ↓
┌─────────────────────────────────────┐
│ VERIFICATION PHASE                  │
├─────────────────────────────────────┤
│ PageVerification[] → VerificationReport│
└─────────────────┬───────────────────┘
                  ↓
          VerificationReport
        (passed OR failed + >= 50% pages OK)
                  ↓
┌─────────────────────────────────────┐
│ RECOVERY PHASE (if needed)          │
├─────────────────────────────────────┤
│ RecoveryAttempt[] → RecoveryReport  │
│ Updated markdowns                   │
└─────────────────┬───────────────────┘
                  ↓
            ProcessingResult
         (final markdown + reports)
```

---

## Model Relationships

```
DocumentPlan
├── structure: DocumentStructure
│   ├── outline: list[OutlineEntry]  # Recursive
│   └── heading_fixes: list[HeadingFix]
├── pages: dict[int, PagePlan]
│   ├── figures: list[FigureContext]
│   └── tables: list[TableContext]
└── jobs: list[Job]
    ├── tasks: list[Task]
    └── context: JobContext
        └── relevant_outline: list[OutlineEntry]

ProcessingResult
├── final_markdown: str
├── ledger: Ledger
│   └── entries: list[LedgerEntry]
├── verification: VerificationReport
│   └── pages: list[PageVerification]
└── recovery_report: RecoveryReport | None
    └── attempts: list[RecoveryAttempt]
```

---

## Key Patterns

### Immutability
- `LedgerEntry` - Never modified after creation
- `DocumentPlan` - Single source of truth, not updated during execution

### Append-Only
- `Ledger.entries` - Only grows, never shrinks or modifies
- Complete audit trail

### Hierarchical
- `OutlineEntry` - Recursive tree structure
- `DocumentPlan` → `Job[]` → `Task[]`

### Status Tracking
- Jobs: `pending` → `running` → `completed`/`failed`
- Recovery: `PENDING` → `IN_PROGRESS` → `SUCCEEDED`/`FAILED`/`ACCEPTED_WITH_CAVEATS`

### Validation Gates
- `EditProposal` → `ValidationResult` → `LedgerEntry` (if approved)
- Agent proposes → System validates → If approved, commit

---

## Type Safety

All models use Pydantic for runtime validation:

```python
from pydantic import BaseModel, Field

class Example(BaseModel):
    required_field: str
    optional_field: str | None = None
    defaulted_field: int = 0
    validated_field: int = Field(..., ge=1, le=6)
```

**Benefits:**
- Runtime type checking
- Automatic validation
- JSON serialization/deserialization
- IDE autocomplete support
- Clear error messages

---

## Next Steps

- Review [Phase Documentation](./pipeline-phase-1-planning.md) to see how these models are used
- Explore [API Integration Guide](./pipeline-api-integration.md) for JSON examples
- Check [System Overview](./pipeline-system-overview.md) for architecture context
