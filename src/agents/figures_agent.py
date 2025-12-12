"""FiguresAgent for image accessibility analysis (PRD-014, Issue #24).

Specialized agent for analyzing images in PDF documents:
- Classifies images (decorative, informative, complex, text)
- Evaluates current alt text quality
- Generates appropriate alt text suggestions
- Identifies images needing long descriptions
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import FiguresAnalysisOutput, ImageAnalysis
from src.services.pdf_converter import PageData
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
        )
        super().__init__(config)

    def _default_prompts(self) -> dict[str, Any]:
        """Provide fallback prompts if YAML file not found."""
        return {
            "system_prompt": """You are an image accessibility expert analyzing PDF documents.
Classify images and evaluate their alt text for accessibility compliance.

IMAGE TYPES:
- decorative: Visual flourish, background, spacer - needs empty alt=""
- informative: Conveys information - needs descriptive alt text
- complex: Charts, diagrams, infographics - needs alt + long description
- text: Image of text - text should be transcribed

CURRENT ALT STATUS:
- "TODO placeholder": Has placeholder like "TODO: describe"
- "empty": No alt text
- "has description": Has existing description (evaluate quality)

ACTIONS:
- add_alt: Add descriptive alt text
- improve_alt: Existing alt insufficient
- mark_decorative: Should have empty alt=""
- add_long_desc: Needs extended description
- none: Current alt is appropriate""",
            "user_prompt_template": """Analyze images on page {page_num}.
Document: {document_title}
Expected images: {expected_image_count}

Current markdown:
```
{page_markdown}
```

Analyze each image for accessibility.""",
        }

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze images on provided pages.

        Args:
            pages: Pages with images to analyze
            manifest: Document manifest with page features
            markdown: Current markdown content
            job_id: Job identifier

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

        for page in pages:
            # Get page features for this page
            page_features = self._get_page_features(manifest, page.page_num)

            if not page_features or not page_features.has_images:
                continue

            # Extract approximate markdown section for this page
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build user message
            user_message = self.prompts.get(
                "user_prompt_template",
                self._default_prompts()["user_prompt_template"]
            ).format(
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
                # Run analysis
                output, usage = await self._run_agent(user_message, image_bytes)

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
            # Skip if no action needed
            if analysis.recommended_action == "none":
                continue

            # Determine severity based on image type
            severity: str
            if analysis.image_type in ["informative", "complex"]:
                severity = "major"
            elif analysis.image_type == "text":
                severity = "major"  # Text images are important for accessibility
            else:
                severity = "minor"

            # Determine routing based on confidence
            route = "auto" if analysis.confidence >= 0.7 else "manual"
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
