# PRD-012: Analysis Agent (Sonnet Phase)

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation
**Estimated Effort**: 3 days
**Dependencies**: PRD-011 (Remediation Data Models), PRD-007 (Processing Worker)
**Reference**: [Accessibility Remediation Pipeline](../../../docs/features/accessibility-remediation-pipeline.md)
**GitHub Issues**: [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24)

## Problem Statement

The current `FullDocumentAgent` uses a single model (Haiku) for both structure analysis and transcription. This approach under-utilizes model capabilities:

- **Analysis** requires deep reasoning about document semantics, layout detection, and accessibility issues
- **Transcription** is mechanical work that benefits from structure guidance

By splitting into two phases with different models, we can:
1. Use Sonnet 4.5's superior reasoning for the hard analytical work
2. Use Haiku's efficiency for bulk transcription guided by Sonnet's analysis
3. Generate a `DocumentManifest` that routes downstream specialized agents
4. Capture initial observations during analysis (before transcription)

## Success Criteria

- [ ] `AnalysisAgent` implemented using Claude Sonnet 4.5
- [ ] Outputs `DocumentManifest` with page features and agent routing
- [ ] Outputs enhanced `HeadingTree` with observation annotations
- [ ] Outputs initial `Observation` list from visual analysis
- [ ] Base agent framework supports model switching
- [ ] Analysis phase completes in <60 seconds for typical documents
- [ ] Cost tracking separates analysis vs extraction costs

## Technical Requirements

### Base Agent Model Switching

```python
# src/agents/base_agent.py - Extension

from enum import Enum

class ModelTier(str, Enum):
    """Model tier for cost/capability tradeoff."""
    REASONING = "reasoning"      # Sonnet - analysis, consolidation
    EFFICIENT = "efficient"      # Haiku - transcription, simple tasks


MODEL_TIER_MAP = {
    ModelTier.REASONING: "us.anthropic.claude-sonnet-4-5-20250514-v1:0",
    ModelTier.EFFICIENT: "us.anthropic.claude-haiku-4-5-20250514-v1:0",
}


@dataclass
class AgentConfig:
    """Configuration for specialist agents."""

    name: str
    prompts_file: Path
    output_type: type[BaseModel]
    correction_types: list[str] = field(default_factory=list)
    max_retries: int = 2
    temperature: float = 0.2

    # NEW: Model tier selection
    model_tier: ModelTier = ModelTier.EFFICIENT


class BaseDocumentAgent(ABC, Generic[TOutput]):
    """Abstract base class for document processing agents."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.prompts = self._load_prompts()

        # Select model based on tier
        self.model_id = MODEL_TIER_MAP[config.model_tier]
        self._agent = self._create_agent()

        logger.info(
            f"Agent {config.name} initialized with model tier "
            f"{config.model_tier.value} ({self.model_id})"
        )

    def _create_agent(self) -> Agent[None, TOutput]:
        """Create PydanticAI agent with selected model."""
        from pydantic_ai.models.bedrock import BedrockConverseModel

        model = BedrockConverseModel(model_name=self.model_id)

        return Agent(
            model,
            output_type=self.config.output_type,
            system_prompt=self.prompts["system_prompt"],
            retries=self.config.max_retries,
        )
```

### Analysis Agent Implementation

```python
# src/agents/analysis_agent.py

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from src.agents.base_agent import BaseDocumentAgent, AgentConfig, ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.remediation import DocumentManifest, PageFeatures
from src.shared.models.observation import Observation, ObservationLocation


class AnalysisOutput(BaseModel):
    """Structured output from analysis phase."""

    # Document metadata
    document_title: str = Field(default="Untitled")
    document_type: str = Field(
        default="unknown",
        description="syllabus, lecture_notes, exam, handout, etc."
    )

    # Structure
    heading_tree: HeadingTree

    # Per-page features
    page_features: list[PageFeatures]

    # Agent routing
    required_agents: list[str] = Field(
        default_factory=list,
        description="Agents needed: figures, tables, structure, typography"
    )

    # Initial observations
    observations: list[AnalysisObservation] = Field(default_factory=list)

    # Confidence
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    notes: str = Field(default="")


class AnalysisObservation(BaseModel):
    """Observation format for analysis output (converted to full Observation later)."""

    page_num: int
    visual_description: str
    markup_issue: str
    severity: str = "major"
    confidence: float = 0.8


@dataclass
class AnalysisAgentConfig:
    """Configuration for the analysis agent."""

    prompts_file: Path = field(
        default_factory=lambda: Path("analysis.yaml")
    )
    max_retries: int = 2
    temperature: float = 0.3  # Slightly higher for analytical reasoning


class AnalysisAgent:
    """Agent that performs deep document analysis using Sonnet.

    This agent:
    1. Analyzes document structure and layout
    2. Detects features on each page (images, tables, lists, etc.)
    3. Determines which specialized agents need to run
    4. Generates initial accessibility observations
    5. Produces a DocumentManifest to guide extraction

    Uses Claude Sonnet 4.5 for superior reasoning capabilities.
    """

    def __init__(self, config: AnalysisAgentConfig | None = None) -> None:
        self.config = config or AnalysisAgentConfig()
        self.prompts = self._load_prompts()
        self._agent: Agent[None, AnalysisOutput] | None = None

        logger.info("AnalysisAgent initialized (Sonnet 4.5)")

    def _load_prompts(self) -> dict[str, Any]:
        """Load prompts from YAML configuration file."""
        prompts_file = self.config.prompts_file
        if not prompts_file.is_absolute():
            prompts_file = Path(settings.agent_prompts_dir) / prompts_file

        try:
            with open(prompts_file) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Prompts file not found: {prompts_file}, using defaults")
            return self._default_prompts()

    def _default_prompts(self) -> dict[str, Any]:
        """Default prompts for analysis agent."""
        return {
            "system_prompt": ANALYSIS_SYSTEM_PROMPT,
            "user_prompt": ANALYSIS_USER_PROMPT,
        }

    def _get_agent(self) -> Agent[None, AnalysisOutput]:
        """Get or create the analysis agent."""
        if self._agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            model = BedrockConverseModel(
                model_name=MODEL_TIER_MAP[ModelTier.REASONING]
            )
            self._agent = Agent(
                model,
                output_type=AnalysisOutput,
                system_prompt=self.prompts["system_prompt"],
                retries=self.config.max_retries,
            )
        return self._agent

    async def analyze(
        self,
        pages: list[PageData],
        job_id: str
    ) -> tuple[DocumentManifest, list[Observation], LLMUsage]:
        """Analyze document and produce manifest + initial observations.

        Args:
            pages: List of page images from PDF conversion
            job_id: Job identifier for observation tracking

        Returns:
            Tuple of (DocumentManifest, list[Observation], LLMUsage)
        """
        agent = self._get_agent()

        # Build messages with all page images
        messages = self._build_image_messages(pages)

        # Add user prompt
        user_prompt = self.prompts["user_prompt"].format(
            total_pages=len(pages)
        )
        messages.append(user_prompt)

        # Run agent
        result = await agent.run(
            messages,
            model_settings={
                "max_tokens": 4096,
                "temperature": self.config.temperature,
            }
        )

        # Extract usage
        usage_data = result.usage()
        usage = LLMUsage(
            input_tokens=usage_data.request_tokens or 0,
            output_tokens=usage_data.response_tokens or 0,
            total_tokens=(usage_data.request_tokens or 0) + (usage_data.response_tokens or 0),
            estimated_cost_cents=self._calculate_cost(
                usage_data.request_tokens or 0,
                usage_data.response_tokens or 0,
                ModelTier.REASONING
            ),
        )

        # Convert output to DocumentManifest
        output = result.output
        manifest = DocumentManifest(
            job_id=job_id,
            document_title=output.document_title,
            document_type=output.document_type,
            total_pages=len(pages),
            heading_tree_json=output.heading_tree.model_dump_json(),
            page_features=output.page_features,
            required_agents=output.required_agents,
            skip_agents=self._determine_skip_agents(output.required_agents),
            analysis_confidence=output.confidence,
            analysis_notes=output.notes,
            analysis_model=MODEL_TIER_MAP[ModelTier.REASONING],
        )

        # Convert observations to full Observation model
        observations = [
            Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="analysis",
                source="agent",
                visual_description=obs.visual_description,
                markup_description=obs.markup_issue,
                location=ObservationLocation(
                    location_type="region",
                    value=f"Page {obs.page_num}",
                    page_num=obs.page_num,
                ),
                confidence=obs.confidence,
                severity=obs.severity,
                route="auto" if obs.confidence >= 0.7 else "manual",
            )
            for obs in output.observations
        ]

        logger.info(
            f"Analysis complete: {len(manifest.page_features)} pages, "
            f"{len(manifest.required_agents)} agents needed, "
            f"{len(observations)} initial observations"
        )

        return manifest, observations, usage

    def _build_image_messages(
        self,
        pages: list[PageData]
    ) -> list[str | BinaryContent]:
        """Build message list with all page images."""
        messages: list[str | BinaryContent] = []

        for page in pages:
            messages.append(f"[Page {page.page_num}]")
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                messages.append(
                    BinaryContent(data=image_bytes, media_type="image/png")
                )

        return messages

    def _determine_skip_agents(self, required: list[str]) -> list[str]:
        """Determine which agents can be skipped."""
        all_agents = {"figures", "tables", "structure", "typography"}
        return list(all_agents - set(required))

    def _calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        tier: ModelTier
    ) -> float:
        """Calculate cost in cents for the given model tier."""
        # Sonnet 4.5 pricing (per 1M tokens)
        if tier == ModelTier.REASONING:
            input_price = 3.00   # $3/1M input
            output_price = 15.00  # $15/1M output
        else:  # Haiku
            input_price = 0.80   # $0.80/1M input
            output_price = 4.00  # $4/1M output

        input_cost = (input_tokens / 1_000_000) * input_price * 100
        output_cost = (output_tokens / 1_000_000) * output_price * 100

        return input_cost + output_cost


# Prompt constants
ANALYSIS_SYSTEM_PROMPT = """You are an expert document accessibility analyst.
Your task is to deeply analyze PDF documents to:

1. UNDERSTAND STRUCTURE
   - Identify the document type (syllabus, lecture notes, exam, etc.)
   - Map the complete heading hierarchy
   - Detect layout patterns (single column, two column, mixed)

2. DETECT PAGE FEATURES
   For each page, identify:
   - Images and their apparent purpose (decorative, informative, complex)
   - Tables and their complexity (simple, merged cells, nested)
   - Lists (ordered, unordered, definition)
   - Code blocks or technical content
   - Mathematical expressions

3. IDENTIFY ACCESSIBILITY ISSUES
   Look for discrepancies between visual presentation and likely markup:
   - Headings that don't match visual hierarchy
   - Images that will need descriptions
   - Tables that may lose structure in conversion
   - Typography that conveys meaning (bold for emphasis, etc.)

4. ROUTE SPECIALIZED AGENTS
   Based on content, determine which specialized agents are needed:
   - "figures": If document has informative images
   - "tables": If document has data tables
   - "structure": If heading hierarchy is complex
   - "typography": If visual styling conveys semantics

Be thorough but efficient. Focus on issues that affect accessibility.
"""

ANALYSIS_USER_PROMPT = """Analyze this {total_pages}-page document.

For each page, detect:
- Content features (images, tables, lists, etc.)
- Layout type
- Complexity factors

Identify the document's heading structure and any accessibility concerns.

Determine which specialized agents should analyze this document.

Provide initial observations for any clear accessibility issues you notice.
"""
```

### Prompt Configuration

```yaml
# config/agents/analysis.yaml

system_prompt: |
  You are an expert document accessibility analyst specializing in PDF remediation.

  Your task is to perform deep analysis of PDF documents to guide the remediation pipeline.

  ANALYSIS OBJECTIVES:

  1. DOCUMENT CLASSIFICATION
     - Identify document type: syllabus, lecture_notes, exam, handout, research_paper, other
     - Note any special characteristics

  2. STRUCTURE ANALYSIS
     - Build complete heading hierarchy (H1-H6)
     - Detect layout: single_column, two_column, mixed
     - Identify reading order issues in multi-column layouts

  3. PER-PAGE FEATURE DETECTION
     For each page, report:
     - has_images: true/false
     - image_count: number of images
     - has_tables: true/false
     - table_count: number of tables
     - has_lists: true/false
     - has_code_blocks: true/false
     - has_math: true/false
     - layout_type: single_column/two_column/mixed
     - complexity_score: 0.0-1.0 (based on content density and structure)
     - complexity_factors: ["dense tables", "nested lists", etc.]

  4. INITIAL OBSERVATIONS
     Note any clear accessibility issues:
     - Missing or incorrect heading levels
     - Images that appear informative but likely lack descriptions
     - Tables with complex structures (merged cells, nested headers)
     - Typography conveying meaning (color-coded text, etc.)

  5. AGENT ROUTING
     Based on content, recommend which agents should run:
     - "figures": Document has images that need description
     - "tables": Document has data tables
     - "structure": Heading hierarchy needs verification
     - "typography": Visual styling appears semantic

  Be thorough but focused on accessibility-relevant details.

user_prompt: |
  Analyze this {total_pages}-page PDF document.

  Examine each page image and provide:

  1. Document title and type classification

  2. Complete heading structure with:
     - Level (1-6)
     - Title text
     - Page number
     - Any section numbering

  3. Page-by-page feature analysis

  4. List of required specialized agents

  5. Initial accessibility observations (if any clear issues are visible)

  Focus on content that will need accessibility remediation.
```

### Integration with Processing Service

```python
# src/services/processing_service.py - Modified

from src.agents.analysis_agent import AnalysisAgent
from src.agents.extraction_agent import ExtractionAgent  # PRD-013
from src.services.remediation_storage_service import RemediationStorageService


class ProcessingService:
    """Main service for orchestrating PDF-to-accessible-markdown conversion."""

    async def process_document(
        self,
        job: ProcessingQueuePayload,
    ) -> ProcessingResult:
        """Process PDF using analysis + extraction pipeline."""
        start_time = time.time()

        try:
            # Update substatus
            await self.job.update_job_status(
                job.job_id, "processing", substatus="analyzing"
            )

            # Download and convert PDF
            pdf_content = await self.storage.download_temp_file(s3_key=job.s3_key)
            conversion_result = await self.pdf_converter.convert_with_page_images(
                pdf_content
            )
            pages = conversion_result.pages

            # Phase 1: Analysis (Sonnet)
            logger.info(f"Job {job.job_id}: Starting analysis phase (Sonnet)")
            analysis_agent = AnalysisAgent()
            manifest, initial_observations, analysis_usage = await analysis_agent.analyze(
                pages, job.job_id
            )

            # Save manifest and initial observations
            await self.remediation_storage.save_manifest(job.job_id, manifest)
            await self.remediation_storage.save_observations(
                job.job_id, initial_observations
            )

            logger.info(
                f"Job {job.job_id}: Analysis complete - "
                f"{len(manifest.required_agents)} agents needed, "
                f"{len(initial_observations)} initial observations, "
                f"cost: ${analysis_usage.estimated_cost_cents/100:.4f}"
            )

            # Update substatus
            await self.job.update_job_status(
                job.job_id, "processing",
                substatus="extracting",
                observation_count=len(initial_observations),
                analysis_model=manifest.analysis_model,
            )

            # Phase 2: Extraction (Haiku) - PRD-013
            # ... continues in PRD-013

        except Exception as e:
            # Error handling
            pass
```

## Acceptance Criteria

### 1. Model Switching
- [ ] `ModelTier` enum defines REASONING and EFFICIENT tiers
- [ ] `MODEL_TIER_MAP` maps tiers to Bedrock model IDs
- [ ] Base agent accepts `model_tier` in config
- [ ] Cost calculation uses correct pricing per tier

### 2. Analysis Agent
- [ ] Uses Sonnet 4.5 (REASONING tier)
- [ ] Accepts all page images as input
- [ ] Outputs structured `AnalysisOutput` model
- [ ] Generates `DocumentManifest` with page features
- [ ] Produces initial observations list
- [ ] Determines required agents based on content

### 3. Page Feature Detection
- [ ] Detects images and counts
- [ ] Detects tables and counts
- [ ] Detects lists, code blocks, math
- [ ] Assesses layout type per page
- [ ] Calculates complexity scores

### 4. Agent Routing
- [ ] `required_agents` populated based on features
- [ ] `skip_agents` computed as complement
- [ ] Routing logic documented and testable

### 5. Integration
- [ ] Processing service calls analysis agent
- [ ] Manifest saved to S3
- [ ] Initial observations saved to S3
- [ ] Substatus updated to "extracting" after analysis
- [ ] Usage metrics tracked separately

### 6. Performance
- [ ] Analysis completes in <60s for 10-page doc
- [ ] Memory usage reasonable with all page images
- [ ] Error handling for API failures

## Deliverables

### Files to Create

```
src/agents/
├── analysis_agent.py           # AnalysisAgent implementation
└── model_tiers.py              # ModelTier enum and mapping

config/agents/
└── analysis.yaml               # Analysis prompts

tests/agents/
└── test_analysis_agent.py
```

### Files to Modify

```
src/agents/base_agent.py        # Add model_tier support
src/services/processing_service.py  # Integrate analysis phase
src/shared/llm_cost.py          # Add Sonnet pricing
```

## Technical Notes

### Sonnet 4.5 Pricing (Bedrock)

```python
# Per 1M tokens
SONNET_INPUT_PRICE = 3.00    # $3.00/1M input tokens
SONNET_OUTPUT_PRICE = 15.00  # $15.00/1M output tokens

# For 50K input + 2K output (typical analysis):
# Input: (50000/1M) * $3.00 = $0.15
# Output: (2000/1M) * $15.00 = $0.03
# Total: ~$0.18 per document
```

### Token Budget

Analysis output should be concise:
- `max_tokens: 4096` is sufficient for manifest + observations
- Page features: ~50 tokens per page
- Heading tree: ~20 tokens per heading
- Observations: ~100 tokens each

### Error Handling

```python
# Retry with exponential backoff for Bedrock throttling
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(ThrottlingException)
)
async def analyze(self, pages: list[PageData], job_id: str):
    # ...
```

## Definition of Done

- [ ] AnalysisAgent implemented with Sonnet 4.5
- [ ] Model tier switching works in base agent
- [ ] DocumentManifest generated correctly
- [ ] Initial observations captured
- [ ] Agent routing logic implemented
- [ ] Prompts tuned for analysis task
- [ ] Integration tests pass
- [ ] Cost tracking accurate
- [ ] Performance meets targets
- [ ] Documentation complete
