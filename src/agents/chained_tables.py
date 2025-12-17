"""Chained tables analysis orchestrator.

Orchestrates the multi-step tables analysis pipeline:
1. Structure Detection (Haiku, multimodal) - Detect table structure
2. Accuracy Assessment (Haiku, TEXT-ONLY) - Assess markdown accuracy
3. Routing (Pure Python) - Determine action and routing

Cost savings vs monolithic approach:
- Step 2 is text-only (no image tokens!)
- Step 2 skips simple tables with headers
- Step 3 is pure Python (no LLM cost)
- Haiku for both LLM steps (3x cheaper than Sonnet)
- Estimated: 60-75% cost reduction
"""

from __future__ import annotations

import logging
from typing import Literal, cast
from uuid import uuid4

from opentelemetry import trace

from src.agents.factory import aggregate_usage, get_tracer
from src.agents.helpers import extract_page_markdown, get_page_features
from src.agents.tables.accuracy_agent import (
    assess_accuracy,
)
from src.agents.tables.routing import (
    TableRoutingResult,
    route_page_tables,
)
from src.agents.tables.structure_agent import (
    detect_structure,
)
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest

logger = logging.getLogger(__name__)


# =============================================================================
# Main Entry Point
# =============================================================================


async def analyze_tables(
    pages: list[PageData],
    manifest: DocumentManifest,
    markdown: str,
    job_id: str,
) -> tuple[list[Observation], LLMUsage]:
    """Analyze tables using chained agent pipeline.

    Pipeline steps:
    1. Structure Detection (Haiku, multimodal) - Detect structure
    2. Accuracy Assessment (Haiku, text-only) - Assess markdown
    3. Routing (Pure Python) - Determine action and routing

    Args:
        pages: Pages with tables to analyze
        manifest: Document manifest with page features
        markdown: Current markdown content
        job_id: Job identifier

    Returns:
        Tuple of (observations for table issues, combined usage)
    """
    tracer = get_tracer()
    usages: list[LLMUsage] = []
    all_observations: list[Observation] = []

    with tracer.start_as_current_span(
        "chained_tables.analyze_tables",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("total_pages", len(pages))

        # Filter to pages with tables
        pages_with_tables = [
            p for p in pages
            if get_page_features(manifest, p.page_num)
            and get_page_features(manifest, p.page_num).has_tables  # type: ignore
        ]

        span.set_attribute("pages_with_tables", len(pages_with_tables))

        if not pages_with_tables:
            logger.info(f"Job {job_id}: No pages with tables, skipping tables analysis")
            return [], LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            )

        logger.info(
            f"Job {job_id}: Starting chained tables analysis on "
            f"{len(pages_with_tables)} pages with tables"
        )

        for page in pages_with_tables:
            page_features = get_page_features(manifest, page.page_num)
            if not page_features or not page.image_base64:
                continue

            page_markdown = extract_page_markdown(markdown, page.page_num)

            # Step 1: Structure Detection (Haiku, multimodal)
            with tracer.start_as_current_span(
                f"step.structure.page_{page.page_num}"
            ):
                structure_output, structure_usage = await detect_structure(
                    page_num=page.page_num,
                    image_base64=page.image_base64,
                    expected_table_count=page_features.table_count,
                    page_markdown=page_markdown,
                    job_id=job_id,
                )
                usages.append(structure_usage)

            # Step 2: Accuracy Assessment (Haiku, TEXT-ONLY)
            # Skips simple tables with headers
            with tracer.start_as_current_span(
                f"step.accuracy.page_{page.page_num}"
            ):
                accuracy_output, accuracy_usage = await assess_accuracy(
                    page_num=page.page_num,
                    structures=structure_output.structures,
                    page_markdown=page_markdown,
                    job_id=job_id,
                )
                usages.append(accuracy_usage)

            # Step 3: Routing (Pure Python - zero cost)
            with tracer.start_as_current_span(
                f"step.routing.page_{page.page_num}"
            ):
                routing_results = route_page_tables(
                    structures=structure_output.structures,
                    accuracies=accuracy_output.assessments,
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
                f"{len(structure_output.structures)} tables detected, "
                f"{len(accuracy_output.assessments)} assessed, "
                f"{len(page_observations)} observations created"
            )

        # Aggregate usage
        total_usage = aggregate_usage(usages)

        # Record final metrics
        span.set_attribute("observations_count", len(all_observations))
        span.set_attribute("tokens.total", total_usage.total_tokens)
        span.set_attribute("cost.cents", total_usage.estimated_cost_cents)

        logger.info(
            f"Job {job_id}: Chained tables analysis complete - "
            f"{len(all_observations)} observations, "
            f"cost: ${total_usage.estimated_cost_cents/100:.4f}"
        )

        return all_observations, total_usage


# =============================================================================
# Observation Conversion
# =============================================================================


def _routing_to_observations(
    routing_results: list[TableRoutingResult],
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
        # Skip if no action needed
        if result.action == "none":
            continue

        # Build location value
        location_value = f"Table {result.table_index} on page {page_num}"

        # Build markup description including action and issues
        markup_desc = (
            f"Table {result.table_index}: {result.action}, "
            f"complexity={result.complexity}"
        )
        if result.issues:
            issues_str = "; ".join(result.issues[:3])  # Limit to first 3
            markup_desc += f" | Issues: {issues_str}"

        obs = Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="tables",
            source="agent",
            visual_description=result.visual_description,
            markup_description=markup_desc,
            location=ObservationLocation(
                location_type="region",
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
    "analyze_tables",
]
