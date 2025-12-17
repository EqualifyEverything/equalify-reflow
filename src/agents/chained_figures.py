"""Chained figures analysis orchestrator.

Orchestrates the multi-step figures analysis pipeline:
1. Classification (Haiku) - Classify images by type
2. Generation (Haiku) - Generate alt text for non-decorative images
3. Routing (Pure Python) - Determine action and routing

Cost savings vs monolithic approach:
- Step 2 skips decorative images (no LLM cost)
- Step 3 is pure Python (no LLM cost)
- Haiku for both LLM steps (3x cheaper than Sonnet)
- Estimated: 50-70% cost reduction
"""

from __future__ import annotations

import logging
from typing import Literal, cast
from uuid import uuid4

from opentelemetry import trace

from src.agents.factory import aggregate_usage, get_tracer
from src.agents.figures.classification_agent import (
    ImageClassificationOutput,
    classify,
)
from src.agents.figures.generation_agent import (
    AltTextGenerationOutput,
    generate,
)
from src.agents.figures.routing import (
    ImageRoutingResult,
    route_page_images,
)
from src.agents.helpers import extract_page_markdown, get_page_features
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest

logger = logging.getLogger(__name__)


# =============================================================================
# Main Entry Point
# =============================================================================


async def analyze_figures(
    pages: list[PageData],
    manifest: DocumentManifest,
    markdown: str,
    job_id: str,
) -> tuple[list[Observation], LLMUsage]:
    """Analyze figures using chained agent pipeline.

    Pipeline steps:
    1. Classification (Haiku) - Classify each image
    2. Generation (Haiku) - Generate alt text for non-decorative images
    3. Routing (Pure Python) - Determine action and routing

    Args:
        pages: Pages with images to analyze
        manifest: Document manifest with page features
        markdown: Current markdown content
        job_id: Job identifier

    Returns:
        Tuple of (observations for image accessibility issues, combined usage)
    """
    tracer = get_tracer()
    usages: list[LLMUsage] = []
    all_observations: list[Observation] = []

    with tracer.start_as_current_span(
        "chained_figures.analyze_figures",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("total_pages", len(pages))

        # Filter to pages with images
        pages_with_images = [
            p for p in pages
            if get_page_features(manifest, p.page_num)
            and get_page_features(manifest, p.page_num).has_images  # type: ignore
        ]

        span.set_attribute("pages_with_images", len(pages_with_images))

        if not pages_with_images:
            logger.info(f"Job {job_id}: No pages with images, skipping figures analysis")
            return [], LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            )

        logger.info(
            f"Job {job_id}: Starting chained figures analysis on "
            f"{len(pages_with_images)} pages with images"
        )

        document_context = f"Document: {manifest.document_title} ({manifest.document_type})"

        for page in pages_with_images:
            page_features = get_page_features(manifest, page.page_num)
            if not page_features or not page.image_base64:
                continue

            page_markdown = extract_page_markdown(markdown, page.page_num)

            # Step 1: Classification (Haiku)
            with tracer.start_as_current_span(
                f"step.classification.page_{page.page_num}"
            ):
                classification_output, classification_usage = await classify(
                    page_num=page.page_num,
                    image_base64=page.image_base64,
                    expected_image_count=page_features.image_count,
                    page_markdown=page_markdown,
                    job_id=job_id,
                )
                usages.append(classification_usage)

            # Step 2: Generation (Haiku) - skips decorative images
            with tracer.start_as_current_span(
                f"step.generation.page_{page.page_num}"
            ):
                generation_output, generation_usage = await generate(
                    page_num=page.page_num,
                    image_base64=page.image_base64,
                    classifications=classification_output.classifications,
                    page_markdown=page_markdown,
                    document_context=document_context,
                    job_id=job_id,
                )
                usages.append(generation_usage)

            # Step 3: Routing (Pure Python - zero cost)
            with tracer.start_as_current_span(
                f"step.routing.page_{page.page_num}"
            ):
                routing_results = route_page_images(
                    classifications=classification_output.classifications,
                    generations=generation_output.generations,
                )

            # Convert routing results to observations
            page_observations = _routing_to_observations(
                routing_results=routing_results,
                page_num=page.page_num,
                job_id=job_id,
            )
            all_observations.extend(page_observations)

            logger.debug(
                f"Job {job_id}: Page {page.page_num} - "
                f"{len(classification_output.classifications)} images classified, "
                f"{len(generation_output.generations)} alt texts generated, "
                f"{len(page_observations)} observations created"
            )

        # Aggregate usage
        total_usage = aggregate_usage(usages)

        # Record final metrics
        span.set_attribute("observations_count", len(all_observations))
        span.set_attribute("tokens.total", total_usage.total_tokens)
        span.set_attribute("cost.cents", total_usage.estimated_cost_cents)

        logger.info(
            f"Job {job_id}: Chained figures analysis complete - "
            f"{len(all_observations)} observations, "
            f"cost: ${total_usage.estimated_cost_cents/100:.4f}"
        )

        return all_observations, total_usage


# =============================================================================
# Observation Conversion
# =============================================================================


def _routing_to_observations(
    routing_results: list[ImageRoutingResult],
    page_num: int,
    job_id: str,
) -> list[Observation]:
    """Convert routing results to Observations.

    Args:
        routing_results: Routing results from Step 3
        page_num: Page number
        job_id: Job identifier

    Returns:
        List of Observation objects
    """
    observations: list[Observation] = []

    for result in routing_results:
        # Skip if no action needed (shouldn't happen, but be safe)
        if result.action == "none":
            continue

        # Build location value (image placeholder pattern)
        location_value = (
            f"![TODO: describe](image-page-{page_num}-{result.image_index}.png)"
        )

        # Build markup description including action and alt text
        markup_desc = f"Image {result.image_index}: {result.action}"
        if result.alt_text:
            alt_preview = result.alt_text[:100]
            if len(result.alt_text) > 100:
                alt_preview += "..."
            markup_desc += f" - suggested: \"{alt_preview}\""

        obs = Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="figures",
            source="agent",
            visual_description=result.visual_description,
            markup_description=markup_desc,
            location=ObservationLocation(
                location_type="element",
                value=location_value,
                page_num=page_num,
            ),
            confidence=result.confidence,
            severity=cast(Literal["critical", "major", "minor"], result.severity),
            route=cast(Literal["auto", "manual"], result.route),
            manual_reason=result.manual_reason,
        )
        observations.append(obs)

    return observations


__all__ = [
    "analyze_figures",
]
