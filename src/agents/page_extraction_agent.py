"""Page Extraction Agent for direct image-to-markdown conversion.

This agent implements a simplified architecture that converts page images
directly to markdown with surrounding page context (±1 page), replacing
the Docling extraction + AI correction approach.

Key differences from correction-based agents:
- Receives page images (not Docling's markdown)
- Outputs complete markdown for the current page
- Uses 3-page context for heading hierarchy continuity
- No comparison step = no hallucination of "corrections"
"""

import base64
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_ai.messages import BinaryContent

from src.config import settings
from src.services.pdf_converter import PageData
from src.shared.llm_cost import calculate_estimated_cost
from src.shared.models.context_models import DocumentContext
from src.shared.models.processing import LLMUsage

from .base_agent import AgentConfig, BaseDocumentAgent

logger = logging.getLogger(__name__)


class PageMarkdown(BaseModel):
    """Output from page extraction agent."""

    markdown: str = Field(..., description="Complete markdown for the page")
    heading_structure: list[str] = Field(
        default_factory=list,
        description="Headings found on this page with levels (e.g., '## Section Title')",
    )
    continues_from_previous: bool = Field(
        default=False, description="True if content continues from previous page"
    )
    continues_to_next: bool = Field(
        default=False, description="True if content continues to next page"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    observations: list[str] = Field(default_factory=list)

    @field_validator("heading_structure", "observations", mode="before")
    @classmethod
    def coerce_to_list(cls, v: Any) -> list[str]:
        """Coerce string values to lists for robust AI output handling."""
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, list):
            return v
        return []


class PageExtractionAgent(BaseDocumentAgent[PageMarkdown]):
    """Agent that converts page images directly to semantic markdown.

    Uses 3-page context (previous, current, next) for:
    - Heading hierarchy continuity
    - Content that spans page boundaries
    - Consistent document structure

    Unlike the correction-based approach, this agent:
    - Receives page images (not Docling's markdown)
    - Outputs complete markdown for the current page
    - Uses document context for consistent heading hierarchy
    - Does NOT compare to any existing extraction
    """

    CORRECTION_TYPES = ["content_generation"]  # Generates, doesn't correct

    def __init__(self) -> None:
        config = AgentConfig(
            name="page_extraction",
            prompts_file=Path("config/agents/page_extraction.yaml"),
            output_type=PageMarkdown,
            correction_types=self.CORRECTION_TYPES,
            max_retries=2,
            temperature=0.2,
        )
        super().__init__(config)

    def _default_prompts(self) -> dict[str, Any]:
        """Provide fallback prompts when YAML file is not found."""
        return {
            "system_prompt": """You are a document conversion specialist. Convert PDF page images to semantic markdown.

You will receive UP TO THREE page images:
1. PREVIOUS PAGE (for context - what came before)
2. CURRENT PAGE (convert this one to markdown)
3. NEXT PAGE (for context - what comes after)

Use the previous and next pages to understand:
- Heading hierarchy (if previous page ends with H2, a new section might be H2 or H3)
- Content continuity (paragraphs, lists, or sentences that span pages)
- Document flow and logical structure

HEADING HIERARCHY RULES:
- H1 (#): Document title - usually only on page 1
- H2 (##): Major sections
- H3 (###): Subsections within H2
- H4+ (####): Deeper nesting as needed
- If previous page ends mid-section, continue at the same heading level
- If a new section starts, determine level based on visual prominence and context

MARKDOWN CONVERSION RULES:
- Preserve visual hierarchy: larger/bolder text = higher heading level
- Convert bullet points (•, ○, ▪, -, *) to markdown lists (-)
- Convert numbered items (1., 2., a., i.) to markdown numbered lists (1.)
- Preserve emphasis: bold text = **bold**, italic = *italic*
- Tables: use proper markdown table syntax with | and -
- Images/figures: use ![Figure N: brief description](figure-N.png)
- Maintain reading order as presented visually (columns left-to-right if applicable)

HANDLING PAGE BOUNDARIES:
- If a sentence or paragraph continues from the previous page, start with the continuation
- If content continues to the next page, end naturally (don't add artificial breaks)
- Note continuation in your observations

CONVERT ONLY THE CURRENT PAGE. Use previous/next pages for context only.""",
            "user_prompt_template": """Convert the CURRENT PAGE (labeled image) to semantic markdown.

**Page Position:** Page {page_number} of {total_pages}

**Document Context:**
{document_context}

**Instructions:**
1. Review all provided images to understand document flow and structure
2. Note heading levels on previous page to maintain hierarchy
3. Convert ONLY the CURRENT PAGE to complete semantic markdown
4. Ensure heading levels are consistent with surrounding pages
5. Handle content that continues from/to adjacent pages appropriately

**Return:**
- Complete markdown for the current page
- List of headings found (with their levels)
- Whether content continues from previous or to next page
- Confidence score (0.0-1.0)
- Brief observations about structure decisions made""",
        }

    async def process(  # type: ignore[override]
        self,
        current_page: PageData,
        previous_page: PageData | None,
        next_page: PageData | None,
        document_context: DocumentContext | None,
        total_pages: int,
    ) -> tuple[PageMarkdown, LLMUsage]:
        """Convert page image to markdown with surrounding context.

        Args:
            current_page: The page to convert (required)
            previous_page: Previous page for context (None if page 1)
            next_page: Next page for context (None if last page)
            document_context: Heading hierarchy, doc type, etc.
            total_pages: Total number of pages in document

        Returns:
            Tuple of (PageMarkdown, LLMUsage)
        """
        # Build multimodal message with up to 3 images
        messages = self._build_multimodal_message(
            current_page, previous_page, next_page, document_context, total_pages
        )

        result, usage = await self._run_agent_multimodal(messages)

        logger.info(
            f"Page {current_page.page_num} extraction: "
            f"input_tokens={usage.input_tokens}, "
            f"output_tokens={usage.output_tokens}, "
            f"total_tokens={usage.total_tokens}, "
            f"estimated_cost_cents={usage.estimated_cost_cents:.4f}, "
            f"images_sent={1 + (1 if previous_page else 0) + (1 if next_page else 0)}"
        )

        return result, usage

    def _build_multimodal_message(
        self,
        current_page: PageData,
        previous_page: PageData | None,
        next_page: PageData | None,
        document_context: DocumentContext | None,
        total_pages: int,
    ) -> list[str | BinaryContent]:
        """Build message with up to 3 page images + context."""
        messages: list[str | BinaryContent] = []

        # Add previous page image if available
        if previous_page and previous_page.image_base64:
            messages.append(f"[PREVIOUS PAGE - Page {previous_page.page_num}]")
            image_bytes = base64.b64decode(previous_page.image_base64)
            messages.append(BinaryContent(data=image_bytes, media_type="image/png"))

        # Add current page image (required)
        messages.append(
            f"[CURRENT PAGE - Page {current_page.page_num}] (CONVERT THIS ONE)"
        )
        if current_page.image_base64:
            image_bytes = base64.b64decode(current_page.image_base64)
            messages.append(BinaryContent(data=image_bytes, media_type="image/png"))
        else:
            raise ValueError(
                f"Current page {current_page.page_num} has no image - "
                "PageExtractionAgent requires page images"
            )

        # Add next page image if available
        if next_page and next_page.image_base64:
            messages.append(f"[NEXT PAGE - Page {next_page.page_num}]")
            image_bytes = base64.b64decode(next_page.image_base64)
            messages.append(BinaryContent(data=image_bytes, media_type="image/png"))

        # Add formatted user prompt
        messages.append(
            self._format_user_prompt(
                current_page, previous_page, next_page, document_context, total_pages
            )
        )

        return messages

    def _format_user_prompt(
        self,
        current_page: PageData,
        previous_page: PageData | None,
        next_page: PageData | None,
        document_context: DocumentContext | None,
        total_pages: int,
    ) -> str:
        """Format user prompt with page and context information."""
        # Format document context
        context_str = self._format_document_context(
            document_context, current_page.page_num
        )

        return self.prompts["user_prompt_template"].format(
            page_number=current_page.page_num,
            total_pages=total_pages,
            document_context=context_str,
        )

    def _format_document_context(
        self, context: DocumentContext | None, page_number: int
    ) -> str:
        """Format document context for prompt injection."""
        if not context:
            return "No document context available (first page or no previous analysis)"

        lines: list[str] = []

        # Document type
        lines.append(f"- Document type: {context.document_type.value}")

        # Title if available
        if context.title:
            lines.append(f"- Document title: {context.title}")

        # Current section context
        section = context.get_current_section_context(page_number)
        active_sections = [
            (level, text) for level, text in section.items() if text is not None
        ]
        if active_sections:
            lines.append("- Current section hierarchy:")
            for level, text in active_sections:
                indent = "  " * (int(level[1]) - 1)
                lines.append(f"  {indent}{level.upper()}: {text}")

        # Expected heading level
        expected_level = context.get_expected_heading_level(page_number)
        lines.append(f"- Expected heading level for new sections: H{expected_level}")

        # Figure/table numbering context
        next_fig = context.get_next_figure_number(page_number)
        next_table = context.get_next_table_number(page_number)
        lines.append(f"- Next figure number: Figure {next_fig}")
        lines.append(f"- Next table number: Table {next_table}")

        # Single H1 warning
        if context.has_single_h1:
            lines.append("- Note: Document has single H1 title - maintain hierarchy")

        return "\n".join(lines)

    async def _run_agent_multimodal(
        self, messages: list[str | BinaryContent]
    ) -> tuple[PageMarkdown, LLMUsage]:
        """Execute the PydanticAI agent with multimodal input.

        This is a specialized version of _run_agent that accepts a pre-built
        message list with multiple images.

        Args:
            messages: List containing text and BinaryContent items

        Returns:
            Tuple of (PageMarkdown output, LLMUsage with token counts and cost)
        """
        logger.debug(
            f"Agent {self.name}: Running multimodal with {len(messages)} message parts"
        )

        # Execute agent
        result = await self._agent.run(
            messages,
            model_settings={
                "max_tokens": settings.claude_max_tokens,
                "temperature": self.config.temperature,
            },
        )

        # Extract usage metrics
        usage = result.usage()
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        total_tokens = input_tokens + output_tokens
        estimated_cost_cents = calculate_estimated_cost(input_tokens, output_tokens)

        llm_usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
        )

        logger.debug(
            f"Agent {self.name}: Completed "
            f"(tokens: {input_tokens}/{output_tokens}, est. cost: ${estimated_cost_cents/100:.6f})"
        )

        return result.output, llm_usage


# Singleton instance
_page_extraction_agent: PageExtractionAgent | None = None


def get_page_extraction_agent() -> PageExtractionAgent:
    """Get or create page extraction agent singleton."""
    global _page_extraction_agent
    if _page_extraction_agent is None:
        _page_extraction_agent = PageExtractionAgent()
    return _page_extraction_agent


__all__ = ["PageExtractionAgent", "PageMarkdown", "get_page_extraction_agent"]
