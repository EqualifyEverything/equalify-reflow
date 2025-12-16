"""Extraction Agent for guided markdown extraction.

This agent performs manifest-guided markdown extraction using Claude Haiku 4.5
(EFFICIENT tier) to transcribe PDF documents to accessible markdown.

The Extraction Agent:
1. Receives DocumentManifest with structure analysis from Analysis phase
2. Receives all page images
3. Transcribes content following the manifest structure
4. Produces markdown with image placeholders for specialized agents

Uses Claude Haiku 4.5 for cost-efficient transcription.
The hard analytical work was done by the Analysis agent (Sonnet).

Supports dynamic instructions via AgentDependencies for:
- Document type hints (syllabus, exam, lecture_notes)
- Retry guidance based on previous failures
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier
from src.config import settings
from src.services.debug_logging_service import debug_logger
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, HeadingTree, PageFeatures
from src.utils.prompt_sanitizer import sanitize_for_prompt

if TYPE_CHECKING:
    pass

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
# Extraction Agent
# =============================================================================


class ExtractionAgent(BaseDocumentAgent[ExtractionOutput]):
    """Agent that performs guided markdown extraction using Haiku.

    This agent:
    1. Receives DocumentManifest with structure analysis
    2. Receives all page images
    3. Transcribes content following the manifest structure
    4. Produces markdown with image placeholders for specialized agents

    Uses Claude Haiku 4.5 for cost-efficient transcription.
    The hard analytical work was done by the Analysis agent (Sonnet).

    Supports dynamic instructions for:
    - Document type hints (syllabus, exam, lecture_notes)
    - Retry guidance based on previous failures

    Example:
        >>> agent = ExtractionAgent()
        >>> markdown, confidence, usage = await agent.extract(pages, manifest, job_id)
        >>> print(f"Extracted {len(markdown)} chars")
    """

    # Document type hints for dynamic instructions
    DOCUMENT_TYPE_HINTS: dict[str, str] = {
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
        "assignment": (
            "This is an assignment. Pay attention to:\n"
            "- Question/problem numbering\n"
            "- Point values and rubric info\n"
            "- Submission requirements\n"
        ),
    }

    def __init__(self, config: AgentConfig | None = None) -> None:
        """Initialize the extraction agent.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        if config is None:
            config = AgentConfig(
                name="extraction_agent",
                prompts_file=Path("extraction.yaml"),
                output_type=ExtractionOutput,
                model_tier=ModelTier.EFFICIENT,
                max_retries=2,
                temperature=0.2,
                max_tokens=16384,
                use_deps=True,  # Enable dynamic instructions
            )
        super().__init__(config)
        self._instructions_registered = False
        logger.info(
            f"ExtractionAgent initialized with model tier {self.model_tier.value} "
            f"({self.model_id}), dynamic instructions enabled"
        )

    def _get_agent(self) -> Agent[AgentDependencies, ExtractionOutput]:  # type: ignore[override]
        """Get or create agent with dynamic instructions registered.

        Overrides base class to register dynamic instructions after agent creation.
        """
        agent = super()._get_agent()

        # Register dynamic instructions only once
        if not self._instructions_registered:
            self._register_dynamic_instructions(agent)  # type: ignore[arg-type]
            self._instructions_registered = True

        return agent  # type: ignore[return-value]

    def _register_dynamic_instructions(
        self, agent: Agent[AgentDependencies, ExtractionOutput]
    ) -> None:
        """Register dynamic instruction generators on the agent.

        These instructions are evaluated at runtime and can access
        AgentDependencies via ctx.deps to provide context-aware guidance.
        """

        @agent.instructions
        def document_type_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Provide document-type-specific extraction tips."""
            doc_type = ctx.deps.document_type
            if doc_type and doc_type in self.DOCUMENT_TYPE_HINTS:
                hint = self.DOCUMENT_TYPE_HINTS[doc_type]
                return f"""
<document_type type="{doc_type.upper()}">
{hint}
</document_type>"""
            return ""

        @agent.instructions
        def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Provide guidance for retry attempts."""
            if not ctx.deps.is_retry:
                return ""

            # Show last 3 failures
            failures = "\n".join(
                f"- {f}" for f in ctx.deps.previous_failures[-3:]
            )
            return f"""
<retry_attempt number="{ctx.deps.attempt_number}">
Previous attempts encountered issues:
{failures}

Adjust your approach to avoid these issues.
</retry_attempt>"""

        logger.debug("ExtractionAgent: Dynamic instructions registered")

    async def process(self, input_data: Any) -> ExtractionOutput:
        """Process method required by BaseDocumentAgent.

        For ExtractionAgent, use extract() method instead.
        """
        raise NotImplementedError("ExtractionAgent uses extract() method, not process()")

    async def extract(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
        deps: AgentDependencies | None = None,
    ) -> tuple[str, float, LLMUsage]:
        """Extract markdown guided by manifest.

        Args:
            pages: List of page images from PDF conversion
            manifest: DocumentManifest from analysis phase
            job_id: Job identifier
            deps: Optional AgentDependencies for dynamic instructions.
                  If not provided, creates default deps from manifest.

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

        # Create or use provided deps
        if deps is None:
            deps = AgentDependencies(
                job_id=job_id,
                manifest=manifest,
                document_type=manifest.document_type,
            )

        # Ensure agent is created (triggers instruction registration)
        self._get_agent()

        # Parse heading tree from manifest
        heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

        # Build messages with all page images
        messages = self._build_image_messages(pages)

        # Save debug images if enabled
        images_to_save = [
            (base64.b64decode(p.image_base64), p.page_num)
            for p in pages if p.image_base64
        ]
        if images_to_save:
            debug_logger.save_debug_images_batch(
                job_id=job_id,
                agent_name="extraction_agent",
                images=images_to_save,
            )

        # Format heading tree for prompt
        heading_tree_text = self._format_heading_tree(heading_tree)

        # Format page features for prompt
        page_features_text = self._format_page_features(manifest.page_features)

        # Build user prompt with manifest context
        # SECURITY: Sanitize user-influenced fields to prevent prompt injection
        # document_title and document_type come from PDF metadata (attacker-controlled)
        # layout_notes may contain extracted text content
        user_prompt = self.prompts["user_prompt"].format(
            total_pages=manifest.total_pages,
            document_title=sanitize_for_prompt(
                manifest.document_title,
                max_length=200,
                context="document_title",
            ),
            document_type=sanitize_for_prompt(
                manifest.document_type,
                max_length=50,
                context="document_type",
            ),
            heading_tree=heading_tree_text,
            page_features=page_features_text,
            layout_notes=sanitize_for_prompt(
                manifest.analysis_notes or "No additional notes.",
                max_length=500,
                context="layout_notes",
            ),
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

        # Run agent with deps for dynamic instructions
        start_time = time.time()
        agent = self._get_agent()
        result = await agent.run(
            messages,
            deps=deps,
            model_settings={
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        )
        duration_ms = (time.time() - start_time) * 1000

        # Use inherited usage tracking
        usage = self._core.create_llm_usage(result)

        output = result.output

        # Debug log: response received
        debug_logger.log_response(
            job_id=job_id,
            agent_name="extraction_agent",
            response_text=output.markdown[:1000] if output.markdown else None,
            parsed_output={"confidence": output.confidence, "notes": output.notes},
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost_cents=usage.estimated_cost_cents,
            duration_ms=duration_ms,
            model_id=self.model_id,
        )

        logger.info(
            f"Extraction complete for job {job_id}: "
            f"{len(output.markdown)} chars, "
            f"confidence: {output.confidence:.2f}, "
            f"cost: ${usage.estimated_cost_cents/100:.4f}"
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


__all__ = [
    "ExtractionAgent",
    "ExtractionOutput",
]
