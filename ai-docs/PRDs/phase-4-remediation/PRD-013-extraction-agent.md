# PRD-013: Extraction Agent (Haiku Phase)

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation
**Estimated Effort**: 2 days
**Dependencies**: PRD-011 (Data Models), PRD-012 (Analysis Agent)
**Reference**: [Accessibility Remediation Pipeline](../../../docs/features/accessibility-remediation-pipeline.md)
**GitHub Issues**: [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24)

## Problem Statement

After the Analysis phase produces a `DocumentManifest` with structure information and page features, the Extraction phase needs to generate the actual markdown transcription. This is primarily mechanical work—the analytical decisions have already been made.

Using Haiku for extraction provides:
1. **Cost efficiency**: ~10x cheaper than Sonnet for output tokens
2. **Speed**: Faster inference for bulk transcription
3. **Guided accuracy**: The manifest provides structure that Haiku follows

The Extraction agent receives rich context from Analysis and produces markdown that:
- Follows the heading structure exactly
- Marks images with placeholders for specialized agents
- Preserves table structure
- Maintains proper reading order based on layout detection

## Success Criteria

- [ ] `ExtractionAgent` implemented using Claude Haiku 4.5
- [ ] Accepts `DocumentManifest` as guidance input
- [ ] Produces complete markdown document
- [ ] Follows heading structure from manifest
- [ ] Uses correct image placeholder format
- [ ] Extraction completes in <90 seconds for typical documents
- [ ] Output quality comparable to current two-phase approach

## Technical Requirements

### Extraction Agent Implementation

```python
# src/agents/extraction_agent.py

import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, HeadingTree

logger = logging.getLogger(__name__)


class ExtractionOutput(BaseModel):
    """Structured output from extraction phase."""

    markdown: str = Field(
        ...,
        description="Complete markdown transcription of the document"
    )
    confidence: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Confidence in transcription accuracy"
    )
    notes: str = Field(
        default="",
        description="Notes about transcription decisions or issues"
    )


@dataclass
class ExtractionAgentConfig:
    """Configuration for the extraction agent."""

    prompts_file: Path = field(
        default_factory=lambda: Path("extraction.yaml")
    )
    max_retries: int = 2
    temperature: float = 0.2  # Low temperature for consistent transcription


class ExtractionAgent:
    """Agent that performs guided markdown extraction using Haiku.

    This agent:
    1. Receives DocumentManifest with structure analysis
    2. Receives all page images
    3. Transcribes content following the manifest structure
    4. Produces markdown with image placeholders for specialized agents

    Uses Claude Haiku 4.5 for cost-efficient transcription.
    The hard analytical work was done by the Analysis agent (Sonnet).
    """

    def __init__(self, config: ExtractionAgentConfig | None = None) -> None:
        self.config = config or ExtractionAgentConfig()
        self.prompts = self._load_prompts()
        self._agent: Agent[None, ExtractionOutput] | None = None

        logger.info("ExtractionAgent initialized (Haiku 4.5)")

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
        """Default prompts for extraction agent."""
        return {
            "system_prompt": EXTRACTION_SYSTEM_PROMPT,
            "user_prompt": EXTRACTION_USER_PROMPT,
        }

    def _get_agent(self) -> Agent[None, ExtractionOutput]:
        """Get or create the extraction agent."""
        if self._agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            model = BedrockConverseModel(
                model_name=MODEL_TIER_MAP[ModelTier.EFFICIENT]
            )
            self._agent = Agent(
                model,
                output_type=ExtractionOutput,
                system_prompt=self.prompts["system_prompt"],
                retries=self.config.max_retries,
            )
        return self._agent

    async def extract(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
    ) -> tuple[str, float, LLMUsage]:
        """Extract markdown guided by manifest.

        Args:
            pages: List of page images from PDF conversion
            manifest: DocumentManifest from analysis phase
            job_id: Job identifier

        Returns:
            Tuple of (markdown_content, confidence, LLMUsage)
        """
        agent = self._get_agent()

        # Parse heading tree from manifest
        heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

        # Build messages with all page images
        messages = self._build_image_messages(pages)

        # Format heading tree for prompt
        heading_tree_text = self._format_heading_tree(heading_tree)

        # Format page features for prompt
        page_features_text = self._format_page_features(manifest.page_features)

        # Add user prompt with manifest context
        user_prompt = self.prompts["user_prompt"].format(
            total_pages=manifest.total_pages,
            document_title=manifest.document_title,
            document_type=manifest.document_type,
            heading_tree=heading_tree_text,
            page_features=page_features_text,
            layout_notes=manifest.analysis_notes,
        )
        messages.append(user_prompt)

        # Run agent
        result = await agent.run(
            messages,
            model_settings={
                "max_tokens": settings.claude_max_tokens,
                "temperature": self.config.temperature,
            }
        )

        # Extract usage
        usage_data = result.usage()
        input_tokens = usage_data.request_tokens or 0
        output_tokens = usage_data.response_tokens or 0

        usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost_cents=self._calculate_cost(input_tokens, output_tokens),
        )

        output = result.output

        logger.info(
            f"Extraction complete: {len(output.markdown)} chars, "
            f"confidence: {output.confidence:.2f}, "
            f"cost: ${usage.estimated_cost_cents/100:.4f}"
        )

        return output.markdown, output.confidence, usage

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

    def _format_heading_tree(self, tree: HeadingTree) -> str:
        """Format heading tree as readable text for the extraction prompt."""
        lines = [
            f"Document Title: {tree.document_title}",
            f"Layout: {tree.layout_type}",
            f"Total Pages: {tree.total_pages}",
            "",
            "Heading Structure (follow this exactly):",
        ]

        for section in tree.sections:
            indent = "  " * (section.level - 1)
            number = f"{section.section_number} " if section.section_number else ""
            lines.append(
                f"{indent}{'#' * section.level} {number}{section.title} (page {section.page})"
            )

        return "\n".join(lines)

    def _format_page_features(self, features: list) -> str:
        """Format page features for the extraction prompt."""
        lines = ["Page-by-Page Content:"]

        for pf in features:
            parts = [f"Page {pf.page_num}:"]

            if pf.has_images:
                parts.append(f"{pf.image_count} image(s)")
            if pf.has_tables:
                parts.append(f"{pf.table_count} table(s)")
            if pf.has_lists:
                parts.append("lists")
            if pf.has_code_blocks:
                parts.append("code")
            if pf.has_math:
                parts.append("math")

            parts.append(f"[{pf.layout_type}]")

            lines.append("  " + ", ".join(parts))

        return "\n".join(lines)

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in cents for Haiku."""
        # Haiku pricing (per 1M tokens)
        input_price = 0.80    # $0.80/1M input
        output_price = 4.00   # $4.00/1M output

        input_cost = (input_tokens / 1_000_000) * input_price * 100
        output_cost = (output_tokens / 1_000_000) * output_price * 100

        return input_cost + output_cost


# Prompt constants
EXTRACTION_SYSTEM_PROMPT = """You are a precise document transcription agent.
Your task is to convert PDF page images into clean, accessible markdown.

CRITICAL INSTRUCTIONS:

1. FOLLOW THE HEADING STRUCTURE EXACTLY
   - Use the heading hierarchy provided in the analysis
   - Do not create new headings or change levels
   - Match heading text exactly as specified

2. IMAGE HANDLING
   - For each image, use placeholder format: ![TODO: describe](image-page-X-N.png)
   - X = page number, N = image number on that page
   - A specialized agent will generate descriptions later

3. TABLE HANDLING
   - Preserve table structure as markdown tables
   - Use | column | separators |
   - Include header rows with | --- | dividers

4. TEXT TRANSCRIPTION
   - Transcribe all visible text accurately
   - Preserve paragraph breaks
   - Maintain list structures (ordered and unordered)
   - Keep code blocks with proper fencing

5. READING ORDER
   - Follow the layout type specified (single/two column)
   - For two-column: transcribe left column fully, then right
   - For mixed: follow natural reading flow

6. DO NOT:
   - Add commentary or explanations
   - Invent content not visible in images
   - Change the document structure
   - Skip any visible text content

Your output should be clean markdown that could be rendered directly.
"""

EXTRACTION_USER_PROMPT = """Transcribe this {total_pages}-page document to markdown.

DOCUMENT: {document_title}
TYPE: {document_type}

{heading_tree}

{page_features}

LAYOUT NOTES: {layout_notes}

INSTRUCTIONS:
1. Follow the heading structure exactly as shown above
2. For images, use: ![TODO: describe](image-page-X-N.png)
3. Preserve all tables as markdown tables
4. Transcribe all visible text accurately
5. Maintain proper reading order based on layout

Begin transcription:
"""
```

### Prompt Configuration

```yaml
# config/agents/extraction.yaml

system_prompt: |
  You are a precise document transcription agent converting PDF images to markdown.

  CORE PRINCIPLES:

  1. ACCURACY OVER CREATIVITY
     - Transcribe exactly what you see
     - Do not paraphrase or summarize
     - Preserve original wording

  2. STRUCTURE COMPLIANCE
     - Follow the provided heading hierarchy exactly
     - Do not add or modify heading levels
     - Match section organization from analysis

  3. IMAGE PLACEHOLDERS
     For every image in the document:
     - Use format: ![TODO: describe](image-page-{page}-{n}.png)
     - {page} = page number where image appears
     - {n} = sequential image number on that page (1, 2, 3...)
     - A specialized agent will add descriptions later

  4. TABLE FORMATTING
     - Use standard markdown table syntax
     - Preserve column alignment when visible
     - Include all header rows
     - Handle merged cells by repeating content or noting [merged]

  5. LIST HANDLING
     - Preserve ordered vs unordered distinction
     - Maintain nesting levels
     - Keep list item content intact

  6. CODE AND MATH
     - Use fenced code blocks with language hints
     - Preserve mathematical notation as-is (LaTeX if recognizable)
     - Mark unclear symbols with [?]

  7. READING ORDER
     - Single column: top to bottom
     - Two column: left column complete, then right column
     - Mixed: follow visual flow, prioritize main content

  OUTPUT FORMAT:
  - Clean markdown only
  - No explanatory comments
  - No meta-discussion about the transcription

user_prompt: |
  Transcribe this {total_pages}-page PDF document to accessible markdown.

  === DOCUMENT INFO ===
  Title: {document_title}
  Type: {document_type}

  === HEADING STRUCTURE (follow exactly) ===
  {heading_tree}

  === PAGE CONTENT SUMMARY ===
  {page_features}

  === ANALYSIS NOTES ===
  {layout_notes}

  === TRANSCRIPTION GUIDELINES ===
  1. Use headings exactly as structured above
  2. Images → ![TODO: describe](image-page-X-N.png)
  3. Tables → markdown table format
  4. Preserve all text content accurately
  5. Follow layout-appropriate reading order

  Begin your markdown transcription now:
```

### Integration with Processing Service

```python
# src/services/processing_service.py - Continued from PRD-012

async def process_document(
    self,
    job: ProcessingQueuePayload,
) -> ProcessingResult:
    """Process PDF using analysis + extraction pipeline."""
    start_time = time.time()

    try:
        # ... Phase 1: Analysis (from PRD-012) ...

        # Phase 2: Extraction (Haiku)
        logger.info(f"Job {job.job_id}: Starting extraction phase (Haiku)")

        extraction_agent = ExtractionAgent()
        markdown, extraction_confidence, extraction_usage = await extraction_agent.extract(
            pages=pages,
            manifest=manifest,
            job_id=job.job_id,
        )

        # Save initial markdown (v0)
        await self.storage.upload_result(
            job_id=job.job_id,
            content=markdown,
            format="md",
            suffix="v0"  # Original extraction before remediation
        )

        # Also save as current version
        result_url = await self.storage.upload_result(
            job_id=job.job_id,
            content=markdown,
            format="md",
        )

        logger.info(
            f"Job {job.job_id}: Extraction complete - "
            f"{len(markdown)} chars, "
            f"confidence: {extraction_confidence:.2f}, "
            f"cost: ${extraction_usage.estimated_cost_cents/100:.4f}"
        )

        # Calculate total usage
        total_usage = LLMUsage(
            input_tokens=analysis_usage.input_tokens + extraction_usage.input_tokens,
            output_tokens=analysis_usage.output_tokens + extraction_usage.output_tokens,
            total_tokens=analysis_usage.total_tokens + extraction_usage.total_tokens,
            estimated_cost_cents=analysis_usage.estimated_cost_cents + extraction_usage.estimated_cost_cents,
        )

        # Update job with extraction results
        await self.job.update_job_status(
            job.job_id,
            "processing",
            substatus="specializing",  # Ready for specialized agents
            markdown_url=result_url,
            extraction_model=MODEL_TIER_MAP[ModelTier.EFFICIENT],
            extraction_confidence=extraction_confidence,
            llm_cost_cents=total_usage.estimated_cost_cents,
            llm_input_tokens=total_usage.input_tokens,
            llm_output_tokens=total_usage.output_tokens,
        )

        # Phase 3: Specialized Agents (PRD-014)
        # ... continues in PRD-014 ...

    except Exception as e:
        # Error handling
        pass
```

### Version Management

```python
# S3 key structure for markdown versions

# Original extraction (never modified)
f"{job_id}-v0.md"

# Current working version (updated by remediation)
f"{job_id}.md"

# After remediation complete, optionally:
f"{job_id}-final.md"
```

## Acceptance Criteria

### 1. Extraction Agent
- [ ] Uses Haiku 4.5 (EFFICIENT tier)
- [ ] Accepts DocumentManifest as input
- [ ] Accepts all page images
- [ ] Outputs ExtractionOutput model

### 2. Manifest Integration
- [ ] Parses heading tree from manifest JSON
- [ ] Formats page features for prompt
- [ ] Includes layout notes in context

### 3. Output Quality
- [ ] Heading structure matches manifest exactly
- [ ] Image placeholders use correct format
- [ ] Tables preserved as markdown
- [ ] Reading order follows layout type

### 4. Image Placeholders
- [ ] Format: `![TODO: describe](image-page-X-N.png)`
- [ ] Page number correct
- [ ] Sequential numbering per page

### 5. Integration
- [ ] Processing service calls extraction agent
- [ ] Markdown saved to S3 (v0 and current)
- [ ] Usage tracked separately from analysis
- [ ] Substatus updated after extraction

### 6. Performance
- [ ] Extraction completes in <90s for 10-page doc
- [ ] Cost lower than Sonnet equivalent
- [ ] Memory usage acceptable

## Deliverables

### Files to Create

```
src/agents/
└── extraction_agent.py         # ExtractionAgent implementation

config/agents/
└── extraction.yaml             # Extraction prompts

tests/agents/
└── test_extraction_agent.py
```

### Files to Modify

```
src/services/processing_service.py  # Integrate extraction phase
```

## Technical Notes

### Haiku 4.5 Pricing (Bedrock)

```python
# Per 1M tokens
HAIKU_INPUT_PRICE = 0.80    # $0.80/1M input tokens
HAIKU_OUTPUT_PRICE = 4.00   # $4.00/1M output tokens

# For 52K input + 8K output (typical extraction):
# Input: (52000/1M) * $0.80 = $0.042
# Output: (8000/1M) * $4.00 = $0.032
# Total: ~$0.07 per document
```

### Comparison: Analysis + Extraction vs Current

| Approach | Analysis | Extraction | Total |
|----------|----------|------------|-------|
| Current (Haiku both) | $0.05 | $0.07 | $0.12 |
| New (Sonnet + Haiku) | $0.18 | $0.07 | $0.25 |

**Cost increase**: ~2x, but with significantly better analysis quality.

### Token Budget

Extraction output scales with document length:
- `max_tokens`: Use `settings.claude_max_tokens` (typically 16384)
- Average: ~800 tokens per page
- 10-page doc: ~8000 output tokens

### Prompt Design

The extraction prompt is intentionally directive:
- Explicit heading structure to follow
- Clear image placeholder format
- Specific table handling instructions
- No room for interpretation

This allows Haiku to focus on accurate transcription rather than making analytical decisions.

## Definition of Done

- [ ] ExtractionAgent implemented with Haiku 4.5
- [ ] Manifest-guided extraction working
- [ ] Image placeholders generated correctly
- [ ] Table structure preserved
- [ ] Integration with processing service complete
- [ ] v0 markdown saved to S3
- [ ] Cost tracking accurate
- [ ] Performance meets targets
- [ ] Unit tests pass
- [ ] Integration tests pass
