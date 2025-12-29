# PRD-021: Dynamic Agent Instructions & Enhanced Field Guidance

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation (Agent Enhancement)
**Estimated Effort**: 2 days
**Dependencies**: PRD-018 (Infrastructure Consolidation), PRD-020 (Reasoning System)
**Source**: Agent Infrastructure Refactoring Plan (Phases 3, 4)

## Problem Statement

Current agent prompts are static YAML files with no runtime adaptation:

1. **No Manifest Context**: Extraction agent doesn't know the heading structure from analysis.
2. **No Failure Recovery**: When an agent fails, retry uses identical instructions.
3. **Static Field Descriptions**: Pydantic field descriptions don't guide LLM behavior optimally.
4. **No Job Context**: Agents don't know job history or document-specific patterns.

### Current State

```python
# Static prompt - same for every document
system_prompt = self.prompts["system_prompt"]

result = await agent.run(user_message)
```

### Target State

```python
# Dynamic instructions adapt to runtime context
@extraction_agent.instructions
async def manifest_guidance(ctx: RunContext[AgentDependencies]) -> str:
    if ctx.deps.manifest:
        return f"Follow this heading structure:\n{format_headings(ctx.deps.manifest)}"
    return ""

@extraction_agent.instructions
async def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
    if ctx.deps.previous_failures:
        return f"Previous attempts failed on: {ctx.deps.previous_failures}"
    return ""
```

## Success Criteria

- [ ] `AgentDependencies` dataclass for runtime context
- [ ] PydanticAI `deps_type` enabled on all agents
- [ ] Dynamic instructions for manifest-guided extraction
- [ ] Dynamic instructions for failure recovery
- [ ] Enhanced Pydantic field descriptions for LLM guidance
- [ ] Tests verify dynamic instruction injection

## Technical Requirements

### 1. Agent Dependencies Dataclass

```python
# src/agents/dependencies.py

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.job_service import JobService
    from src.shared.models.remediation import DocumentManifest


@dataclass
class AgentDependencies:
    """Runtime dependencies injected into agent instructions.

    Provides context that allows dynamic instruction generation
    based on job state, document analysis, and failure history.
    """

    job_id: str
    """Current job identifier."""

    job_service: "JobService | None" = None
    """Job service for state queries (optional, for advanced use)."""

    manifest: "DocumentManifest | None" = None
    """Document manifest from analysis phase (for extraction/specialized agents)."""

    previous_failures: list[str] = field(default_factory=list)
    """List of error messages from previous attempts (for retry guidance)."""

    attempt_number: int = 1
    """Current attempt number (1 = first try, 2+ = retry)."""

    document_type: str | None = None
    """Document type from analysis (syllabus, exam, etc.)."""

    custom_context: dict[str, str] = field(default_factory=dict)
    """Additional context for specialized use cases."""

    def add_failure(self, error_message: str) -> None:
        """Record a failure for retry guidance."""
        self.previous_failures.append(error_message)
        self.attempt_number += 1

    @property
    def is_retry(self) -> bool:
        """Check if this is a retry attempt."""
        return self.attempt_number > 1
```

### 2. Update Agent Creation with Dependencies

```python
# src/agents/base_agent.py (updated)

from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.dependencies import AgentDependencies


class BaseDocumentAgent(ABC, Generic[TOutput]):
    """Base class with dependency injection support."""

    def _create_agent(self) -> Agent[AgentDependencies, TOutput]:
        """Create agent with dependency injection enabled."""
        model = BedrockConverseModel(model_name=self._core.model_id)

        return Agent(
            model,
            deps_type=AgentDependencies,  # Enable dependency injection
            output_type=self.config.output_type,
            system_prompt=self._core.prompts["system_prompt"],
            retries=self.config.max_retries,
        )

    async def _run_with_deps(
        self,
        user_message: str | list,
        deps: AgentDependencies,
        **kwargs,
    ):
        """Run agent with injected dependencies."""
        agent = self._get_agent()
        return await agent.run(user_message, deps=deps, **kwargs)
```

### 3. Dynamic Instructions for Extraction Agent

```python
# src/agents/extraction_agent.py (updated)

from pydantic_ai import RunContext

from src.agents.dependencies import AgentDependencies


class ExtractionAgent(BaseDocumentAgent[ExtractionOutput]):
    """Extraction agent with dynamic manifest guidance."""

    def _create_agent(self) -> Agent[AgentDependencies, ExtractionOutput]:
        agent = super()._create_agent()

        # Register dynamic instructions
        @agent.instructions
        async def manifest_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Inject manifest heading structure into instructions."""
            if ctx.deps.manifest and ctx.deps.manifest.heading_tree:
                headings = self._format_heading_structure(ctx.deps.manifest)
                return (
                    "\n\n=== HEADING STRUCTURE (follow exactly) ===\n"
                    f"{headings}\n"
                    "Use these exact headings in your transcription.\n"
                )
            return ""

        @agent.instructions
        async def document_type_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Provide document-type-specific instructions."""
            doc_type = ctx.deps.document_type or "general"

            type_hints = {
                "syllabus": (
                    "This is a course syllabus. Pay attention to:\n"
                    "- Course schedule tables\n"
                    "- Grading policy sections\n"
                    "- Assignment due dates\n"
                ),
                "exam": (
                    "This is an exam document. Pay attention to:\n"
                    "- Question numbering\n"
                    "- Point values\n"
                    "- Answer spaces (preserve blanks)\n"
                ),
                "lecture_notes": (
                    "These are lecture notes. Pay attention to:\n"
                    "- Slide numbers/titles\n"
                    "- Bullet point hierarchies\n"
                    "- Diagrams and figures\n"
                ),
            }

            hint = type_hints.get(doc_type)
            if hint:
                return f"\n\n=== DOCUMENT TYPE: {doc_type.upper()} ===\n{hint}"
            return ""

        @agent.instructions
        async def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Provide guidance for retry attempts."""
            if not ctx.deps.is_retry:
                return ""

            failures = "\n".join(f"- {f}" for f in ctx.deps.previous_failures[-3:])
            return (
                f"\n\n=== RETRY ATTEMPT {ctx.deps.attempt_number} ===\n"
                f"Previous attempts failed with:\n{failures}\n\n"
                "Try alternative approaches to avoid these issues.\n"
            )

        return agent

    def _format_heading_structure(self, manifest: DocumentManifest) -> str:
        """Format heading tree for prompt injection."""
        lines = []
        for node in manifest.heading_tree.nodes:
            indent = "  " * (node.level - 1)
            prefix = f"H{node.level}:"
            lines.append(f"{indent}{prefix} {node.title} (page {node.page})")
        return "\n".join(lines) if lines else "No heading structure detected."
```

### 4. Dynamic Instructions for Specialized Agents

```python
# src/agents/figures_agent.py (updated)

class FiguresAgent(BaseDocumentAgent[FiguresAnalysisOutput]):
    """Figures agent with context-aware instructions."""

    def _create_agent(self) -> Agent[AgentDependencies, FiguresAnalysisOutput]:
        agent = super()._create_agent()

        @agent.instructions
        async def page_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide page-specific context from manifest."""
            if not ctx.deps.manifest:
                return ""

            # Get expected image count from manifest
            page_features = ctx.deps.custom_context.get("current_page_features")
            if page_features:
                return (
                    f"\n\n=== PAGE CONTEXT ===\n"
                    f"Expected images: {page_features.get('image_count', 'unknown')}\n"
                    f"Layout: {page_features.get('layout_type', 'unknown')}\n"
                )
            return ""

        @agent.instructions
        async def document_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide document-level context."""
            if ctx.deps.manifest:
                return (
                    f"\n\n=== DOCUMENT CONTEXT ===\n"
                    f"Title: {ctx.deps.manifest.document_title}\n"
                    f"Type: {ctx.deps.manifest.document_type}\n"
                    f"Total pages: {ctx.deps.manifest.total_pages}\n"
                )
            return ""

        return agent
```

### 5. Enhanced Pydantic Field Descriptions

Improve field descriptions to guide LLM output generation:

```python
# src/agents/analysis_agent.py (enhanced field descriptions)

class AnalysisPageFeatures(BaseModel):
    """Per-page feature detection with enhanced descriptions."""

    page_num: int = Field(
        ...,
        ge=1,
        description="1-indexed page number (first page is 1, not 0)"
    )

    has_images: bool = Field(
        default=False,
        description=(
            "True if page contains INFORMATIVE images requiring alt text. "
            "Includes: charts, diagrams, photos, screenshots with content. "
            "Excludes: decorative borders, backgrounds, logos, spacers."
        ),
    )

    image_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Count of INFORMATIVE images only (matching has_images criteria). "
            "If has_images=True, image_count must be >= 1. "
            "If has_images=False, image_count must be 0."
        ),
    )

    layout_type: Literal["single_column", "two_column", "mixed"] = Field(
        default="single_column",
        description=(
            "Layout for THIS PAGE ONLY (not the whole document). "
            "'single_column': Standard linear reading order, text flows top to bottom. "
            "'two_column': Side-by-side columns (common in academic papers). "
            "'mixed': ONLY if multiple layouts appear on the SAME page. "
            "Note: If page 1 is single-column and page 2 is two-column, "
            "each gets their own layout_type (not 'mixed')."
        ),
    )

    complexity_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Page complexity score for routing decisions. "
            "0.0 = Very simple (plain text, clear structure, no special elements). "
            "0.5 = Moderate (some tables/lists, standard formatting). "
            "1.0 = Very complex (nested tables, multi-column, dense figures, merged cells). "
            "Consider: table nesting depth, list hierarchy, image density, column count."
        ),
    )

    complexity_factors: list[str] = Field(
        default_factory=list,
        description=(
            "List specific factors contributing to complexity_score. "
            "Examples: 'dense tables', 'nested lists', 'multi-column layout', "
            "'complex images with labels', 'mathematical equations', 'merged table cells'. "
            "Empty list if complexity_score <= 0.3."
        ),
    )


class AnalysisObservation(BaseModel):
    """Initial accessibility observation with severity guidance."""

    severity: Literal["critical", "major", "minor"] = Field(
        default="major",
        description=(
            "Impact severity for prioritization: "
            "'critical' = Blocks access entirely (missing alt on key diagram, broken table structure). "
            "'major' = Significant barrier (skipped heading level, unclear reading order). "
            "'minor' = Inconvenience (missing emphasis markup, suboptimal but functional). "
            "When uncertain, prefer 'major' over 'minor'."
        ),
    )

    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Your confidence in this observation (0.0-1.0). "
            "High (0.8+): Clear visual evidence, unambiguous issue. "
            "Medium (0.5-0.8): Some evidence but interpretation needed. "
            "Low (<0.5): Uncertain, may need human verification. "
            "Be calibrated: don't claim 0.9+ unless you're very sure."
        ),
    )
```

### 6. Validator for Field Consistency

```python
# src/agents/analysis_agent.py (model validator)

from pydantic import model_validator


class AnalysisPageFeatures(BaseModel):
    # ... fields above ...

    @model_validator(mode="after")
    def validate_image_consistency(self) -> "AnalysisPageFeatures":
        """Ensure has_images and image_count are consistent."""
        if self.has_images and self.image_count == 0:
            # Auto-fix: if has_images is True, count must be at least 1
            self.image_count = 1
        elif not self.has_images and self.image_count > 0:
            # Auto-fix: if has_images is False, count must be 0
            self.image_count = 0
        return self

    @model_validator(mode="after")
    def validate_complexity_factors(self) -> "AnalysisPageFeatures":
        """Ensure complexity_factors matches complexity_score."""
        if self.complexity_score <= 0.3 and self.complexity_factors:
            # Low complexity shouldn't have factors
            import logging
            logging.getLogger(__name__).debug(
                f"Clearing complexity_factors for low complexity page {self.page_num}"
            )
            self.complexity_factors = []
        return self
```

## Acceptance Criteria

### 1. Dependencies
- [ ] `AgentDependencies` dataclass with all context fields
- [ ] `deps_type=AgentDependencies` on all agent creations
- [ ] Dependency injection working in agent runs

### 2. Dynamic Instructions
- [ ] Extraction agent: manifest heading guidance
- [ ] Extraction agent: document type hints
- [ ] All agents: retry failure guidance
- [ ] Figures agent: page context from manifest
- [ ] Instructions only injected when deps are available

### 3. Field Descriptions
- [ ] All complex fields have detailed descriptions
- [ ] Descriptions guide LLM on edge cases
- [ ] Severity levels clearly defined
- [ ] Confidence calibration guidance included

### 4. Validators
- [ ] `has_images` / `image_count` consistency enforced
- [ ] `complexity_factors` / `complexity_score` consistency
- [ ] Auto-fix where possible, warn on inconsistency

### 5. Tests
- [ ] Dynamic instructions inject correctly
- [ ] Empty deps don't cause errors
- [ ] Retry guidance includes failure history
- [ ] Field validators enforce constraints

## Deliverables

### Files to Create
```
src/agents/
└── dependencies.py            # AgentDependencies dataclass
```

### Files to Modify
```
src/agents/base_agent.py       # Add deps_type, _run_with_deps()
src/agents/analysis_agent.py   # Enhanced field descriptions, validators
src/agents/extraction_agent.py # Dynamic instructions
src/agents/figures_agent.py    # Dynamic instructions
src/agents/tables_agent.py     # Dynamic instructions
src/agents/structure_agent.py  # Dynamic instructions
src/agents/typography_agent.py # Dynamic instructions
```

### Tests to Create
```
tests/unit/agents/
├── test_dependencies.py
├── test_dynamic_instructions.py
└── test_field_validators.py
```

## Technical Notes

### PydanticAI Instructions Pattern

```python
# Instructions are functions decorated with @agent.instructions
# They receive RunContext with deps and return additional prompt text

@agent.instructions
async def my_instruction(ctx: RunContext[AgentDependencies]) -> str:
    # Access deps via ctx.deps
    if ctx.deps.manifest:
        return f"Additional guidance based on {ctx.deps.manifest}"
    return ""  # Return empty string to skip
```

### When to Use Dynamic Instructions

| Use Case | Instruction Type |
|----------|-----------------|
| Document structure guidance | Manifest injection |
| Document type hints | Type-specific tips |
| Retry with different approach | Failure history |
| Page-specific context | Custom context dict |
| Job history patterns | Job service queries |

### Performance Consideration

Dynamic instructions add to prompt length. Keep them concise:
- Manifest headings: ~100-500 tokens
- Document type hints: ~50 tokens
- Retry guidance: ~100 tokens
- **Total overhead: ~5-10% prompt size increase**

## Definition of Done

- [ ] `AgentDependencies` implemented and tested
- [ ] All agents use `deps_type=AgentDependencies`
- [ ] Extraction agent has manifest/type/retry instructions
- [ ] Specialized agents have relevant dynamic instructions
- [ ] Field descriptions enhanced with LLM guidance
- [ ] Model validators enforce field consistency
- [ ] All tests pass
- [ ] Documentation complete
