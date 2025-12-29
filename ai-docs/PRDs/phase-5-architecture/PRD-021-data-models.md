# PRD-021: Data Models

## Status: ✅ COMPLETE (2024-12-17)

Core data models implemented. Summary agent integration deferred to PRD-022+.

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: 5 - Architecture
**Estimated Effort**: 2 days
**Dependencies**: None (foundational)
**Reference**: [PRD-020](./PRD-020-3-phase-architecture.md)

## Problem Statement

The current data models are designed around the observation → consolidation → proposal flow. The new 4-phase architecture requires:

1. **ProcessingResult**: Complete output with glass box transparency
2. **AgentTrace**: Per-agent execution trace
3. **ReviewItem/ReviewChecklist**: Human review interface (multiple choice + free input)
4. **DocumentSummary**: Context passed to all agents
5. **AutoCorrection**: Safe automatic edits

## Success Criteria

- [x] All new models defined with Pydantic validation
- [x] Models support JSON serialization for API
- [x] Observation model updated with new state machine
- [x] ReviewItem supports multiple choice with "Other" option
- [x] ProcessingResult contains full glass box trace

## Breaking Changes (Hard Cut)

This is a **hard cut** - no deprecation period. The following are removed entirely:

### Files to Delete
```
src/shared/models/proposal.py           # Replaced by AutoCorrection
src/agents/consolidation/               # Entire directory removed
src/agents/consolidation/grouping_agent.py
src/agents/consolidation/conflict_agent.py
src/agents/consolidation/proposal_agent.py
```

### Models Removed
- `Proposal` - replaced by `AutoCorrection`
- `SearchReplaceDiff` - fields moved into `AutoCorrection`
- `ObservationGroup` - no longer needed (no consolidation)
- `ConflictReport` - no longer needed
- `ProposalDraft` - no longer needed
- `RoutedProposal` - no longer needed

## Technical Requirements

### New Files to Create

```
src/shared/models/
├── processing_result.py    # ProcessingResult, ProcessingTrace
├── agent_trace.py          # AgentTrace, AgentResult
├── review_checklist.py     # ReviewChecklist, ReviewItem, ReviewOption
├── document_context.py     # DocumentSummary, ObservationContext
├── auto_correction.py      # AutoCorrection model
```

### Model Definitions

#### ProcessingResult (Top-level API Output)

```python
# src/shared/models/processing_result.py

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

class ProcessingResult(BaseModel):
    """Complete result of document processing - exposed via API."""

    job_id: str
    status: Literal["completed", "needs_review", "failed"]

    # The outputs
    markdown: str = Field(description="Final markdown with auto-corrections applied")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence score")

    # Glass box: Full transparency
    processing_trace: "ProcessingTrace"

    # Human review interface
    review_checklist: "ReviewChecklist"

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processing_time_seconds: float


class ProcessingTrace(BaseModel):
    """Glass box: Everything that happened during processing."""

    # Phase summaries
    analysis: "AnalysisSummary"    # Phase 1: Analyze
    extraction: "ExtractionSummary"  # Phase 2: Extract
    structure: "StructureSummary"    # Phase 3a: Refine (structure loop)

    # Per-agent results (Phase 3b: Refine - specialized agents)
    agents: list["AgentTrace"]

    # Aggregate stats
    total_observations: int
    auto_corrections_applied: int
    review_items_generated: int
    total_cost_cents: float
    total_time_seconds: float
    total_tokens: int


class AnalysisSummary(BaseModel):
    """Summary of analysis phase."""
    document_type: str
    total_pages: int
    key_entities: list[str]
    required_agents: list[str]
    confidence: float
    time_seconds: float
    cost_cents: float


class ExtractionSummary(BaseModel):
    """Summary of extraction phase."""
    confidence: float
    pages_extracted: int
    correction_iterations: int
    time_seconds: float
    cost_cents: float


class StructureSummary(BaseModel):
    """Summary of structure verification loop (Phase 3a: Refine)."""
    iterations: int
    lint_issues_found: int
    lint_issues_fixed: int
    ocr_suggestions_processed: int
    corrections_applied: int
    final_lint_clean: bool
    time_seconds: float
    cost_cents: float
```

#### AgentTrace (Per-Agent Output)

```python
# src/shared/models/agent_trace.py

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

from .observation import Observation
from .auto_correction import AutoCorrection
from .review_checklist import ReviewItem


class AgentTrace(BaseModel):
    """What one agent did - full transparency."""

    agent_name: Literal["figures", "tables", "structure", "typography"]

    # What it saw
    observations: list[Observation] = Field(default_factory=list)

    # What it decided
    auto_corrections: list[AutoCorrection] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)

    # Its reasoning (glass box)
    reasoning_summary: str = Field(
        description="Human-readable summary of what agent did"
    )

    # Metrics
    confidence: float = Field(ge=0.0, le=1.0)
    cost_cents: float = Field(ge=0.0)
    time_seconds: float = Field(ge=0.0)
    iterations: int = Field(default=1, description="For agents with validation loops")

    # Tracking
    started_at: datetime
    completed_at: datetime


class AgentResult(BaseModel):
    """Standard output format for all specialized agents."""

    agent_name: str
    observations: list[Observation] = Field(default_factory=list)
    auto_corrections: list[AutoCorrection] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    reasoning_summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    enhanced_content: dict[str, str] | None = Field(
        default=None,
        description="placeholder_id -> enhanced content"
    )

    # For conversion to AgentTrace
    cost_cents: float = 0.0
    time_seconds: float = 0.0
    iterations: int = 1
```

#### AutoCorrection

```python
# src/shared/models/auto_correction.py

from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class AutoCorrection(BaseModel):
    """A correction safe to apply automatically."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    observation_id: str = Field(description="Links to the observation this fixes")

    # The edit
    search: str = Field(min_length=1, description="Text to find in markdown")
    replace: str = Field(description="Text to replace with (can be empty for deletion)")

    # Glass box
    justification: str = Field(
        min_length=1,
        description="Why this is safe to auto-apply"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Should be high (>0.95) for auto-corrections"
    )

    # Tracking
    applied: bool = False
    applied_at: datetime | None = None

    # Source tracking
    agent: str = Field(description="Which agent generated this")
    page_num: int | None = None
```

#### ReviewChecklist and ReviewItem

```python
# src/shared/models/review_checklist.py

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
import uuid


class ReviewOption(BaseModel):
    """One option in a review item (multiple choice)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str = Field(description="Display text for this option")
    action: Literal["replace", "keep", "skip", "other"] = Field(
        description="What happens if selected"
    )
    replacement_text: str | None = Field(
        default=None,
        description="For 'replace' actions, the text to use"
    )
    is_recommended: bool = Field(
        default=False,
        description="Agent's top choice (shown first in UI)"
    )


class ReviewItem(BaseModel):
    """Item needing human decision - multiple choice with free input."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    observation_id: str = Field(description="Links to the observation")
    agent: str = Field(description="Which agent generated this")

    # NOTE: category is NOT on ReviewItem - it's derived from linked Observation
    # at checklist construction time via from_items_and_observations()

    # The question
    question: str = Field(description="Human-readable question")

    # Multiple choice options (2-4 options, "Other" implicit)
    options: list[ReviewOption] = Field(
        min_length=2,
        max_length=4,
        description="Predefined choices"
    )

    # For applying replacements (populated by agents when creating ReviewItem)
    search_text: str = Field(
        description="Exact text to find in markdown for replacement"
    )

    # Context for decision
    context: str = Field(description="Surrounding text ~500 chars")
    page_num: int
    visual_context_url: str | None = Field(
        default=None,
        description="S3 URL to page image snippet"
    )

    # Agent's recommendation (glass box)
    agent_recommendation: str = Field(description="What agent thinks should happen")
    agent_confidence: float = Field(ge=0.0, le=1.0)

    # Human's decision (filled after review)
    selected_option_id: str | None = None
    custom_input: str | None = Field(
        default=None,
        description="If 'Other' selected, the custom text"
    )
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class ReviewChecklist(BaseModel):
    """Human review interface - exposed via API."""

    # All items needing review
    items: list[ReviewItem] = Field(default_factory=list)

    # Quick summary
    summary: str = Field(description="e.g., '5 items need review: 2 figures, 2 OCR, 1 table'")

    # Grouped for UI navigation
    # NOTE: by_category is derived from linked Observations at construction time
    by_category: dict[str, list[ReviewItem]] = Field(default_factory=dict)
    by_agent: dict[str, list[ReviewItem]] = Field(default_factory=dict)
    by_page: dict[int, list[ReviewItem]] = Field(default_factory=dict)

    # Stats
    total_items: int = 0
    critical_items: int = Field(
        default=0,
        description="Items with confidence < 0.7"
    )
    completed_items: int = 0

    @classmethod
    def from_items_and_observations(
        cls,
        items: list[ReviewItem],
        observations: list["Observation"],
    ) -> "ReviewChecklist":
        """Build checklist from items with category derived from linked observations.

        Category is NOT stored on ReviewItem - it's derived at checklist construction
        by looking up the linked Observation. This ensures single source of truth.

        NOTE: Items whose linked Observation is already closed (status="closed") are
        excluded from the checklist. Closed observations don't need review - they've
        already been resolved (e.g., via auto-correction).
        """
        # Build observation lookup
        obs_by_id = {obs.id: obs for obs in observations}

        by_category: dict[str, list[ReviewItem]] = {}
        by_agent: dict[str, list[ReviewItem]] = {}
        by_page: dict[int, list[ReviewItem]] = {}
        included_items: list[ReviewItem] = []

        for item in items:
            # Look up linked observation
            obs = obs_by_id.get(item.observation_id)

            # Skip items whose observation is already closed (no review needed)
            if obs and obs.status == "closed":
                continue

            # Derive category from linked observation
            category = obs.category if obs else "unknown"

            by_category.setdefault(category, []).append(item)
            by_agent.setdefault(item.agent, []).append(item)
            by_page.setdefault(item.page_num, []).append(item)
            included_items.append(item)

        # Build summary
        parts = [f"{len(v)} {k}" for k, v in by_agent.items()]
        summary = f"{len(included_items)} items need review: {', '.join(parts)}"

        return cls(
            items=included_items,
            summary=summary,
            by_category=by_category,
            by_agent=by_agent,
            by_page=by_page,
            total_items=len(included_items),
            critical_items=sum(1 for i in included_items if i.agent_confidence < 0.7),
            completed_items=sum(1 for i in included_items if i.reviewed_at is not None),
        )
```

#### DocumentSummary (Context for Agents)

```python
# src/shared/models/document_context.py

from pydantic import BaseModel, Field

from .remediation import HeadingTree


class DocumentSummary(BaseModel):
    """Context passed to all downstream agents."""

    # Basic info
    title: str
    document_type: str = Field(description="research_paper, syllabus, exam, etc.")

    # Semantic context
    topic_summary: str = Field(description="1-2 sentences about content")
    structure_summary: str = Field(description="e.g., '9 pages, two-column, abstract + 6 sections'")

    # Key terms for OCR detection and context
    key_entities: list[str] = Field(
        default_factory=list,
        description="Names, projects, technical terms: ['yt', 'Enzo', 'Turk']"
    )
    domain_terms: list[str] = Field(
        default_factory=list,
        description="Domain vocabulary: ['parallelization', 'MPI', 'OpenMP']"
    )

    # Expectations
    expected_elements: list[str] = Field(
        default_factory=list,
        description="e.g., ['abstract', 'references', 'figures']"
    )
    audience_level: str = Field(
        default="general",
        description="academic, student, general"
    )


class ObservationContext(BaseModel):
    """Full context for processing a single observation."""

    from .observation import Observation

    observation: "Observation"
    document_summary: DocumentSummary
    heading_tree: HeadingTree

    # Visual context
    page_image_base64: str | None = Field(
        default=None,
        description="The specific page as base64 PNG"
    )

    # Textual context (trimmed, not full doc)
    markdown_excerpt: str = Field(description="~1000 chars centered on issue")
    before_context: str = Field(description="~500 chars before")
    after_context: str = Field(description="~500 chars after")

    # Location helpers
    page_num: int
    line_range: tuple[int, int] | None = None
```

### Enhancements to Existing Models

#### Observation Model Updates

The Observation model is updated with a **simplified 2-field lifecycle**.

**Key Changes:**
- `status`: Simplified to just `open` or `closed`
- `resolution`: Tracks the outcome (`fixed`, `kept_original`, `skipped`)
- `route`/`manual_reason`/`resolution_path`/`resolved_by`/`resolved_by_type`/`resolved_at`: All removed - ProcessingTrace captures the full story
- Added `close(resolution)` method for clean transitions

```python
# src/shared/models/observation.py (full updated model)

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field


class ObservationLocation(BaseModel):
    """Location reference for an observation."""
    location_type: Literal["element", "range", "region"] = "region"
    value: str = Field(..., min_length=1)
    page_num: int = Field(..., ge=1)


class Observation(BaseModel):
    """A discrepancy between visual presentation and semantic markup."""

    # Identification
    id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str

    # Source
    agent: str = Field(description="figures, tables, structure, typography")
    source: Literal["agent", "human"] = "agent"

    # What was observed
    visual_description: str = Field(..., min_length=1)
    markup_description: str = Field(..., min_length=1)

    # Location
    location: ObservationLocation
    affected_pages: list[int] = Field(
        default_factory=list,
        description="For issues spanning multiple pages"
    )

    # Assessment
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    severity: Literal["critical", "major", "minor"] = "major"

    # Category for grouping (single source of truth - ReviewItem derives from this)
    category: str = Field(
        default="general",
        description="reading_order, heading, ocr, formatting, alt_text, table, etc."
    )

    # SIMPLIFIED LIFECYCLE (2 fields only)
    # ProcessingTrace already captures who/when/how
    status: Literal["open", "closed"] = "open"
    resolution: Literal["fixed", "kept_original", "skipped"] | None = Field(
        default=None,
        description="What was the outcome? Only set when status='closed'"
    )

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    human_comment: str | None = None

    def close(self, resolution: Literal["fixed", "kept_original", "skipped"]) -> None:
        """Close this observation with the given resolution.

        Args:
            resolution: The outcome
                - "fixed": Correction was applied (auto or human)
                - "kept_original": Human chose to keep as-is
                - "skipped": Force-applied with unreviewed items
        """
        if self.status == "closed":
            raise ValueError("Observation already closed")
        self.status = "closed"
        self.resolution = resolution
```

#### State Machine Design

**Simplified to 2 Fields:**

The observation just needs to know "am I done?" and "what was the outcome?" Everything else (who, when, how) is captured in `ProcessingTrace`.

| Field | Values | Description |
|-------|--------|-------------|
| `status` | `open`, `closed` | Is this observation resolved? |
| `resolution` | `fixed`, `kept_original`, `skipped` | What was the outcome? (only when closed) |

**Resolution Values:**
| Resolution | Meaning |
|------------|---------|
| `fixed` | Correction was applied (via AutoCorrection or ReviewItem) |
| `kept_original` | Human reviewed and chose to keep original |
| `skipped` | Force-applied reviews with this item unreviewed |

**Usage:**
```python
# When AutoCorrection is applied
obs.close("fixed")

# When human reviews and accepts fix
obs.close("fixed")

# When human reviews and keeps original
obs.close("kept_original")

# When force-applying with unreviewed items
obs.close("skipped")
```

#### Category Field Design Decision

`category` is ONLY on `Observation`. It is NOT on `ReviewItem`.

- `Observation.category`: Set when observation is created (single source of truth)
- `ReviewChecklist.by_category`: Derived at checklist construction by looking up linked observations

This ensures no data duplication and no sync risk. When filtering by category in the API, use the pre-computed `checklist.by_category.get(category, [])`.

#### DocumentManifest Updates

```python
# src/shared/models/remediation.py (additions)

class DocumentManifest(BaseModel):
    # ... existing fields ...

    # NEW: Document summary for downstream agents
    summary: DocumentSummary | None = Field(
        default=None,
        description="Generated during analysis for downstream context"
    )
```

## API Schema Examples

### ProcessingResult Response

```json
{
  "job_id": "abc-123",
  "status": "needs_review",
  "markdown": "# Document Title\n\n...",
  "confidence": 0.87,
  "processing_trace": {
    "analysis": {
      "document_type": "research_paper",
      "total_pages": 9,
      "key_entities": ["yt", "Enzo", "DVCS"],
      "required_agents": ["figures", "tables", "typography"],
      "confidence": 0.95,
      "time_seconds": 12.5,
      "cost_cents": 5.8
    },
    "extraction": {
      "confidence": 0.92,
      "pages_extracted": 9,
      "correction_iterations": 1,
      "time_seconds": 45.2,
      "cost_cents": 8.4
    },
    "structure": {
      "iterations": 2,
      "lint_issues_found": 5,
      "lint_issues_fixed": 5,
      "ocr_suggestions_processed": 3,
      "corrections_applied": 2,
      "final_lint_clean": true,
      "time_seconds": 18.3,
      "cost_cents": 4.2
    },
    "agents": [
      {
        "agent_name": "figures",
        "observations": [...],
        "auto_corrections": [...],
        "review_items": [...],
        "reasoning_summary": "Processed 3 images. 2 auto-corrected, 1 needs review.",
        "confidence": 0.88,
        "cost_cents": 12.5,
        "time_seconds": 25.0,
        "iterations": 1
      }
    ],
    "total_observations": 8,
    "auto_corrections_applied": 5,
    "review_items_generated": 3,
    "total_cost_cents": 42.5,
    "total_time_seconds": 120.0,
    "total_tokens": 45000
  },
  "review_checklist": {
    "items": [...],
    "summary": "3 items need review: 1 figures, 2 typography",
    "by_category": {...},
    "by_agent": {...},
    "by_page": {...},
    "total_items": 3,
    "critical_items": 0,
    "completed_items": 0
  }
}
```

### ReviewItem Example

```json
{
  "id": "ri-456",
  "observation_id": "obs-789",
  "agent": "typography",
  "question": "Is 'Exxon' a typo for 'Enzo'?",
  "options": [
    {
      "id": "opt-1",
      "label": "Yes, replace with 'Enzo'",
      "action": "replace",
      "replacement_text": "Enzo",
      "is_recommended": true
    },
    {
      "id": "opt-2",
      "label": "No, 'Exxon' is correct",
      "action": "keep",
      "is_recommended": false
    }
  ],
  "search_text": "Exxon",
  "context": "...Both Exxon and yt are developed using...",
  "page_num": 3,
  "agent_recommendation": "This appears to be an OCR error. The document discusses the 'Enzo' project, not the oil company 'Exxon'.",
  "agent_confidence": 0.85,
  "selected_option_id": null,
  "custom_input": null,
  "reviewed_by": null,
  "reviewed_at": null
}
```

Note: `category` is NOT on ReviewItem. It's derived from the linked Observation at checklist construction time.

## Acceptance Criteria

### Model Validation
- [ ] All models pass Pydantic validation
- [ ] JSON serialization works correctly
- [ ] Nested models serialize properly
- [ ] Optional fields handle None correctly

### API Compatibility
- [ ] ProcessingResult can be returned from FastAPI endpoint
- [ ] ReviewChecklist.from_items_and_observations() works correctly
- [ ] by_category grouping derived from linked Observations
- [ ] DocumentSummary integrates with manifest

### Simplified Observation Lifecycle
- [ ] Observation has only 2 lifecycle fields: status, resolution
- [ ] close() method transitions status and sets resolution
- [ ] Raises error if already closed

### ReviewItem
- [ ] search_text field populated by agents
- [ ] category NOT on ReviewItem (derived at checklist level)

### Glass Box
- [ ] AgentTrace captures all agent decisions
- [ ] ProcessingTrace aggregates all 4 phases
- [ ] ReviewItem includes agent reasoning

### Summary Agent (see PRD-020 spec)
- [ ] summary_agent.py generates DocumentSummary
- [ ] Runs in parallel with headings_agent and features_agent
- [ ] Uses Haiku (EFFICIENT tier) for cost efficiency
- [ ] Outputs key_entities and domain_terms for OCR detection
- [ ] chained_analysis.py updated to call summary_agent
- [ ] DocumentManifest.summary field populated

## Deliverables

### Files to Create
```
src/shared/models/
├── processing_result.py
├── agent_trace.py
├── auto_correction.py
├── review_checklist.py
├── document_context.py

src/agents/
├── summary_agent.py           # NEW: Generate DocumentSummary (see PRD-020 spec)

config/agents/
├── summary.yaml               # NEW: Prompts for summary agent (see PRD-020 spec)

tests/unit/models/
├── test_processing_result.py
├── test_agent_trace.py
├── test_review_checklist.py
├── test_document_context.py
├── test_observation_lifecycle.py

tests/unit/agents/
├── test_summary_agent.py      # NEW: Tests for summary agent
```

**Note**: The `summary_agent.py` implementation is fully specified in PRD-020 "Phase 1: DocumentSummary Generation" section.

### Files to Modify
```
src/shared/models/observation.py     # Simplify to 2-field lifecycle
src/shared/models/remediation.py     # Add DocumentSummary model + summary field to manifest
src/shared/models/__init__.py        # Export new models, remove Proposal exports
src/agents/chained_analysis.py       # Add summary_agent call (parallel with headings/features)
```

### Files to Delete
```
src/shared/models/proposal.py
src/agents/consolidation/            # Entire directory
```

## Definition of Done

- [ ] All new models implemented
- [ ] Observation simplified to 2 lifecycle fields (status, resolution)
- [ ] close() method implemented
- [ ] ReviewItem has search_text field (no category)
- [ ] ReviewChecklist.from_items_and_observations() works
- [ ] Unit tests for each model including lifecycle
- [ ] JSON schema examples documented
- [ ] Old Proposal model and consolidation agents deleted
- [ ] Integrated with existing model exports
- [ ] summary_agent.py implemented and integrated with chained_analysis.py
- [ ] DocumentManifest includes populated summary field
- [ ] Unit tests for summary_agent passing
