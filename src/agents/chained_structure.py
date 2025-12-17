"""Chained structure analysis orchestrator.

Orchestrates the multi-step structure analysis pipeline:
1. Hierarchy Validation (Pure Python) - Validate heading hierarchy
2. Alignment Check (Haiku, CONDITIONAL) - Visual-semantic alignment
3. Reading Order Check (Haiku, CONDITIONAL) - Two-column reading order

Cost savings vs monolithic approach:
- Step 1 catches ~90% of issues with zero LLM cost
- Step 2 only runs on pages with issues or complex layouts
- Step 3 only runs on two-column pages
- Haiku for all LLM steps (3x cheaper than Sonnet)
- Estimated: 70-80% cost reduction
"""

from __future__ import annotations

import logging
import re
from typing import Literal, cast
from uuid import uuid4

from opentelemetry import trace

from src.agents.factory import aggregate_usage, get_tracer
from src.agents.structure.hierarchy_validator import (
    HierarchyIssue,
    HierarchyValidation,
    validate_hierarchy,
)
from src.agents.structure.alignment_agent import (
    AlignmentOutput,
    check_alignment,
)
from src.agents.structure.reading_order_agent import (
    ReadingOrderOutput,
    check_reading_order,
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


async def analyze_structure(
    pages: list[PageData],
    manifest: DocumentManifest,
    markdown: str,
    job_id: str,
) -> tuple[list[Observation], LLMUsage]:
    """Analyze document structure using chained agent pipeline.

    Pipeline steps:
    1. Hierarchy Validation (Pure Python) - Zero cost, catches 90% of issues
    2. Alignment Check (Haiku, conditional) - Only pages with issues
    3. Reading Order Check (Haiku, conditional) - Only two-column pages

    Args:
        pages: All document pages
        manifest: Document manifest with page features
        markdown: Current markdown content
        job_id: Job identifier

    Returns:
        Tuple of (observations for structure issues, combined usage)
    """
    tracer = get_tracer()
    usages: list[LLMUsage] = []
    all_observations: list[Observation] = []

    with tracer.start_as_current_span(
        "chained_structure.analyze_structure",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("total_pages", len(pages))

        logger.info(
            f"Job {job_id}: Starting chained structure analysis on {len(pages)} pages"
        )

        # Step 1: Hierarchy Validation (Pure Python - zero cost!)
        with tracer.start_as_current_span("step.hierarchy_validation"):
            hierarchy_result = validate_hierarchy(markdown, job_id)
            span.set_attribute("hierarchy_issues", len(hierarchy_result.issues))

        # Convert hierarchy issues to observations
        hierarchy_observations = _hierarchy_to_observations(
            hierarchy_result, job_id
        )
        all_observations.extend(hierarchy_observations)

        # Group hierarchy issues by page for conditional processing
        issues_by_page: dict[int, list[HierarchyIssue]] = {}
        for issue in hierarchy_result.issues:
            # Extract page number from location string
            page_num = _extract_page_from_location(issue.location)
            if page_num not in issues_by_page:
                issues_by_page[page_num] = []
            issues_by_page[page_num].append(issue)

        # Identify pages needing additional checks
        two_column_pages = []
        pages_with_issues = set(issues_by_page.keys())

        for page in pages:
            page_features = get_page_features(manifest, page.page_num)
            if page_features and page_features.layout_type in ["two_column", "mixed"]:
                two_column_pages.append(page.page_num)

        # Step 2: Alignment Check (Haiku, conditional)
        # Run on pages with hierarchy issues OR complex layouts
        pages_needing_alignment = pages_with_issues | set(two_column_pages)
        alignment_pages = [p for p in pages if p.page_num in pages_needing_alignment]

        if alignment_pages:
            with tracer.start_as_current_span("step.alignment_check"):
                span.set_attribute("alignment_pages", len(alignment_pages))

                for page in alignment_pages:
                    if not page.image_base64:
                        continue

                    page_markdown_section = extract_page_markdown(markdown, page.page_num)
                    page_issues = issues_by_page.get(page.page_num, [])

                    alignment_output, alignment_usage = await check_alignment(
                        page_num=page.page_num,
                        image_base64=page.image_base64,
                        page_markdown=page_markdown_section,
                        hierarchy_issues=page_issues,
                        job_id=job_id,
                    )
                    usages.append(alignment_usage)

                    # Convert alignment issues to observations
                    alignment_obs = _alignment_to_observations(alignment_output, job_id)
                    all_observations.extend(alignment_obs)
        else:
            logger.debug(f"Job {job_id}: No pages need alignment check (all simple single-column)")

        # Step 3: Reading Order Check (Haiku, conditional)
        # Only run on two-column pages
        two_column_page_data = [p for p in pages if p.page_num in two_column_pages]

        if two_column_page_data:
            with tracer.start_as_current_span("step.reading_order_check"):
                span.set_attribute("reading_order_pages", len(two_column_page_data))

                for page in two_column_page_data:
                    if not page.image_base64:
                        continue

                    page_features = get_page_features(manifest, page.page_num)
                    layout_type = page_features.layout_type if page_features else "two_column"
                    page_markdown_section = extract_page_markdown(markdown, page.page_num)

                    reading_order_output, reading_order_usage = await check_reading_order(
                        page_num=page.page_num,
                        image_base64=page.image_base64,
                        page_markdown=page_markdown_section,
                        layout_type=layout_type,
                        job_id=job_id,
                    )
                    usages.append(reading_order_usage)

                    # Convert reading order issues to observations
                    reading_order_obs = _reading_order_to_observations(
                        reading_order_output, job_id
                    )
                    all_observations.extend(reading_order_obs)
        else:
            logger.debug(f"Job {job_id}: No two-column pages, skipping reading order check")

        # Aggregate usage
        total_usage = aggregate_usage(usages)

        # Record final metrics
        span.set_attribute("observations_count", len(all_observations))
        span.set_attribute("tokens.total", total_usage.total_tokens)
        span.set_attribute("cost.cents", total_usage.estimated_cost_cents)

        logger.info(
            f"Job {job_id}: Chained structure analysis complete - "
            f"{len(all_observations)} observations, "
            f"cost: ${total_usage.estimated_cost_cents/100:.4f}"
        )

        return all_observations, total_usage


# =============================================================================
# Observation Conversion
# =============================================================================


def _extract_page_from_location(location: str) -> int:
    """Extract page number from location string like 'Page 3, line 42'."""
    match = re.search(r"Page (\d+)", location)
    return int(match.group(1)) if match else 1


def _hierarchy_to_observations(
    validation: HierarchyValidation,
    job_id: str,
) -> list[Observation]:
    """Convert hierarchy validation results to Observations."""
    observations: list[Observation] = []

    for issue in validation.issues:
        page_num = _extract_page_from_location(issue.location)

        # Map issue types to severity
        severity_map = {
            "skipped_level": "major",
            "multiple_h1": "major",
            "empty_heading": "minor",
            "long_heading": "minor",
            "orphan_heading": "major",
        }
        severity = severity_map.get(issue.issue_type, "minor")

        # Hierarchy issues are usually auto-fixable
        route = "auto" if issue.issue_type in ["skipped_level", "empty_heading"] else "manual"

        obs = Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="structure",
            source="agent",
            visual_description=f"Heading: {issue.heading_text}" if issue.heading_text else issue.location,
            markup_description=issue.description,
            location=ObservationLocation(
                location_type="element",
                value=issue.location,
                page_num=page_num,
            ),
            confidence=0.95,  # Pure Python validation is high confidence
            severity=cast(Literal["critical", "major", "minor"], severity),
            route=cast(Literal["auto", "manual"], route),
            manual_reason=None if route == "auto" else f"Issue type '{issue.issue_type}' requires review",
        )
        observations.append(obs)

    return observations


def _alignment_to_observations(
    output: AlignmentOutput,
    job_id: str,
) -> list[Observation]:
    """Convert alignment check results to Observations."""
    observations: list[Observation] = []

    for issue in output.issues:
        obs = Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="structure",
            source="agent",
            visual_description=f"Heading '{issue.heading_text}' appears as level {issue.visual_level}",
            markup_description=f"Semantic level h{issue.semantic_level}, should be h{issue.suggested_level}",
            location=ObservationLocation(
                location_type="element",
                value=f"Page {issue.page_num}: {issue.heading_text}",
                page_num=issue.page_num,
            ),
            confidence=issue.confidence,
            severity=cast(Literal["critical", "major", "minor"], issue.severity),
            route="manual",  # Alignment issues need human verification
            manual_reason="Visual-semantic mismatch requires human review",
        )
        observations.append(obs)

    return observations


def _reading_order_to_observations(
    output: ReadingOrderOutput,
    job_id: str,
) -> list[Observation]:
    """Convert reading order check results to Observations."""
    observations: list[Observation] = []

    for issue in output.issues:
        obs = Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="structure",
            source="agent",
            visual_description=issue.description,
            markup_description=f"Affected: {issue.affected_content}. Suggested: {issue.suggested_order}",
            location=ObservationLocation(
                location_type="region",
                value=f"Page {issue.page_num} reading order",
                page_num=issue.page_num,
            ),
            confidence=issue.confidence,
            severity=cast(Literal["critical", "major", "minor"], issue.severity),
            route="manual",  # Reading order issues always need human review
            manual_reason="Reading order issue in multi-column layout",
        )
        observations.append(obs)

    return observations


__all__ = [
    "analyze_structure",
]
