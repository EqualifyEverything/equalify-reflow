"""Extraction Agent for guided markdown extraction (PRD-013).

This agent performs manifest-guided markdown extraction using Claude Haiku 4.5
(EFFICIENT tier) to transcribe PDF documents to accessible markdown.

The Extraction Agent:
1. Receives DocumentManifest with structure analysis from Analysis phase
2. Receives all page images
3. Transcribes content following the manifest structure
4. Produces markdown with image placeholders for specialized agents

Uses Claude Haiku 4.5 for cost-efficient transcription.
The hard analytical work was done by the Analysis agent (Sonnet).
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.services.debug_logging_service import debug_logger
from src.services.pdf_converter import PageData
from src.shared.llm_cost import HAIKU_PRICING, calculate_estimated_cost
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, HeadingTree, PageFeatures

logger = logging.getLogger(__name__)


# =============================================================================
# Output Models
# =============================================================================


class ExtractionOutput(BaseModel):
    """Structured output from extraction phase."""

    markdown: str = Field(
        ..., description="Complete markdown transcription of the document"
    )
    confidence: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Confidence in transcription accuracy",
    )
    notes: str = Field(
        default="", description="Notes about transcription decisions or issues"
    )


# =============================================================================
# Agent Configuration
# =============================================================================


@dataclass
class ExtractionAgentConfig:
    """Configuration for the extraction agent."""

    prompts_file: Path = field(default_factory=lambda: Path("extraction.yaml"))
    max_retries: int = 2
    temperature: float = 0.2  # Low temperature for consistent transcription
    max_tokens: int = 16384  # Large output for full document transcription


# =============================================================================
# Extraction Agent
# =============================================================================


class ExtractionAgent:
    """Agent that performs guided markdown extraction using Haiku.

    This agent:
    1. Receives DocumentManifest with structure analysis
    2. Receives all page images
    3. Transcribes content following the manifest structure
    4. Produces markdown with image placeholders for specialized agents

    Uses Claude Haiku 4.5 for cost-efficient transcription.
    The hard analytical work was done by the Analysis agent (Sonnet).

    Example:
        >>> agent = ExtractionAgent()
        >>> markdown, confidence, usage = await agent.extract(pages, manifest, job_id)
        >>> print(f"Extracted {len(markdown)} chars")
    """

    def __init__(self, config: ExtractionAgentConfig | None = None) -> None:
        """Initialize the extraction agent.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self.config = config or ExtractionAgentConfig()
        self.model_tier = ModelTier.EFFICIENT
        self.model_id = MODEL_TIER_MAP[self.model_tier]
        self.prompts = self._load_prompts()
        self._agent: Agent[None, ExtractionOutput] | None = None

        logger.info(
            f"ExtractionAgent initialized with model tier {self.model_tier.value} "
            f"({self.model_id})"
        )

    def _load_prompts(self) -> dict[str, Any]:
        """Load prompts from YAML configuration file."""
        prompts_file = self.config.prompts_file
        if not prompts_file.is_absolute():
            prompts_file = Path(settings.agent_prompts_dir) / prompts_file

        try:
            with open(prompts_file) as f:
                prompts: dict[str, Any] = yaml.safe_load(f)
                logger.debug(f"Loaded prompts from {prompts_file}")
                return prompts
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

            model = BedrockConverseModel(model_name=self.model_id)
            self._agent = Agent(
                model,
                output_type=ExtractionOutput,
                system_prompt=self.prompts["system_prompt"],
                retries=self.config.max_retries,
            )
            logger.debug(f"Created extraction agent with model {self.model_id}")
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

        Raises:
            ValueError: If no pages provided
            RuntimeError: If extraction fails after retries
        """
        if not pages:
            raise ValueError("No pages provided for extraction")

        logger.info(
            f"Starting extraction for job {job_id}: "
            f"{len(pages)} pages, manifest title='{manifest.document_title}'"
        )

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
            layout_notes=manifest.analysis_notes or "No additional notes.",
        )
        messages.append(user_prompt)

        # Debug log: prompt being sent
        image_info = None
        if settings.debug_log_images:
            image_info = {
                "page_count": len(pages),
                "pages_with_images": sum(1 for p in pages if p.image_base64),
            }

        debug_logger.log_prompt(
            job_id=job_id,
            agent_name="extraction_agent",
            system_prompt=self.prompts.get("system_prompt"),
            user_message=user_prompt,
            image_info=image_info,
            model_id=self.model_id,
            model_tier=self.model_tier.value,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

        # Run agent
        start_time = time.time()
        result = await agent.run(
            messages,
            model_settings={
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        )
        duration_ms = (time.time() - start_time) * 1000

        # Extract usage
        usage_data = result.usage()
        input_tokens = usage_data.request_tokens or 0
        output_tokens = usage_data.response_tokens or 0
        total_tokens = input_tokens + output_tokens

        estimated_cost_cents = calculate_estimated_cost(
            input_tokens, output_tokens, HAIKU_PRICING
        )

        usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
        )

        output = result.output

        # Debug log: response received
        debug_logger.log_response(
            job_id=job_id,
            agent_name="extraction_agent",
            response_text=output.markdown[:1000] if output.markdown else None,  # Log start of markdown
            parsed_output={"confidence": output.confidence, "notes": output.notes},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
            duration_ms=duration_ms,
            model_id=self.model_id,
        )

        logger.info(
            f"Extraction complete for job {job_id}: "
            f"{len(output.markdown)} chars, "
            f"confidence: {output.confidence:.2f}, "
            f"cost: ${estimated_cost_cents/100:.4f}"
        )

        return output.markdown, output.confidence, usage

    def _build_image_messages(
        self, pages: list[PageData]
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

    def _format_page_features(self, features: list[PageFeatures]) -> str:
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


# =============================================================================
# Default Prompts (fallback if YAML not found)
# =============================================================================

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


__all__ = [
    "ExtractionAgent",
    "ExtractionAgentConfig",
    "ExtractionOutput",
]
