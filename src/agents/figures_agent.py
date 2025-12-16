"""FiguresAgent for image accessibility analysis.

Specialized agent for analyzing images in PDF documents:
- Classifies images (decorative, informative, complex, text)
- Evaluates current alt text quality
- Generates appropriate alt text suggestions
- Identifies images needing long descriptions

Supports dynamic instructions via AgentDependencies for:
- Document context (title, type, total pages)
- Page-specific context (expected images, layout)
- Retry guidance based on previous failures
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic_ai import Agent, RunContext

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import FiguresAnalysisOutput, ImageAnalysis
from src.config import settings
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures

logger = logging.getLogger(__name__)


class FiguresAgent(BaseDocumentAgent[FiguresAnalysisOutput]):
    """Agent specialized in image accessibility analysis.

    Focuses on:
    - Classifying images (decorative, informative, complex, text)
    - Evaluating current alt text quality
    - Generating appropriate alt text suggestions
    - Identifying images needing long descriptions

    Uses Sonnet (REASONING tier) for accurate visual analysis.

    Supports dynamic instructions for:
    - Document context (title, type)
    - Page-specific context (expected images, layout)
    - Retry guidance based on previous failures
    """

    def __init__(self) -> None:
        """Initialize FiguresAgent with configuration."""
        config = AgentConfig(
            name="figures_agent",
            prompts_file=Path("figures.yaml"),
            output_type=FiguresAnalysisOutput,
            correction_types=["alt_text", "figure_caption", "long_description"],
            max_retries=2,
            temperature=0.3,
            model_tier=ModelTier.REASONING,
            use_deps=True,  # Enable dynamic instructions
        )
        super().__init__(config)
        self._instructions_registered = False

    def _get_agent(self) -> Agent[AgentDependencies, FiguresAnalysisOutput]:  # type: ignore[override]
        """Get or create agent with dynamic instructions registered."""
        agent = super()._get_agent()

        if not self._instructions_registered:
            self._register_dynamic_instructions(agent)  # type: ignore[arg-type]
            self._instructions_registered = True

        return agent  # type: ignore[return-value]

    def _register_dynamic_instructions(
        self, agent: Agent[AgentDependencies, FiguresAnalysisOutput]
    ) -> None:
        """Register dynamic instruction generators on the agent."""

        @agent.instructions
        def document_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide document-level context."""
            if ctx.deps.manifest:
                return f"""
<document_context>
Title: {ctx.deps.manifest.document_title}
Type: {ctx.deps.manifest.document_type}
Total pages: {ctx.deps.manifest.total_pages}
</document_context>"""
            return ""

        @agent.instructions
        def page_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide page-specific context from manifest."""
            page_features = ctx.deps.custom_context.get("current_page_features")
            if page_features:
                page_num = page_features.get("page_num", "?")
                image_count = page_features.get("image_count", 0)
                layout_type = page_features.get("layout_type", "single_column")
                return f"""
<current_page>
Page {page_num}: {image_count} images expected
Layout: {layout_type}
</current_page>"""
            return ""

        @agent.instructions
        def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Provide guidance for retry attempts."""
            if not ctx.deps.is_retry:
                return ""

            failures = "\n".join(f"- {f}" for f in ctx.deps.previous_failures[-3:])
            return f"""
<retry_attempt number="{ctx.deps.attempt_number}">
Previous attempts encountered issues:
{failures}

Adjust your approach to avoid these issues.
</retry_attempt>"""

        logger.debug("FiguresAgent: Dynamic instructions registered")

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
        deps: AgentDependencies | None = None,
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze images on provided pages.

        Args:
            pages: Pages with images to analyze
            manifest: Document manifest with page features
            markdown: Current markdown content
            job_id: Job identifier
            deps: Optional AgentDependencies for dynamic instructions.
                  If not provided, creates default deps from manifest.

        Returns:
            Tuple of (observations for image accessibility issues, combined usage metrics)
        """
        observations: list[Observation] = []
        combined_usage = LLMUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_cents=0.0,
        )

        # Create base deps if not provided
        if deps is None:
            deps = AgentDependencies(
                job_id=job_id,
                manifest=manifest,
                document_type=manifest.document_type,
            )

        for page in pages:
            # Get page features for this page
            page_features = self._get_page_features(manifest, page.page_num)

            if not page_features or not page_features.has_images:
                continue

            # Create page-specific deps with context
            page_deps = deps.clone_for_page(
                page.page_num,
                image_count=page_features.image_count,
                layout_type=page_features.layout_type,
            )

            # Extract approximate markdown section for this page
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build user message
            user_message = self.prompts["user_prompt_template"].format(
                page_num=page.page_num,
                document_title=manifest.document_title,
                expected_image_count=page_features.image_count,
                page_markdown=page_markdown,
            )

            # Decode image for multimodal input
            image_bytes = None
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)

            try:
                # Run analysis with deps for dynamic instructions
                output, usage = await self._run_with_deps(
                    user_message, page_deps, image_bytes
                )

                # Accumulate usage
                combined_usage.input_tokens += usage.input_tokens
                combined_usage.output_tokens += usage.output_tokens
                combined_usage.total_tokens += usage.total_tokens
                combined_usage.estimated_cost_cents += usage.estimated_cost_cents

                logger.debug(
                    f"FiguresAgent page {page.page_num}: "
                    f"Found {output.images_found} images, "
                    f"{len(output.analyses)} analyses, "
                    f"cost: ${usage.estimated_cost_cents/100:.4f}"
                )

                # Log reasoning corpus for each analysis
                corpus_service = get_reasoning_corpus_service()
                for analysis in output.analyses:
                    corpus = analysis.extract_reasoning_corpus()
                    await corpus_service.log_corpus_batch(
                        job_id=job_id,
                        agent_name="figures",
                        corpus=corpus,
                    )

                # Convert analyses to observations
                page_observations = self._analyses_to_observations(
                    output.analyses,
                    page.page_num,
                    job_id,
                )
                observations.extend(page_observations)

            except Exception as e:
                logger.error(
                    f"FiguresAgent failed on page {page.page_num}: {e}",
                    exc_info=True,
                )
                # Continue with other pages

        return observations, combined_usage

    def _get_page_features(
        self,
        manifest: DocumentManifest,
        page_num: int,
    ) -> PageFeatures | None:
        """Get PageFeatures for a specific page number."""
        for pf in manifest.page_features:
            if pf.page_num == page_num:
                return pf
        return None

    def _extract_page_markdown(self, markdown: str, page_num: int) -> str:
        """Extract approximate markdown section for a page.

        This is a simple heuristic - divides markdown roughly by page.
        In practice, may use heading boundaries from manifest.

        Args:
            markdown: Full document markdown
            page_num: Page number (1-indexed)

        Returns:
            Approximate markdown for this page
        """
        lines = markdown.split("\n")
        if not lines:
            return ""

        # Rough approximation: divide by estimated pages
        # Assume about 30-50 lines per page
        chunk_size = max(len(lines) // 10, 30)
        start = (page_num - 1) * chunk_size
        end = min(page_num * chunk_size, len(lines))

        return "\n".join(lines[start:end])

    def _analyses_to_observations(
        self,
        analyses: list[ImageAnalysis],
        page_num: int,
        job_id: str,
    ) -> list[Observation]:
        """Convert ImageAnalysis objects to Observations.

        Args:
            analyses: List of image analyses from agent
            page_num: Page number
            job_id: Job identifier

        Returns:
            List of Observation objects
        """
        observations: list[Observation] = []

        for analysis in analyses:
            # Skip if no action needed (access .value for Reasoned[T] fields)
            if analysis.recommended_action.value == "none":
                continue

            # Determine severity based on image type (access .value for Reasoned[T] fields)
            image_type_value = analysis.image_type.value
            severity: str
            if image_type_value in ["informative", "complex"]:
                severity = "major"
            elif image_type_value == "text":
                severity = "major"  # Text images are important for accessibility
            else:
                severity = "minor"

            # Determine routing based on confidence
            route = "auto" if analysis.confidence >= settings.min_confidence_for_auto_approval else "manual"
            manual_reason = None
            if route == "manual":
                manual_reason = "Low confidence in image classification"

            # Build location value (image placeholder pattern)
            location_value = (
                f"![TODO: describe](image-page-{page_num}-{analysis.image_index}.png)"
            )

            obs = Observation(
                id=str(uuid4()),
                job_id=job_id,
                agent="figures",
                source="agent",
                visual_description=analysis.visual_description,
                markup_description=(
                    f"Image {analysis.image_index}: {analysis.current_alt_status}"
                ),
                location=ObservationLocation(
                    location_type="element",
                    value=location_value,
                    page_num=page_num,
                ),
                confidence=analysis.confidence,
                severity=cast(Literal["critical", "major", "minor"], severity),
                route=cast(Literal["auto", "manual"], route),
                manual_reason=manual_reason,
            )
            observations.append(obs)

        return observations

    async def process(self, input_data: Any) -> FiguresAnalysisOutput:
        """Process method required by BaseDocumentAgent.

        For FiguresAgent, use analyze() method instead which provides
        the proper interface for specialized agents.
        """
        raise NotImplementedError(
            "FiguresAgent uses analyze() method, not process()"
        )


__all__ = ["FiguresAgent"]
