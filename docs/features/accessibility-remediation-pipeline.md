# Accessibility Remediation Pipeline

> **Status:** Proposed
> **Related Issues:** [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24)
> **Last Updated:** 2024-12-10

## Overview

This document describes the accessibility remediation pipeline architecture for transforming PDFs into accessible, semantic markdown. The system uses a multi-phase approach with specialized AI agents, human-in-the-loop review, and search-replace editing for applying corrections.

### Key Design Principles

1. **Observation-first design** - AI observes discrepancies between visual presentation and markup rather than claiming WCAG violations
2. **Smart model routing** - Use capable models (Sonnet) for analysis, efficient models (Haiku) for transcription
3. **Human-in-the-loop** - All changes require approval before application
4. **Surgical edits** - Changes applied via search-replace, not full rewrites
5. **Graceful degradation** - Low-confidence items flagged for manual review but don't block completion

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PIPELINE FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   PDF Upload                                                                │
│       │                                                                     │
│       ▼                                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │  PHASE 1: ANALYSIS (Sonnet 4.5)                                     │  │
│   │  All page images → DocumentManifest + HeadingTree + Observations    │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │  PHASE 2: EXTRACTION (Haiku)                                        │  │
│   │  Page images + Manifest + HeadingTree → Markdown (v0)               │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │  PHASE 3: SPECIALIZED ANALYSIS (Sonnet 4.5, per relevant page)      │  │
│   │  Agents: Figures, Tables, Structure, Typography                     │  │
│   │  Output: Additional Observations                                    │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │  PHASE 4: CONSOLIDATION (Sonnet 4.5)                                │  │
│   │  Observations → Proposals (search/replace diffs)                    │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │  PHASE 5: HUMAN REVIEW                                              │  │
│   │  Accept / Reject / Edit proposals                                   │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │  PHASE 6: APPLICATION                                               │  │
│   │  Apply approved edits via search-replace                            │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│       │                                                                     │
│       ▼                                                                     │
│   Remediated Document                                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Analysis (Sonnet 4.5)

### Purpose

Perform deep semantic analysis of the document to:
- Understand document structure and layout
- Identify features on each page (images, tables, lists, etc.)
- Detect initial accessibility observations
- Route downstream agents efficiently

### Input

- All page images (PNG, base64-encoded)
- Total page count

### Output

#### DocumentManifest

```python
class PageFeatures(BaseModel):
    """Features detected on a single page."""
    page_num: int

    # Content detection
    has_images: bool
    image_count: int
    has_tables: bool
    table_count: int
    has_lists: bool
    has_code_blocks: bool
    has_math: bool

    # Layout
    layout_type: Literal["single_column", "two_column", "mixed"]
    has_headers_footers: bool

    # Complexity assessment (0-1)
    complexity_score: float
    complexity_factors: list[str]  # ["dense tables", "nested lists", etc.]

class DocumentManifest(BaseModel):
    """Complete document analysis from Phase 1."""

    # Structure
    heading_tree: HeadingTree
    total_pages: int
    document_type: str  # "syllabus", "lecture_notes", "exam", etc.

    # Per-page analysis
    page_features: list[PageFeatures]

    # Agent routing
    required_agents: list[str]  # ["figures", "tables", "structure"]
    skip_agents: list[str]      # Agents not needed for this doc

    # Initial observations (from analysis phase)
    initial_observations: list[Observation]

    # Confidence
    analysis_confidence: float
    analysis_notes: str
```

#### HeadingTree (existing, enhanced)

```python
class HeadingNode(BaseModel):
    level: int              # 1-6
    title: str              # Heading text
    page: int               # Page number
    section_number: str | None

    # NEW: Observations about this heading
    observations: list[str]  # ["Level skip: H1 → H3", "Inconsistent with visual weight"]

class HeadingTree(BaseModel):
    document_title: str
    title_page: int
    sections: list[HeadingNode]
    total_pages: int
    layout_type: str
    confidence: float
    observations: str
```

### Model Selection Rationale

**Sonnet 4.5** is used for Phase 1 because:
- Requires reasoning about document semantics
- Must make judgments about complexity and routing
- Small output (manifest JSON), high reasoning requirement
- Initial observations require understanding of accessibility issues

---

## Phase 2: Extraction (Haiku)

### Purpose

Generate initial markdown transcription guided by Phase 1 analysis.

### Input

- All page images
- DocumentManifest from Phase 1
- HeadingTree from Phase 1

### Output

```python
class DocumentMarkdown(BaseModel):
    markdown: str           # Complete document markdown
    confidence: float       # Transcription confidence
    observations: str       # Notes about transcription decisions
```

### Model Selection Rationale

**Haiku** is used for Phase 2 because:
- Transcription is mechanical, not analytical
- HeadingTree provides structure guidance (decisions already made)
- High token output (full document text)
- Cost efficiency: Haiku is ~10x cheaper than Sonnet for output tokens

### Prompt Strategy

The Haiku prompt receives rich context from Sonnet's analysis:

```
You are transcribing a PDF document to accessible markdown.

DOCUMENT STRUCTURE (from analysis):
{heading_tree_formatted}

LAYOUT TYPE: {layout_type}
DOCUMENT TYPE: {document_type}

PAGE-BY-PAGE FEATURES:
- Page 1: 2 images, 0 tables, single column
- Page 2: 0 images, 1 table, single column
...

TRANSCRIPTION GUIDELINES:
1. Follow the heading structure exactly as analyzed
2. Mark images with placeholder: ![TODO: describe](image-page-X-N.png)
3. Preserve table structure as markdown tables
4. Maintain reading order as determined by layout analysis

[Page images follow]
```

---

## Phase 3: Specialized Analysis (Sonnet 4.5)

### Purpose

Run specialized agents on relevant pages to generate detailed observations.

### Agent Routing

Agents are queued based on `DocumentManifest.required_agents` and `page_features`:

| Agent | Triggered When | Pages Processed |
|-------|---------------|-----------------|
| FiguresAgent | `has_images: true` on any page | Only pages with images |
| TablesAgent | `has_tables: true` on any page | Only pages with tables |
| StructureAgent | Always (lightweight check) | All pages |
| TypographyAgent | `complexity_score > 0.5` | High-complexity pages |

### Agent Input

Each agent receives targeted context:

```python
class AgentContext(BaseModel):
    """Context passed to specialized agents."""
    job_id: str

    # Page-specific
    page_num: int
    page_image_base64: str

    # Document context
    current_markdown: str
    heading_tree: HeadingTree
    page_features: PageFeatures

    # Relevant markdown section (extracted for this page)
    page_markdown_section: str | None
```

### Agent Output

All agents produce observations:

```python
class Observation(BaseModel):
    """A discrepancy between visual presentation and semantic markup."""

    id: str                     # UUID
    job_id: str
    agent: str                  # "figures", "tables", "structure", "typography"

    # What the AI perceived
    visual_description: str     # "Image shows a flowchart with 5 connected boxes"
    markup_description: str     # "Image has empty alt text"

    # Location
    page_num: int
    location_type: Literal["element", "range", "region"]
    location_value: str         # CSS selector, line range, or region description

    # Assessment
    confidence: float           # 0-1
    severity: Literal["critical", "major", "minor"]

    # Routing
    route: Literal["auto", "manual"]
    manual_reason: str | None   # Why this needs human judgment

    # Lifecycle
    status: Literal["open", "resolved", "wont_fix", "manual"]
    resolved_by: str | None     # Proposal ID

    # Metadata
    created_at: datetime
    source: Literal["agent", "human"]
```

### Specialized Agents

#### FiguresAgent (#24)

**Focus:** Image accessibility

**Observations generated:**
- Missing alt text
- Alt text doesn't match visual content
- Decorative image marked as informative (or vice versa)
- Complex image needs long description
- Image contains text that should be transcribed

**Output example:**
```json
{
  "visual_description": "Flowchart showing student registration process with 5 steps: Apply → Review → Accept → Enroll → Confirm",
  "markup_description": "Image has alt text: 'registration flowchart'",
  "confidence": 0.9,
  "severity": "major",
  "route": "auto"
}
```

#### TablesAgent (#24)

**Focus:** Table structure and data accuracy

**Observations generated:**
- Table headers not marked
- Complex table structure (merged cells) may be lossy
- Data in markdown doesn't match PDF visual
- Table would benefit from caption
- Table is presentational, should be reformatted

**Output example:**
```json
{
  "visual_description": "Grade distribution table with merged header cells spanning 'Fall' and 'Spring' semesters",
  "markup_description": "Simple markdown table without colspan support, header structure lost",
  "confidence": 0.7,
  "severity": "major",
  "route": "manual",
  "manual_reason": "Complex merged cell structure requires human judgment on best representation"
}
```

#### StructureAgent (#23)

**Focus:** Heading hierarchy and reading order

**Observations generated:**
- Heading level skipped (H1 → H3)
- Visual heading not marked as heading
- Reading order incorrect for multi-column layout
- List structure not preserved
- Section breaks missing

#### TypographyAgent (#23)

**Focus:** Semantic meaning from visual styling

**Observations generated:**
- Bold text conveys emphasis not marked
- Italic indicates term/definition
- Color-coding conveys meaning
- Font size changes indicate structure not captured
- Callout boxes not semantically marked

---

## Phase 4: Consolidation (Sonnet 4.5)

### Purpose

Group related observations into actionable proposals with search-replace diffs.

### Input

- All observations from Phase 1 + Phase 3
- Current markdown

### Output

```python
class SearchReplaceDiff(BaseModel):
    """A single search-replace edit."""
    search: str     # Exact text to find
    replace: str    # Text to substitute

class Proposal(BaseModel):
    """A suggested edit resolving one or more observations."""

    id: str                         # UUID
    job_id: str

    # What this addresses
    resolves: list[str]             # Observation IDs

    # The edit
    diff: SearchReplaceDiff

    # Justification (critical for human review)
    justification: str              # Why these observations are grouped,
                                    # why this edit addresses them

    # Routing
    route: Literal["auto", "manual"]

    # Lifecycle
    status: Literal["pending", "approved", "rejected", "applied", "failed"]
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_notes: str | None

    # Metadata
    created_at: datetime
    estimated_impact: str           # "Adds alt text to 3 images"
```

### Consolidation Logic

The consolidation agent:

1. **Groups related observations**
   - Same page region
   - Overlapping edit locations
   - Semantically related (e.g., heading + its following content)

2. **Generates minimal diffs**
   - Smallest search string that uniquely identifies location
   - Complete replacement including context

3. **Writes justifications**
   - Explains why observations are grouped
   - Describes how the edit resolves them
   - Notes any tradeoffs or assumptions

4. **Routes to manual when uncertain**
   - Conflicting observations
   - Low confidence on any grouped observation
   - Complex structural changes

### Example Consolidation

**Input observations:**
1. "Image on page 3 has empty alt text"
2. "Image appears to be a flowchart showing course prerequisites"

**Output proposal:**
```json
{
  "resolves": ["obs-1", "obs-2"],
  "diff": {
    "search": "![](images/figure-3-1.png)",
    "replace": "![Flowchart showing CS 101 as prerequisite for CS 201, which leads to CS 301 and CS 302](images/figure-3-1.png)"
  },
  "justification": "Combining empty alt text observation with visual content analysis. The flowchart shows course prerequisites which is informational content requiring description.",
  "route": "auto"
}
```

---

## Phase 5: Human Review

### Purpose

Present proposals for human approval before applying changes.

### Interface Design

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ACCESSIBILITY REVIEW: CS101_Syllabus.pdf                               │
│  Status: Awaiting Review | 8 proposals | 2 manual items                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  AUTO-FIXABLE PROPOSALS (6)                              [Apply All]    │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  □ Proposal 1: Add alt text to course logo                              │
│    Page 1 | Resolves: 1 observation | Confidence: 0.95                  │
│    ┌─────────────────────────────────────────────────────────────────┐  │
│    │ - ![](images/logo.png)                                          │  │
│    │ + ![University of Illinois Chicago logo](images/logo.png)       │  │
│    └─────────────────────────────────────────────────────────────────┘  │
│    Justification: Logo is decorative but identifies the institution.   │
│    [Accept] [Reject] [Edit]                                             │
│                                                                         │
│  □ Proposal 2: Fix heading hierarchy                                    │
│    Page 2-3 | Resolves: 2 observations | Confidence: 0.88              │
│    ┌─────────────────────────────────────────────────────────────────┐  │
│    │ - ## Course Objectives                                          │  │
│    │ + ### Course Objectives                                         │  │
│    └─────────────────────────────────────────────────────────────────┘  │
│    Justification: H2 follows H1 title, but visual weight suggests H3.  │
│    [Accept] [Reject] [Edit]                                             │
│                                                                         │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  NEEDS MANUAL ATTENTION (2)                              [Optional]     │
│  These items are flagged for human judgment and won't block completion  │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                         │
│  ○ Complex table on page 5                                              │
│    Observation: Table has merged cells spanning multiple columns        │
│    AI Note: "Unable to determine best markdown representation"          │
│    [Provide Fix] [Skip]                                                 │
│                                                                         │
│  ○ Ambiguous image on page 7                                            │
│    Observation: Image may be decorative or informative                  │
│    AI Note: "Background pattern could be meaningful or decorative"      │
│    [Provide Fix] [Skip]                                                 │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  [Complete Review]                                                      │
│  6 proposals accepted | 0 rejected | 2 manual items skipped             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Human Actions

| Action | Result |
|--------|--------|
| **Accept** | Proposal status → `approved`, queued for application |
| **Reject** | Proposal status → `rejected`, observations remain `open` |
| **Edit** | Opens edit dialog (see below) |
| **Apply All** | Batch-accepts all auto-routed proposals |
| **Skip** (manual) | Observation status → `wont_fix`, doesn't block completion |
| **Provide Fix** | Opens edit dialog for manual items |

### Edit Dialog

When human selects "Edit" or "Provide Fix":

```
┌─────────────────────────────────────────────────────────────────────────┐
│  EDIT PROPOSAL                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Original observation:                                                  │
│  "Image shows flowchart but alt text is empty"                          │
│                                                                         │
│  Before (required):                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ ![](images/figure-3-1.png)                                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  After (optional - leave blank for AI to infer):                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ ![Course prerequisite flowchart](images/figure-3-1.png)         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  Comment (helps AI if After is blank):                                  │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ This flowchart shows prerequisites, not process flow            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  [Submit]  [Cancel]                                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

### Edit Processing

| Human provides | System behavior |
|----------------|-----------------|
| Before + After | Create Proposal directly, auto-approve, apply |
| Before + Comment (no After) | Create Observation → run consolidation → present new Proposal |

This allows humans to either:
1. Provide exact fixes (fast path)
2. Give guidance and let AI generate the fix (collaborative path)

---

## Phase 6: Application

### Purpose

Apply approved proposals to generate the remediated document.

### Application Strategy

Based on research into AI coding tools (Aider, Claude Code, Cursor), the system uses **layered matching**:

1. **Exact match** - Find search string character-for-character
2. **Whitespace-insensitive** - Normalize whitespace, try again
3. **Fuzzy match** - Use difflib for approximate matching (future)

### Application Flow

```python
async def apply_proposals(job_id: str, proposals: list[Proposal]) -> ApplicationResult:
    """Apply approved proposals to markdown."""

    markdown = await load_current_markdown(job_id)
    applied = []
    failed = []

    for proposal in proposals:
        if proposal.status != "approved":
            continue

        # Try exact match first
        if proposal.diff.search in markdown:
            markdown = markdown.replace(
                proposal.diff.search,
                proposal.diff.replace,
                1  # Only first occurrence
            )
            proposal.status = "applied"
            applied.append(proposal)

            # Mark observations as resolved
            for obs_id in proposal.resolves:
                await update_observation_status(obs_id, "resolved", proposal.id)
        else:
            # Exact match failed
            proposal.status = "failed"
            proposal.failure_reason = "Search text not found in document"
            failed.append(proposal)

    # Validate result
    lint_errors = await validate_markdown(markdown)

    if lint_errors:
        # Surface errors but don't block
        logger.warning(f"Lint errors after application: {lint_errors}")

    # Save new version
    await save_markdown_version(job_id, markdown)

    return ApplicationResult(
        applied_count=len(applied),
        failed_count=len(failed),
        failed_proposals=failed,
        lint_errors=lint_errors
    )
```

### Failure Handling

When a proposal fails to apply:

1. Mark proposal as `failed` with reason
2. Surface to user in completion summary
3. Original observations remain `open`
4. Don't block job completion

---

## Data Storage

### S3 Structure

```
results-bucket/
└── {job_id}/
    ├── output.md                 # Current markdown (latest version)
    ├── output-v0.md              # Original extraction (before remediation)
    ├── manifest.json             # DocumentManifest from Phase 1
    ├── observations.json         # All observations
    ├── proposals.json            # All proposals with status
    ├── application-log.json      # Audit trail of applied changes
    └── images/
        └── figure-1.png          # Extracted images
```

### Redis Structure

Job hash additions:

```
eq-pdf:job:{job_id}
├── ... (existing fields) ...
├── substatus: "analyzing" | "extracting" | "awaiting_review" | "applying"
├── observation_count: "12"
├── proposal_count: "8"
├── pending_proposals: "6"
├── manual_observations: "2"
├── analysis_model: "claude-sonnet-4-5"
├── extraction_model: "claude-haiku-4-5"
└── remediation_started_at: "2024-12-10T10:30:00Z"
```

---

## Model Cost Optimization

### Token Flow Estimate (10-page document)

| Phase | Model | Input Tokens | Output Tokens | Est. Cost |
|-------|-------|--------------|---------------|-----------|
| 1. Analysis | Sonnet 4.5 | ~50,000 (images) | ~2,000 (manifest) | $0.18 |
| 2. Extraction | Haiku | ~52,000 (images + manifest) | ~8,000 (markdown) | $0.07 |
| 3. Specialized | Sonnet 4.5 | ~15,000 (subset of pages) | ~1,000 (observations) | $0.06 |
| 4. Consolidation | Sonnet 4.5 | ~10,000 (observations + md) | ~1,500 (proposals) | $0.05 |
| **Total** | | | | **~$0.36** |

### Comparison to Current Approach

Current (Haiku for both phases): ~$0.15/doc
Proposed (Sonnet analysis + Haiku extraction): ~$0.36/doc

**Cost increase:** ~2.4x

**Value gained:**
- Smarter initial observations
- Better agent routing (skip unnecessary work)
- Higher-quality proposals
- Reduced human review time

---

## Job State Machine

```
                                    ┌─────────────┐
                                    │ pii_scanning │
                                    └──────┬──────┘
                                           │
                         ┌─────────────────┴─────────────────┐
                         │                                   │
                         ▼                                   ▼
              ┌──────────────────┐                  ┌─────────────┐
              │ awaiting_approval │                  │  processing │
              │   (PII found)     │                  │             │
              └────────┬─────────┘                  └──────┬──────┘
                       │                                   │
                       │ approved                          │
                       └──────────────┬────────────────────┘
                                      │
                                      ▼
                            ┌─────────────────┐
                            │   processing    │
                            │ ─────────────── │
                            │ substatus:      │
                            │ • analyzing     │
                            │ • extracting    │
                            │ • specializing  │
                            │ • consolidating │
                            │ • awaiting_review│
                            │ • applying      │
                            └────────┬────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
           ┌─────────────┐                   ┌─────────────┐
           │  completed  │                   │   failed    │
           └─────────────┘                   └─────────────┘
```

---

## API Changes

### New Endpoints

```
GET  /api/documents/{job_id}/observations
     Returns all observations for a job

GET  /api/documents/{job_id}/proposals
     Returns all proposals for a job

POST /api/documents/{job_id}/proposals/{proposal_id}/approve
     Approve a proposal

POST /api/documents/{job_id}/proposals/{proposal_id}/reject
     Reject a proposal

POST /api/documents/{job_id}/proposals/{proposal_id}/edit
     Submit human edit (before, after?, comment)

POST /api/documents/{job_id}/apply
     Apply all approved proposals

GET  /api/documents/{job_id}/review
     Get review page data (observations, proposals, current markdown)
```

### Enhanced Status Response

```json
{
  "job_id": "abc-123",
  "status": "processing",
  "substatus": "awaiting_review",
  "created_at": "2024-12-10T10:30:00Z",
  "updated_at": "2024-12-10T10:35:00Z",

  "extraction": {
    "completed_at": "2024-12-10T10:32:00Z",
    "markdown_url": "s3://...",
    "total_pages": 10,
    "analysis_model": "claude-sonnet-4-5",
    "extraction_model": "claude-haiku-4-5"
  },

  "remediation": {
    "observation_count": 12,
    "proposal_count": 8,
    "pending_proposals": 6,
    "approved_proposals": 0,
    "rejected_proposals": 0,
    "manual_observations": 2,
    "review_url": "/review/abc-123"
  },

  "cost": {
    "total_cents": 36.2,
    "analysis_cents": 18.0,
    "extraction_cents": 7.0,
    "specialized_cents": 6.0,
    "consolidation_cents": 5.2
  }
}
```

---

## Implementation Phases

### Phase 1: Foundation (Issues #23, #24 prerequisite)

1. Add `substatus` to job model
2. Create Observation and Proposal Pydantic models
3. Add S3 storage for observations/proposals JSON
4. Implement model switching (Sonnet/Haiku) in agent framework

### Phase 2: Analysis Enhancement

1. Extend ExtractionAgent output to include DocumentManifest
2. Split into Analysis (Sonnet) + Extraction (Haiku) phases
3. Store manifest to S3

### Phase 3: Specialized Agents (#23, #24)

1. Implement FiguresAgent
2. Implement TablesAgent
3. Implement StructureAgent
4. Implement TypographyAgent
5. Add agent routing based on manifest

### Phase 4: Consolidation

1. Implement ConsolidationAgent
2. Group observations into proposals
3. Generate search-replace diffs

### Phase 5: Human Review

1. Build review API endpoints
2. Implement proposal approval workflow
3. Handle human edits (before/after/comment)

### Phase 6: Application

1. Implement search-replace application
2. Add markdown validation
3. Version tracking for applied changes

---

## Open Questions

1. **Iteration limit** - How many times can humans request re-consolidation?
2. **Batch vs. incremental** - Apply proposals one-by-one or batch?
3. **Conflict resolution** - What if two proposals touch overlapping text?
4. **Rollback** - Can users undo applied proposals?
5. **Learning** - Should we track rejection patterns to improve agents?

---

## References

- [Aider Edit Formats](https://aider.chat/docs/more/edit-formats.html) - Search-replace research
- [Claude Code Architecture](https://docs.anthropic.com/en/docs/claude-code) - Text editor tool patterns
- [WCAG 2.1 Guidelines](https://www.w3.org/WAI/WCAG21/quickref/) - Accessibility criteria
