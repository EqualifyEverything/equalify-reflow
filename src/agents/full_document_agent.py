"""Full document extraction agent using two-phase approach.

This agent processes entire documents by:
1. Phase 1: Analyzing structure to build a heading tree
2. Phase 2: Guided transcription using the heading tree

This avoids duplicate content issues that occur with sliding window approaches
on multi-column documents.
"""

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from src.config import settings
from src.services.pdf_converter import PageData
from src.shared.llm_cost import calculate_estimated_cost
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Maximum pages before requiring chunked processing
MAX_PAGES_FULL_CONTEXT = 15


# =============================================================================
# Output Models
# =============================================================================


class HeadingNode(BaseModel):
    """A single heading in the document structure."""

    level: int = Field(..., ge=1, le=6, description="Heading level (1-6)")
    title: str = Field(..., description="Heading text as it appears")
    page: int = Field(..., ge=1, description="Page number where heading appears")
    section_number: str | None = Field(
        default=None, description="Section number if present (e.g., '1', '2.1', 'A')"
    )


class HeadingTree(BaseModel):
    """Document heading structure from Phase 1 analysis."""

    document_title: str = Field(..., description="Main document title (H1)")
    title_page: int = Field(default=1, description="Page where title appears")
    sections: list[HeadingNode] = Field(
        default_factory=list, description="All headings in document order"
    )
    total_pages: int = Field(..., description="Total pages in document")
    layout_type: str = Field(
        default="single_column",
        description="Detected layout: single_column, two_column, or mixed",
    )
    confidence: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Confidence in structure analysis"
    )
    observations: str = Field(
        default="", description="Notes about document structure"
    )


class DocumentMarkdown(BaseModel):
    """Full document markdown from Phase 2 transcription."""

    markdown: str = Field(..., description="Complete markdown transcription")
    confidence: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Confidence in transcription"
    )
    observations: str = Field(
        default="", description="Notes about transcription decisions"
    )


# =============================================================================
# Agent Configuration
# =============================================================================


@dataclass
class FullDocumentAgentConfig:
    """Configuration for the full document agent."""

    prompts_file: Path = field(
        default_factory=lambda: Path("full_document_extraction.yaml")
    )
    max_retries: int = 2
    temperature: float = 0.2


# =============================================================================
# Full Document Agent
# =============================================================================


class FullDocumentAgent:
    """Agent that processes entire documents in two phases.

    Phase 1: Structure Analysis
        - Receives all page images
        - Outputs HeadingTree with document structure

    Phase 2: Guided Transcription
        - Receives all page images + HeadingTree
        - Outputs complete DocumentMarkdown

    This approach eliminates duplicate content issues that occur when
    processing pages individually with overlapping context windows.
    """

    def __init__(self, config: FullDocumentAgentConfig | None = None) -> None:
        """Initialize the agent with configuration."""
        self.config = config or FullDocumentAgentConfig()
        self.prompts = self._load_prompts()

        # Create separate agents for each phase
        self._structure_agent: Agent[None, HeadingTree] | None = None
        self._transcription_agent: Agent[None, DocumentMarkdown] | None = None

        logger.info("FullDocumentAgent initialized")

    def _load_prompts(self) -> dict:
        """Load prompts from YAML configuration file."""
        prompts_file = self.config.prompts_file
        if not prompts_file.is_absolute():
            prompts_file = Path(settings.agent_prompts_dir) / prompts_file

        try:
            with open(prompts_file) as f:
                prompts = yaml.safe_load(f)
                logger.debug(f"Loaded prompts from {prompts_file}")
                return dict(prompts)
        except FileNotFoundError:
            logger.error(f"Prompts file not found: {prompts_file}")
            raise

    def _get_structure_agent(self) -> Agent[None, HeadingTree]:
        """Get or create the structure analysis agent."""
        if self._structure_agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            model = BedrockConverseModel(model_name=settings.bedrock_model_id)
            self._structure_agent = Agent(
                model,
                output_type=HeadingTree,
                system_prompt=self.prompts["structure_system_prompt"],
                retries=self.config.max_retries,
            )
        return self._structure_agent

    def _get_transcription_agent(self) -> Agent[None, DocumentMarkdown]:
        """Get or create the transcription agent."""
        if self._transcription_agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            model = BedrockConverseModel(model_name=settings.bedrock_model_id)
            self._transcription_agent = Agent(
                model,
                output_type=DocumentMarkdown,
                system_prompt=self.prompts["transcription_system_prompt"],
                retries=self.config.max_retries,
            )
        return self._transcription_agent

    def _calculate_estimated_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost in cents using centralized pricing.

        Uses the centralized calculate_estimated_cost function with default
        Claude Haiku 4.5 pricing from src.shared.llm_cost.

        Args:
            input_tokens: Number of input tokens consumed
            output_tokens: Number of output tokens generated

        Returns:
            Estimated cost in cents (float)
        """
        return calculate_estimated_cost(input_tokens, output_tokens)

    def _build_image_messages(
        self, pages: list[PageData]
    ) -> list[str | BinaryContent]:
        """Build message list with all page images."""
        messages: list[str | BinaryContent] = []

        for page in pages:
            messages.append(f"[Page {page.page_num}]")
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                messages.append(BinaryContent(data=image_bytes, media_type="image/png"))

        return messages

    def _format_heading_tree(self, tree: HeadingTree) -> str:
        """Format heading tree as readable text for the transcription prompt."""
        lines = [
            f"Document: {tree.document_title}",
            f"Layout: {tree.layout_type}",
            f"Total Pages: {tree.total_pages}",
            "",
            "Heading Structure:",
        ]

        for section in tree.sections:
            indent = "  " * (section.level - 1)
            number = f"{section.section_number} " if section.section_number else ""
            lines.append(
                f"{indent}{'#' * section.level} {number}{section.title} (page {section.page})"
            )

        return "\n".join(lines)

    async def process(
        self, pages: list[PageData]
    ) -> tuple[str, HeadingTree, LLMUsage]:
        """Process entire document and return markdown.

        Args:
            pages: List of PageData with page images

        Returns:
            Tuple of (markdown_content, heading_tree, total_usage)

        Raises:
            ValueError: If document exceeds MAX_PAGES_FULL_CONTEXT
        """
        total_pages = len(pages)

        if total_pages > MAX_PAGES_FULL_CONTEXT:
            raise ValueError(
                f"Document has {total_pages} pages, exceeding the maximum of "
                f"{MAX_PAGES_FULL_CONTEXT} pages. Chunked processing not yet implemented."
            )

        if total_pages == 0:
            raise ValueError("No pages provided")

        logger.info(f"Processing {total_pages}-page document with full context approach")

        total_usage = LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0)

        # Phase 1: Structure Analysis
        logger.info("Phase 1: Analyzing document structure...")
        heading_tree, phase1_usage = await self._analyze_structure(pages)
        total_usage.input_tokens += phase1_usage.input_tokens
        total_usage.output_tokens += phase1_usage.output_tokens
        total_usage.total_tokens += phase1_usage.total_tokens
        total_usage.estimated_cost_cents += phase1_usage.estimated_cost_cents

        logger.info(
            f"Phase 1 complete: Found {len(heading_tree.sections)} sections, "
            f"layout={heading_tree.layout_type}, est. cost=${phase1_usage.estimated_cost_cents/100:.4f}"
        )

        # Phase 2: Guided Transcription
        logger.info("Phase 2: Transcribing document with heading guidance...")
        markdown_result, phase2_usage = await self._transcribe_document(
            pages, heading_tree
        )
        total_usage.input_tokens += phase2_usage.input_tokens
        total_usage.output_tokens += phase2_usage.output_tokens
        total_usage.total_tokens += phase2_usage.total_tokens
        total_usage.estimated_cost_cents += phase2_usage.estimated_cost_cents

        logger.info(
            f"Phase 2 complete: Generated {len(markdown_result.markdown)} chars, "
            f"confidence={markdown_result.confidence:.2f}, est. cost=${phase2_usage.estimated_cost_cents/100:.4f}"
        )

        logger.info(
            f"Document processing complete. Est. total cost: ${total_usage.estimated_cost_cents/100:.4f}"
        )

        return markdown_result.markdown, heading_tree, total_usage

    async def _analyze_structure(
        self, pages: list[PageData]
    ) -> tuple[HeadingTree, LLMUsage]:
        """Phase 1: Analyze document structure to build heading tree."""
        agent = self._get_structure_agent()

        # Build messages with all page images
        messages = self._build_image_messages(pages)

        # Add user prompt
        user_prompt = self.prompts["structure_user_prompt"].format(
            total_pages=len(pages)
        )
        messages.append(user_prompt)

        # Run agent
        result = await agent.run(messages)

        # Extract usage
        usage_data = result.usage()
        input_tokens = usage_data.request_tokens or 0
        output_tokens = usage_data.response_tokens or 0
        total_tokens = input_tokens + output_tokens
        estimated_cost_cents = self._calculate_estimated_cost(input_tokens, output_tokens)

        usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
        )

        # Set total_pages on the result
        result.output.total_pages = len(pages)

        return result.output, usage

    async def _transcribe_document(
        self, pages: list[PageData], heading_tree: HeadingTree
    ) -> tuple[DocumentMarkdown, LLMUsage]:
        """Phase 2: Transcribe document guided by heading tree."""
        agent = self._get_transcription_agent()

        # Build messages with all page images
        messages = self._build_image_messages(pages)

        # Format heading tree for prompt
        heading_tree_text = self._format_heading_tree(heading_tree)

        # Add user prompt
        user_prompt = self.prompts["transcription_user_prompt"].format(
            total_pages=len(pages),
            heading_tree=heading_tree_text,
        )
        messages.append(user_prompt)

        # Run agent
        result = await agent.run(messages)

        # Extract usage
        usage_data = result.usage()
        input_tokens = usage_data.request_tokens or 0
        output_tokens = usage_data.response_tokens or 0
        total_tokens = input_tokens + output_tokens
        estimated_cost_cents = self._calculate_estimated_cost(input_tokens, output_tokens)

        usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
        )

        return result.output, usage
