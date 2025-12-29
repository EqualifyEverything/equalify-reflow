# PRD-014: Specialized Analysis Agents

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation
**Estimated Effort**: 5 days
**Dependencies**: PRD-011 (Data Models), PRD-012 (Analysis Agent), PRD-013 (Extraction Agent)
**Reference**: [Accessibility Remediation Pipeline](../../../docs/features/accessibility-remediation-pipeline.md)
**GitHub Issues**: [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23) (Structure & Typography), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24) (Figures & Tables)

## Problem Statement

After extraction produces initial markdown, specialized agents analyze specific accessibility concerns by comparing the visual PDF to the generated markup. Each agent focuses on a narrow domain, allowing for:

1. **Deeper expertise**: Prompts optimized for specific issue types
2. **Efficient routing**: Only process pages with relevant content
3. **Targeted observations**: High-quality, actionable findings

The agents are:
- **FiguresAgent** (#24): Image classification and description quality
- **TablesAgent** (#24): Table structure accuracy and data validation
- **StructureAgent** (#23): Heading hierarchy and reading order
- **TypographyAgent** (#23): Semantic meaning from visual styling

Each agent outputs `Observation` objects that feed into the consolidation phase.

## Success Criteria

- [ ] All four specialized agents implemented
- [ ] Agent routing based on DocumentManifest.required_agents
- [ ] Per-page processing only for relevant pages
- [ ] Observations generated in standard format
- [ ] Sonnet 4.5 used for analytical accuracy
- [ ] Total specialized analysis <2 minutes for typical documents

## Technical Requirements

### Agent Router

```python
# src/agents/agent_router.py

import logging
from typing import Any

from src.shared.models.observation import Observation
from src.shared.models.remediation import DocumentManifest
from src.services.pdf_converter import PageData

logger = logging.getLogger(__name__)


class AgentRouter:
    """Routes specialized agents based on document manifest."""

    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}

    def register_agent(self, name: str, agent: Any) -> None:
        """Register an agent by name."""
        self._agents[name] = agent
        logger.debug(f"Registered agent: {name}")

    async def run_required_agents(
        self,
        manifest: DocumentManifest,
        pages: list[PageData],
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Run all required agents and collect observations.

        Args:
            manifest: DocumentManifest with required_agents list
            pages: All page images
            markdown: Current markdown content
            job_id: Job identifier

        Returns:
            Combined list of observations from all agents
        """
        all_observations: list[Observation] = []

        for agent_name in manifest.required_agents:
            if agent_name not in self._agents:
                logger.warning(f"Agent not registered: {agent_name}")
                continue

            agent = self._agents[agent_name]

            # Determine which pages this agent should process
            relevant_pages = self._get_relevant_pages(agent_name, manifest, pages)

            if not relevant_pages:
                logger.info(f"Agent {agent_name}: No relevant pages, skipping")
                continue

            logger.info(
                f"Agent {agent_name}: Processing {len(relevant_pages)} pages"
            )

            # Run agent on relevant pages
            observations = await agent.analyze(
                pages=relevant_pages,
                manifest=manifest,
                markdown=markdown,
                job_id=job_id,
            )

            all_observations.extend(observations)

            logger.info(
                f"Agent {agent_name}: Generated {len(observations)} observations"
            )

        return all_observations

    def _get_relevant_pages(
        self,
        agent_name: str,
        manifest: DocumentManifest,
        pages: list[PageData],
    ) -> list[PageData]:
        """Filter pages relevant to a specific agent."""
        relevant_page_nums: set[int] = set()

        for pf in manifest.page_features:
            if agent_name == "figures" and pf.has_images:
                relevant_page_nums.add(pf.page_num)
            elif agent_name == "tables" and pf.has_tables:
                relevant_page_nums.add(pf.page_num)
            elif agent_name == "structure":
                # Structure agent processes all pages
                relevant_page_nums.add(pf.page_num)
            elif agent_name == "typography" and pf.complexity_score > 0.5:
                # Typography agent for complex pages
                relevant_page_nums.add(pf.page_num)

        return [p for p in pages if p.page_num in relevant_page_nums]
```

### Base Specialized Agent

```python
# src/agents/specialized_agent_base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation
from src.shared.models.remediation import DocumentManifest


@dataclass
class SpecializedAgentConfig:
    """Configuration for specialized agents."""

    name: str
    prompts_file: Path
    focus_area: str  # "figures", "tables", "structure", "typography"
    max_retries: int = 2
    temperature: float = 0.3


class SpecializedAgentBase(ABC):
    """Base class for specialized analysis agents.

    All specialized agents:
    - Use Sonnet 4.5 for analytical accuracy
    - Process only relevant pages
    - Output Observation objects
    - Receive full document context
    """

    def __init__(self, config: SpecializedAgentConfig) -> None:
        self.config = config
        self.prompts = self._load_prompts()
        self._agent: Agent | None = None

    @abstractmethod
    def _load_prompts(self) -> dict[str, Any]:
        """Load agent-specific prompts."""
        pass

    @abstractmethod
    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Analyze pages and generate observations."""
        pass

    def _get_agent(self, output_type: type[BaseModel]) -> Agent:
        """Get or create the PydanticAI agent."""
        if self._agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            model = BedrockConverseModel(
                model_name=MODEL_TIER_MAP[ModelTier.REASONING]
            )
            self._agent = Agent(
                model,
                output_type=output_type,
                system_prompt=self.prompts["system_prompt"],
                retries=self.config.max_retries,
            )
        return self._agent
```

### FiguresAgent (#24)

```python
# src/agents/figures_agent.py

import base64
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai.messages import BinaryContent

from src.agents.specialized_agent_base import SpecializedAgentBase, SpecializedAgentConfig
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.remediation import DocumentManifest


class ImageAnalysis(BaseModel):
    """Analysis of a single image."""

    image_index: int = Field(..., description="Image number on this page (1-indexed)")
    image_type: str = Field(
        ...,
        description="decorative, informative, complex, or text"
    )
    visual_description: str = Field(
        ...,
        description="What the image visually depicts"
    )
    current_alt_status: str = Field(
        ...,
        description="TODO placeholder, empty, or has description"
    )
    recommended_action: str = Field(
        ...,
        description="What should be done: add_alt, improve_alt, mark_decorative, add_long_desc"
    )
    suggested_alt: str | None = Field(
        default=None,
        description="Suggested alt text if applicable"
    )
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class FiguresAnalysisOutput(BaseModel):
    """Output from figures analysis for a single page."""

    page_num: int
    images_found: int
    analyses: list[ImageAnalysis]
    notes: str = ""


class FiguresAgent(SpecializedAgentBase):
    """Agent specialized in image accessibility analysis.

    Focuses on:
    - Classifying images (decorative, informative, complex, text)
    - Evaluating current alt text quality
    - Generating appropriate alt text suggestions
    - Identifying images needing long descriptions
    """

    def __init__(self) -> None:
        config = SpecializedAgentConfig(
            name="figures_agent",
            prompts_file=Path("figures.yaml"),
            focus_area="figures",
        )
        super().__init__(config)

    def _load_prompts(self) -> dict[str, Any]:
        return {
            "system_prompt": FIGURES_SYSTEM_PROMPT,
            "user_prompt": FIGURES_USER_PROMPT,
        }

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Analyze images on provided pages."""
        observations: list[Observation] = []
        agent = self._get_agent(FiguresAnalysisOutput)

        for page in pages:
            page_features = next(
                (pf for pf in manifest.page_features if pf.page_num == page.page_num),
                None
            )

            if not page_features or not page_features.has_images:
                continue

            # Extract markdown section for this page (approximate)
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build message with page image
            messages = [
                f"Analyze images on page {page.page_num} of document: {manifest.document_title}",
                f"\nCurrent markdown for this page:\n```\n{page_markdown}\n```\n",
                f"\nExpected images on this page: {page_features.image_count}",
                "\nPage image:",
            ]

            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                messages.append(
                    BinaryContent(data=image_bytes, media_type="image/png")
                )

            # Run analysis
            result = await agent.run(messages)
            output = result.output

            # Convert to observations
            for analysis in output.analyses:
                if analysis.recommended_action != "none":
                    obs = Observation(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        agent="figures",
                        source="agent",
                        visual_description=analysis.visual_description,
                        markup_description=f"Image {analysis.image_index}: {analysis.current_alt_status}",
                        location=ObservationLocation(
                            location_type="element",
                            value=f"![TODO: describe](image-page-{page.page_num}-{analysis.image_index}.png)",
                            page_num=page.page_num,
                        ),
                        confidence=analysis.confidence,
                        severity="major" if analysis.image_type in ["informative", "complex"] else "minor",
                        route="auto" if analysis.confidence >= 0.7 else "manual",
                        manual_reason="Low confidence in image classification" if analysis.confidence < 0.7 else None,
                    )
                    observations.append(obs)

        return observations

    def _extract_page_markdown(self, markdown: str, page_num: int) -> str:
        """Extract approximate markdown section for a page."""
        # Simple heuristic: look for page markers or divide evenly
        # In practice, may use heading boundaries from manifest
        lines = markdown.split("\n")
        chunk_size = max(len(lines) // 10, 20)  # Rough approximation
        start = (page_num - 1) * chunk_size
        end = page_num * chunk_size
        return "\n".join(lines[start:end])


FIGURES_SYSTEM_PROMPT = """You are an image accessibility expert.
Analyze images in PDF documents and evaluate their accessibility.

IMAGE CLASSIFICATION:
- decorative: Visual flourish, background, spacer - should have empty alt=""
- informative: Conveys information - needs descriptive alt text
- complex: Charts, diagrams, infographics - needs alt + long description
- text: Image of text - text should be transcribed

EVALUATION CRITERIA:
1. Does the image convey meaningful information?
2. Is the current alt text (if any) accurate and sufficient?
3. Would a screen reader user miss important information?
4. Does the image contain text that should be accessible?

OUTPUT REQUIREMENTS:
- Classify each image
- Describe what it visually shows
- Note current alt text status
- Recommend specific action
- Suggest alt text when appropriate
"""

FIGURES_USER_PROMPT = """Analyze the images on this page."""
```

### TablesAgent (#24)

```python
# src/agents/tables_agent.py

class TableAnalysis(BaseModel):
    """Analysis of a single table."""

    table_index: int
    has_headers: bool
    header_structure: str  # "single_row", "multi_row", "column_headers", "none"
    complexity: str  # "simple", "merged_cells", "nested", "irregular"
    data_accuracy: str  # "accurate", "partial", "missing_data", "structural_loss"
    visual_description: str
    markdown_issues: list[str]
    recommended_action: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class TablesAnalysisOutput(BaseModel):
    """Output from tables analysis for a single page."""

    page_num: int
    tables_found: int
    analyses: list[TableAnalysis]
    notes: str = ""


class TablesAgent(SpecializedAgentBase):
    """Agent specialized in table accessibility analysis.

    Focuses on:
    - Verifying table headers are properly marked
    - Detecting merged cell structures that may be lost
    - Comparing visual table data to markdown accuracy
    - Identifying tables that need restructuring
    """

    def __init__(self) -> None:
        config = SpecializedAgentConfig(
            name="tables_agent",
            prompts_file=Path("tables.yaml"),
            focus_area="tables",
        )
        super().__init__(config)

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Analyze tables on provided pages."""
        observations: list[Observation] = []
        # Implementation similar to FiguresAgent
        # Focus on table structure validation

        return observations


TABLES_SYSTEM_PROMPT = """You are a table accessibility expert.
Analyze tables in PDF documents and evaluate their markdown representation.

TABLE STRUCTURE ISSUES:
- Missing header row identification
- Merged cells that can't be represented in markdown
- Complex multi-level headers
- Irregular structures (varying column counts)

VALIDATION TASKS:
1. Compare visual table structure to markdown
2. Verify all data cells are captured
3. Check header associations are clear
4. Identify cells that may need scope attributes

OUTPUT REQUIREMENTS:
- Note structural complexity
- Flag data accuracy issues
- Recommend specific fixes
- Highlight cells needing manual review
"""
```

### StructureAgent (#23)

```python
# src/agents/structure_agent.py

class StructureIssue(BaseModel):
    """A structural issue found in the document."""

    issue_type: str  # "heading_skip", "heading_mismatch", "reading_order", "missing_landmark"
    location_description: str
    visual_evidence: str
    markup_state: str
    severity: str
    recommended_fix: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class StructureAnalysisOutput(BaseModel):
    """Output from structure analysis."""

    issues: list[StructureIssue]
    heading_hierarchy_valid: bool
    reading_order_valid: bool
    notes: str = ""


class StructureAgent(SpecializedAgentBase):
    """Agent specialized in document structure analysis.

    Focuses on:
    - Heading hierarchy validation (no skipped levels)
    - Visual vs semantic heading alignment
    - Reading order in multi-column layouts
    - Section and landmark structure
    """

    def __init__(self) -> None:
        config = SpecializedAgentConfig(
            name="structure_agent",
            prompts_file=Path("structure.yaml"),
            focus_area="structure",
        )
        super().__init__(config)

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Analyze document structure."""
        observations: list[Observation] = []

        # Structure agent processes all pages together
        # to understand full document hierarchy

        return observations


STRUCTURE_SYSTEM_PROMPT = """You are a document structure expert.
Analyze document organization for accessibility compliance.

HEADING RULES:
- H1 should be document title (one per document)
- No skipped levels (H1 → H3 is invalid)
- Heading levels should match visual hierarchy
- Nested sections should use incrementing levels

READING ORDER:
- Content should flow logically for screen readers
- Multi-column content needs proper linearization
- Sidebars/callouts should be positioned appropriately

EVALUATE:
1. Does heading structure match visual hierarchy?
2. Are there any skipped heading levels?
3. Would screen reader navigation make sense?
4. Is content order logical when linearized?
"""
```

### TypographyAgent (#23)

```python
# src/agents/typography_agent.py

class TypographyIssue(BaseModel):
    """A typography-based semantic issue."""

    issue_type: str  # "emphasis_unmarked", "definition_unmarked", "semantic_color", "visual_heading"
    visual_description: str
    markup_state: str
    semantic_meaning: str
    recommended_markup: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class TypographyAnalysisOutput(BaseModel):
    """Output from typography analysis."""

    page_num: int
    issues: list[TypographyIssue]
    notes: str = ""


class TypographyAgent(SpecializedAgentBase):
    """Agent specialized in semantic typography analysis.

    Focuses on:
    - Bold text conveying emphasis (should use <strong>)
    - Italic text indicating terms/definitions
    - Color-coding that conveys meaning
    - Font size changes suggesting structure
    """

    def __init__(self) -> None:
        config = SpecializedAgentConfig(
            name="typography_agent",
            prompts_file=Path("typography.yaml"),
            focus_area="typography",
        )
        super().__init__(config)

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Analyze typography-based semantics."""
        observations: list[Observation] = []

        return observations


TYPOGRAPHY_SYSTEM_PROMPT = """You are a typography semantics expert.
Analyze how visual styling conveys meaning that should be preserved in markup.

SEMANTIC TYPOGRAPHY:
- Bold often indicates emphasis or importance
- Italic may indicate terms, titles, or foreign words
- Color-coding may indicate categories or status
- Size changes may suggest heading hierarchy

EVALUATION:
1. Does bold text convey emphasis? Should use **strong**
2. Does italic indicate a term? Should use *em* or definition list
3. Does color convey meaning? Needs alternative indicator
4. Does size suggest structure? May need heading markup

AVOID FALSE POSITIVES:
- Decorative styling vs semantic styling
- Brand fonts vs meaningful weight changes
- Design elements vs information hierarchy
"""
```

### Integration with Processing Service

```python
# src/services/processing_service.py - Phase 3

async def process_document(
    self,
    job: ProcessingQueuePayload,
) -> ProcessingResult:
    """Process PDF using full remediation pipeline."""

    # ... Phase 1 & 2 from previous PRDs ...

    # Phase 3: Specialized Analysis
    logger.info(f"Job {job.job_id}: Starting specialized analysis")

    await self.job.update_job_status(
        job.job_id, "processing", substatus="specializing"
    )

    # Initialize and register agents
    router = AgentRouter()
    router.register_agent("figures", FiguresAgent())
    router.register_agent("tables", TablesAgent())
    router.register_agent("structure", StructureAgent())
    router.register_agent("typography", TypographyAgent())

    # Run required agents
    specialized_observations = await router.run_required_agents(
        manifest=manifest,
        pages=pages,
        markdown=markdown,
        job_id=job.job_id,
    )

    # Combine with initial observations
    all_observations = initial_observations + specialized_observations

    # Save all observations
    await self.remediation_storage.save_observations(job.job_id, all_observations)

    logger.info(
        f"Job {job.job_id}: Specialized analysis complete - "
        f"{len(specialized_observations)} new observations"
    )

    # Update job
    await self.job.update_job_status(
        job.job_id, "processing",
        substatus="consolidating",
        observation_count=len(all_observations),
    )

    # Phase 4: Consolidation (PRD-015)
    # ...
```

## Acceptance Criteria

### 1. Agent Router
- [ ] Routes based on manifest.required_agents
- [ ] Filters pages by content type
- [ ] Collects observations from all agents
- [ ] Handles missing/unregistered agents gracefully

### 2. FiguresAgent (#24)
- [ ] Classifies images (decorative/informative/complex/text)
- [ ] Evaluates current alt text status
- [ ] Generates alt text suggestions
- [ ] Identifies images needing long descriptions
- [ ] Routes low-confidence to manual queue

### 3. TablesAgent (#24)
- [ ] Detects header structure
- [ ] Identifies merged cell issues
- [ ] Validates data accuracy vs visual
- [ ] Flags tables needing restructure

### 4. StructureAgent (#23)
- [ ] Validates heading hierarchy
- [ ] Detects skipped levels
- [ ] Evaluates reading order
- [ ] Compares visual vs semantic headings

### 5. TypographyAgent (#23)
- [ ] Identifies semantic bold/italic
- [ ] Detects meaningful color usage
- [ ] Finds visual headings without markup
- [ ] Distinguishes semantic from decorative

### 6. Performance
- [ ] Total specialized analysis <2 minutes
- [ ] Per-page processing efficient
- [ ] Only relevant pages processed

## Deliverables

### Files to Create

```
src/agents/
├── agent_router.py
├── specialized_agent_base.py
├── figures_agent.py
├── tables_agent.py
├── structure_agent.py
└── typography_agent.py

config/agents/
├── figures.yaml
├── tables.yaml
├── structure.yaml
└── typography.yaml

tests/agents/
├── test_agent_router.py
├── test_figures_agent.py
├── test_tables_agent.py
├── test_structure_agent.py
└── test_typography_agent.py
```

## Technical Notes

### Cost Estimate (Specialized Phase)

Each agent uses Sonnet for a subset of pages:
- FiguresAgent: ~5K tokens/page with images
- TablesAgent: ~5K tokens/page with tables
- StructureAgent: ~2K tokens for overview
- TypographyAgent: ~3K tokens/complex page

Typical 10-page doc with 3 pages images, 1 page tables:
- Figures: 3 × 5K = 15K tokens
- Tables: 1 × 5K = 5K tokens
- Structure: 2K tokens (overview)
- Typography: 2 × 3K = 6K tokens
- **Total: ~28K tokens → ~$0.10**

### Observation Quality

Observations should be:
- **Specific**: Exact location, not vague
- **Actionable**: Clear recommended fix
- **Scoped**: One issue per observation
- **Confident**: Include confidence score for routing

## Definition of Done

- [ ] All four specialized agents implemented
- [ ] Agent router routes correctly based on manifest
- [ ] Observations generated in standard format
- [ ] Integration tests pass for each agent
- [ ] Page filtering works correctly
- [ ] Cost tracking per agent
- [ ] Documentation complete
- [ ] Ready for consolidation phase
