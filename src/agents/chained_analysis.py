"""Chained analysis orchestrator for improved layout detection.

This module orchestrates a multi-step analysis pipeline that breaks down
the monolithic analysis prompt into focused, sequential steps:

1. Layout detection (visual, must be first)
2. Document type classification (uses layout context)
3. Headings + Features in parallel (headings uses doc_type, features independent)
4. Manifest assembly (pure Python, no LLM)

This approach improves layout detection accuracy by giving the model
a focused task with a short prompt, rather than a 267-line monolithic prompt
that causes the model to "barely glance" at images.

Execution timeline:
    Layout (5s) → DocType (3s) → [Headings | Features] parallel (5s) → Assemble
    Total: ~13s for typical document (vs ~24s fully sequential)
"""

import asyncio
import logging
from typing import Literal

from opentelemetry import trace

from src.agents import doctype_agent, features_agent, headings_agent, layout_agent
from src.agents.doctype_agent import DocumentTypeOutput
from src.agents.factory import aggregate_usage, get_tracer
from src.agents.features_agent import PageFeatureDetection
from src.agents.headings_agent import HeadingsOutput
from src.agents.layout_agent import PageLayout
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import (
    DocumentManifest,
    HeadingNode,
    HeadingTree,
    PageFeatures,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Main Entry Point
# =============================================================================


async def analyze_document(
    pages: list[PageData],
    job_id: str,
    parallel: bool = True,
) -> tuple[DocumentManifest, list[str], LLMUsage]:
    """Analyze document using chained agent pipeline.

    This function orchestrates the chained analysis flow:
    1. Layout detection (must be first - controls reading order)
    2. Document type classification (uses layout context)
    3. Headings + Features in parallel (or sequential if parallel=False)
    4. Manifest assembly from collected results

    Args:
        pages: List of PageData with page images
        job_id: Job ID for correlation and tracing
        parallel: Whether to run headings and features in parallel

    Returns:
        Tuple of (DocumentManifest, observations list, aggregated LLMUsage)
    """
    tracer = get_tracer()
    usages: list[LLMUsage] = []

    with tracer.start_as_current_span(
        "chained_analysis.analyze_document",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("total_pages", len(pages))
        span.set_attribute("parallel_mode", parallel)

        logger.info(
            f"Job {job_id}: Starting chained analysis on {len(pages)} pages "
            f"(parallel={parallel})"
        )

        # Step 1: Layout detection (MUST be first - controls reading order)
        with tracer.start_as_current_span("step.layout_detection") as layout_span:
            layouts, layout_usage = await layout_agent.detect(pages, job_id)
            usages.append(layout_usage)
            layout_span.set_attribute("two_column_count",
                sum(1 for layout in layouts if layout.layout_type == "two_column"))

        # Step 2: Document type classification (uses layout context)
        with tracer.start_as_current_span("step.doctype_classification"):
            doc_type, doctype_usage = await doctype_agent.classify(
                pages, layouts, job_id
            )
            usages.append(doctype_usage)

        # Step 3: Headings + Features (can run in parallel)
        if parallel:
            with tracer.start_as_current_span("step.headings_features_parallel"):
                headings_task = headings_agent.extract(
                    pages, layouts, doc_type, job_id
                )
                features_task = features_agent.detect(pages, job_id)

                (headings_output, headings_usage), (features_list, features_usage) = (
                    await asyncio.gather(headings_task, features_task)
                )
                usages.extend([headings_usage, features_usage])
        else:
            # Sequential mode (for debugging or if parallel causes issues)
            with tracer.start_as_current_span("step.headings_extraction"):
                headings_output, headings_usage = await headings_agent.extract(
                    pages, layouts, doc_type, job_id
                )
                usages.append(headings_usage)

            with tracer.start_as_current_span("step.features_detection"):
                features_list, features_usage = await features_agent.detect(
                    pages, job_id
                )
                usages.append(features_usage)

        # Step 4: Assemble manifest (pure Python, no LLM)
        with tracer.start_as_current_span("step.manifest_assembly"):
            manifest = assemble_manifest(
                job_id=job_id,
                total_pages=len(pages),
                layouts=layouts,
                doc_type=doc_type,
                headings=headings_output,
                features=features_list,
            )

        # Aggregate usage
        total_usage = aggregate_usage(usages)

        # Record final metrics
        span.set_attribute("manifest.document_type", manifest.document_type)
        span.set_attribute("manifest.required_agents", ",".join(manifest.required_agents))
        span.set_attribute("tokens.total", total_usage.total_tokens)
        span.set_attribute("cost.cents", total_usage.estimated_cost_cents)

        logger.info(
            f"Job {job_id}: Chained analysis complete - "
            f"type={manifest.document_type}, "
            f"headings={len(headings_output.headings)}, "
            f"agents={manifest.required_agents}, "
            f"cost: ${total_usage.estimated_cost_cents/100:.4f}"
        )

        # Return empty observations list (specialized agents will generate observations)
        return manifest, [], total_usage


# =============================================================================
# Manifest Assembly (Pure Python)
# =============================================================================


def assemble_manifest(
    job_id: str,
    total_pages: int,
    layouts: list[PageLayout],
    doc_type: DocumentTypeOutput,
    headings: HeadingsOutput,
    features: list[PageFeatureDetection],
) -> DocumentManifest:
    """Assemble DocumentManifest from chained analysis results.

    This function converts the outputs from individual agents into the
    DocumentManifest format expected by the extraction pipeline.

    Args:
        job_id: Job ID
        total_pages: Total number of pages
        layouts: Layout detection results
        doc_type: Document type classification
        headings: Heading structure extraction results
        features: Page feature detection results

    Returns:
        Assembled DocumentManifest
    """
    # Build HeadingTree from headings output
    heading_tree = build_heading_tree(
        headings=headings,
        layouts=layouts,
        total_pages=total_pages,
    )

    # Convert features to PageFeatures format
    page_features = convert_features(features, layouts)

    # Determine required agents based on features
    required_agents = determine_required_agents(
        features=features,
        has_two_column=any(layout.layout_type == "two_column" for layout in layouts),
    )

    # Determine which agents can be skipped
    skip_agents = determine_skip_agents(features)

    # Calculate overall confidence (average of layout confidences)
    avg_layout_confidence = (
        sum(layout.confidence for layout in layouts) / len(layouts) if layouts else 0.8
    )

    return DocumentManifest(
        job_id=job_id,
        document_title=heading_tree.document_title,
        document_type=doc_type.document_type,
        total_pages=total_pages,
        heading_tree_json=heading_tree.model_dump_json(),
        page_features=page_features,
        required_agents=required_agents,
        skip_agents=skip_agents,
        analysis_confidence=min(avg_layout_confidence, doc_type.confidence),
        analysis_notes=build_analysis_notes(layouts, doc_type, headings),
        document_type_reasoning=doc_type.evidence,
        heading_order_verification=headings.reading_order_notes,
        agent_routing_reasoning=build_routing_reasoning(features, required_agents),
        analysis_model=MODEL_TIER_MAP[ModelTier.EFFICIENT],
    )


def build_heading_tree(
    headings: HeadingsOutput,
    layouts: list[PageLayout],
    total_pages: int,
) -> HeadingTree:
    """Build HeadingTree from headings agent output.

    Args:
        headings: HeadingsOutput from headings agent
        layouts: Layout detection results for layout_type
        total_pages: Total number of pages

    Returns:
        HeadingTree for DocumentManifest
    """
    # Find document title (first H1 or first heading)
    document_title = "Untitled"
    title_page = 1
    for h in headings.headings:
        if h.level == 1:
            document_title = h.text
            title_page = h.page_num
            break
    # Fallback: use first heading if no H1
    if document_title == "Untitled" and headings.headings:
        document_title = headings.headings[0].text
        title_page = headings.headings[0].page_num

    # Convert to HeadingNode format
    sections = [
        HeadingNode(
            level=h.level,
            title=h.text,
            page=h.page_num,
            section_number=h.section_number,
        )
        for h in headings.headings
    ]

    # Determine overall layout type
    layout_counts: dict[str, int] = {}
    for layout in layouts:
        layout_counts[layout.layout_type] = layout_counts.get(layout.layout_type, 0) + 1

    if layout_counts.get("two_column", 0) > 0 and layout_counts.get("single_column", 0) > 0:
        overall_layout: Literal["single_column", "two_column", "mixed"] = "mixed"
    elif layout_counts.get("two_column", 0) > 0:
        overall_layout = "two_column"
    else:
        overall_layout = "single_column"

    return HeadingTree(
        document_title=document_title,
        title_page=title_page,
        sections=sections,
        total_pages=total_pages,
        layout_type=overall_layout,
        confidence=0.9,  # High confidence from focused agents
        observations=headings.reading_order_notes,
    )


def convert_features(
    features: list[PageFeatureDetection],
    layouts: list[PageLayout],
) -> list[PageFeatures]:
    """Convert PageFeatureDetection to PageFeatures format.

    Args:
        features: Feature detection results
        layouts: Layout detection results

    Returns:
        List of PageFeatures for DocumentManifest
    """
    # Build layout lookup
    layout_by_page = {layout.page_num: layout for layout in layouts}

    result = []
    for f in features:
        layout = layout_by_page.get(f.page_num)
        layout_type: Literal["single_column", "two_column", "mixed"] = (
            layout.layout_type if layout else "single_column"
        )
        layout_reasoning = layout.evidence if layout else None

        # Calculate complexity score based on features
        complexity_factors = []
        complexity_score = 0.3  # Base score

        if f.has_tables:
            complexity_score += 0.2
            complexity_factors.append(f"{f.table_count} tables")
        if f.has_images:
            complexity_score += 0.15
            complexity_factors.append(f"{f.image_count} images")
        if f.has_code_blocks:
            complexity_score += 0.1
            complexity_factors.append("code blocks")
        if f.has_math:
            complexity_score += 0.15
            complexity_factors.append("math equations")
        if layout_type == "two_column":
            complexity_score += 0.1
            complexity_factors.append("two-column layout")

        complexity_score = min(complexity_score, 1.0)

        result.append(
            PageFeatures(
                page_num=f.page_num,
                has_images=f.has_images,
                image_count=f.image_count,
                has_tables=f.has_tables,
                table_count=f.table_count,
                has_lists=f.has_lists,
                has_code_blocks=f.has_code_blocks,
                has_math=f.has_math,
                layout_type=layout_type,
                has_headers_footers=False,  # Would need separate detection
                complexity_score=round(complexity_score, 2),
                complexity_factors=complexity_factors if complexity_score > 0.3 else [],
                layout_type_reasoning=layout_reasoning,
            )
        )

    return result


# =============================================================================
# Agent Routing Logic
# =============================================================================


def determine_required_agents(
    features: list[PageFeatureDetection],
    has_two_column: bool,
) -> list[str]:
    """Determine which specialized agents should run.

    Agent routing logic:
    - figures: Run if any page has informative images
    - tables: Run if any page has data tables
    - structure: Always run (validates heading hierarchy)
    - typography: Run if document has complex formatting

    Args:
        features: Page feature detection results
        has_two_column: Whether any page has two-column layout

    Returns:
        List of required agent names
    """
    required = ["structure"]  # Always run structure validation

    # Check for images
    if any(f.has_images for f in features):
        required.append("figures")

    # Check for tables
    if any(f.has_tables for f in features):
        required.append("tables")

    # Typography for complex documents
    if has_two_column or any(f.has_code_blocks or f.has_math for f in features):
        required.append("typography")

    return required


def determine_skip_agents(features: list[PageFeatureDetection]) -> list[str]:
    """Determine which agents can be safely skipped.

    Args:
        features: Page feature detection results

    Returns:
        List of agents that can be skipped
    """
    skip = []

    # Skip figures if no images
    if not any(f.has_images for f in features):
        skip.append("figures")

    # Skip tables if no tables
    if not any(f.has_tables for f in features):
        skip.append("tables")

    return skip


def build_analysis_notes(
    layouts: list[PageLayout],
    doc_type: DocumentTypeOutput,
    headings: HeadingsOutput,
) -> str:
    """Build analysis notes from chained results.

    Args:
        layouts: Layout detection results
        doc_type: Document type classification
        headings: Heading extraction results

    Returns:
        Summary notes string
    """
    notes = []

    # Layout summary
    two_col_pages = [layout.page_num for layout in layouts if layout.layout_type == "two_column"]
    if two_col_pages:
        notes.append(f"Two-column layout on pages: {two_col_pages}")
    else:
        notes.append("Single-column layout throughout")

    # Document type
    notes.append(f"Document type: {doc_type.document_type} ({doc_type.evidence})")

    # Heading summary
    if headings.headings:
        max_level = max(h.level for h in headings.headings)
        notes.append(f"{len(headings.headings)} headings detected (max depth H{max_level})")

    # Reading order notes
    if headings.reading_order_notes:
        notes.append(f"Reading order: {headings.reading_order_notes}")

    return " | ".join(notes)


def build_routing_reasoning(
    features: list[PageFeatureDetection],
    required_agents: list[str],
) -> str:
    """Build reasoning for agent routing decisions.

    Args:
        features: Page feature detection results
        required_agents: List of required agents

    Returns:
        Reasoning string
    """
    reasons = []

    total_images = sum(f.image_count for f in features)
    total_tables = sum(f.table_count for f in features)
    pages_with_code = sum(1 for f in features if f.has_code_blocks)
    pages_with_math = sum(1 for f in features if f.has_math)

    if "figures" in required_agents:
        reasons.append(f"figures agent: {total_images} informative images detected")
    else:
        reasons.append("figures agent: skipped (no informative images)")

    if "tables" in required_agents:
        reasons.append(f"tables agent: {total_tables} data tables detected")
    else:
        reasons.append("tables agent: skipped (no data tables)")

    reasons.append("structure agent: always runs for heading validation")

    if "typography" in required_agents:
        typography_reasons = []
        if pages_with_code:
            typography_reasons.append(f"{pages_with_code} pages with code")
        if pages_with_math:
            typography_reasons.append(f"{pages_with_math} pages with math")
        reasons.append(f"typography agent: {', '.join(typography_reasons)}")
    else:
        reasons.append("typography agent: skipped (no complex formatting)")

    return " | ".join(reasons)


__all__ = [
    "analyze_document",
    "assemble_manifest",
    "determine_required_agents",
]
