"""Vision-based markdown extraction service for scanned PDFs.

Uses Claude's vision capabilities to transcribe page images to markdown
when Docling cannot extract text (scanned/image-based PDFs).

Supports different document types:
- Standard documents (articles, books, syllabi)
- Visual documents (posters, flyers, infographics)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier

logger = logging.getLogger(__name__)

# Threshold for detecting scanned PDFs (chars per page)
SCANNED_THRESHOLD_CHARS_PER_PAGE = 50


class VisualDocumentType(str, Enum):
    """Types of visual documents requiring special handling."""

    STANDARD = "standard"  # Regular document (article, book, syllabus)
    POSTER = "poster"  # Event poster, flyer
    INFOGRAPHIC = "infographic"  # Data visualization, infographic
    FORM = "form"  # Fillable form
    CERTIFICATE = "certificate"  # Certificate, award
    BROCHURE = "brochure"  # Multi-panel brochure


@dataclass
class VisionExtractionResult:
    """Result of vision-based page extraction."""

    page_num: int
    markdown: str
    success: bool
    error: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0
    detected_type: VisualDocumentType = VisualDocumentType.STANDARD


@dataclass
class VisionExtractionStats:
    """Statistics from vision extraction."""

    pages_extracted: int
    total_tokens_input: int
    total_tokens_output: int
    total_duration_ms: int
    estimated_cost: float
    detected_types: dict[int, VisualDocumentType] | None = None


# =============================================================================
# System Prompts for Different Document Types
# =============================================================================

STANDARD_DOCUMENT_PROMPT = """You transcribe PDF page images to accessible markdown.

Rules:
- Transcribe text EXACTLY as written (verbatim)
- Use proper heading hierarchy (# for title, ## for sections, ### for subsections)
- For images/figures: use placeholder ![TODO: describe](figure-page-N-M.png)
- For tables: use markdown table syntax with | delimiters
- Mark unclear or illegible text with [?]
- Preserve paragraph structure with blank lines
- Output ONLY the markdown, no explanations or commentary"""

POSTER_DOCUMENT_PROMPT = """You extract content from event posters, flyers, and promotional materials into accessible markdown.

This is a VISUAL DOCUMENT where the layout itself conveys meaning. Your job is to:
1. Extract ALL visible text (titles, dates, descriptions, contact info)
2. Describe the main artwork/imagery for accessibility
3. Organize into semantic sections

Output format:
# [Main Title/Event Name]

## Event Details
- **Date:** [extracted date]
- **Time:** [extracted time]
- **Location:** [extracted location]
- **Cost:** [free/price if shown]

## Description
[Event description text from the poster]

## Artwork Description
[Describe the main visual/illustration for accessibility: what it depicts, style, colors, symbolism]

## Organizer
[Organization name, logos described]

## Contact Information
[Phone, email, website, social media handles]

---

Rules:
- Extract ALL text visible on the poster
- For the main artwork, write a concise but complete description (2-4 sentences)
- Preserve hashtags exactly as shown (e.g., #wmnhist)
- Include accessibility info if mentioned (ASL, captioning, etc.)
- If text is stylized/decorative, still transcribe it
- Output ONLY the markdown, no meta-commentary"""

INFOGRAPHIC_DOCUMENT_PROMPT = """You extract content from infographics and data visualizations into accessible markdown.

This is a VISUAL DOCUMENT where graphics convey information. Your job is to:
1. Extract the main title and any text
2. Describe each data visualization (charts, graphs, diagrams)
3. Extract key statistics and data points
4. Preserve the logical flow of information

Output format:
# [Infographic Title]

## Overview
[Brief description of what this infographic is about]

## Key Statistics
- [Stat 1]: [Value]
- [Stat 2]: [Value]

## [Section Name]
[Text content]

### Chart: [Chart Title]
[Description of what the chart shows, including key data points and trends]

## Data Visualization Descriptions
[For accessibility: describe each chart/graph - type, axes, key values, trends]

## Source
[Data source if shown]

---

Rules:
- Describe charts/graphs in enough detail that someone could understand the data
- Extract all numerical values and statistics
- Note colors only when they convey meaning (e.g., "red indicates danger")
- Output ONLY the markdown"""


def get_prompt_for_document_type(doc_type: VisualDocumentType) -> str:
    """Get the appropriate system prompt for a document type."""
    prompts = {
        VisualDocumentType.STANDARD: STANDARD_DOCUMENT_PROMPT,
        VisualDocumentType.POSTER: POSTER_DOCUMENT_PROMPT,
        VisualDocumentType.INFOGRAPHIC: INFOGRAPHIC_DOCUMENT_PROMPT,
        # Fall back to poster prompt for similar visual types
        VisualDocumentType.BROCHURE: POSTER_DOCUMENT_PROMPT,
        VisualDocumentType.CERTIFICATE: POSTER_DOCUMENT_PROMPT,
        VisualDocumentType.FORM: STANDARD_DOCUMENT_PROMPT,
    }
    return prompts.get(doc_type, STANDARD_DOCUMENT_PROMPT)


def is_scanned_pdf(docling_markdown: str, total_pages: int) -> bool:
    """Detect if PDF is scanned based on ACTUAL text density (excluding markup).

    Strips HTML comments (<!-- image --> placeholders) and markdown image
    references before calculating characters per page. This prevents scanned
    documents with only image placeholders from being incorrectly classified
    as having extractable text.

    Args:
        docling_markdown: Full markdown extracted by Docling
        total_pages: Total number of pages

    Returns:
        True if PDF appears to be scanned (minimal extractable text)
    """
    if total_pages == 0:
        return True

    # Strip HTML comments (<!-- image --> placeholders from Docling)
    text_only = re.sub(r"<!--.*?-->", "", docling_markdown)
    # Strip markdown image references ![...](...)
    text_only = re.sub(r"!\[.*?\]\(.*?\)", "", text_only)
    # Strip remaining whitespace
    text_only = text_only.strip()

    chars_per_page = len(text_only) / total_pages
    is_scanned = chars_per_page < SCANNED_THRESHOLD_CHARS_PER_PAGE

    logger.info(
        f"Scanned PDF detection: {len(text_only)} text chars / {total_pages} pages "
        f"= {chars_per_page:.1f} chars/page (threshold={SCANNED_THRESHOLD_CHARS_PER_PAGE}) "
        f"-> is_scanned={is_scanned}"
    )

    return is_scanned


async def detect_visual_document_type(
    image_base64: str,
    filename: str = "",
    total_pages: int = 1,
) -> VisualDocumentType:
    """Detect the type of visual document from its first page image.

    Uses a quick LLM call to classify the document type.

    Args:
        image_base64: Base64-encoded PNG of first page
        filename: Original filename (for hints)
        total_pages: Total pages in document

    Returns:
        Detected VisualDocumentType
    """
    # Quick heuristics first
    filename_lower = filename.lower()

    # Single page documents are more likely to be visual
    if total_pages == 1:
        # Filename hints
        if any(word in filename_lower for word in ["poster", "flyer", "flier", "announcement"]):
            logger.info(f"Document type detected from filename: POSTER ({filename})")
            return VisualDocumentType.POSTER
        if any(word in filename_lower for word in ["infographic", "chart", "data", "stats"]):
            logger.info(f"Document type detected from filename: INFOGRAPHIC ({filename})")
            return VisualDocumentType.INFOGRAPHIC
        if any(word in filename_lower for word in ["certificate", "award", "diploma"]):
            logger.info(f"Document type detected from filename: CERTIFICATE ({filename})")
            return VisualDocumentType.CERTIFICATE
        if any(word in filename_lower for word in ["form", "application", "survey"]):
            logger.info(f"Document type detected from filename: FORM ({filename})")
            return VisualDocumentType.FORM
        if any(word in filename_lower for word in ["brochure", "pamphlet", "leaflet"]):
            logger.info(f"Document type detected from filename: BROCHURE ({filename})")
            return VisualDocumentType.BROCHURE

    # For single-page documents without clear filename hints, use LLM classification
    if total_pages == 1:
        try:
            model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

            agent: Agent[None, str] = Agent(
                model=model,
                output_type=str,
                system_prompt="""Classify this document image into ONE of these categories:
- POSTER: Event poster, flyer, promotional material with bold graphics
- INFOGRAPHIC: Data visualization, charts, statistics-focused
- CERTIFICATE: Award, diploma, certificate
- FORM: Fillable form, application
- BROCHURE: Multi-panel brochure, pamphlet
- STANDARD: Regular document, article, report, book page

Respond with ONLY the category name (e.g., "POSTER" or "STANDARD").""",
            )

            image_bytes = base64.b64decode(image_base64)

            result = await agent.run(
                [
                    "What type of document is this? Classify it.",
                    BinaryContent(data=image_bytes, media_type="image/png"),
                ]
            )

            response = result.output.strip().upper()

            # Map response to enum
            type_map = {
                "POSTER": VisualDocumentType.POSTER,
                "INFOGRAPHIC": VisualDocumentType.INFOGRAPHIC,
                "CERTIFICATE": VisualDocumentType.CERTIFICATE,
                "FORM": VisualDocumentType.FORM,
                "BROCHURE": VisualDocumentType.BROCHURE,
                "STANDARD": VisualDocumentType.STANDARD,
            }

            detected = type_map.get(response, VisualDocumentType.STANDARD)
            logger.info(f"Document type detected by LLM: {detected.value} (response: {response})")
            return detected

        except Exception as e:
            logger.warning(f"Document type detection failed, defaulting to STANDARD: {e}")
            return VisualDocumentType.STANDARD

    # Multi-page documents default to standard
    logger.info(f"Document type defaulting to STANDARD ({total_pages} pages)")
    return VisualDocumentType.STANDARD


async def extract_page_markdown_vision(
    page_num: int,
    image_base64: str,
    document_type: str = "document",
    total_pages: int = 1,
    expected_figures: int = 0,
    expected_tables: int = 0,
    visual_doc_type: VisualDocumentType = VisualDocumentType.STANDARD,
) -> VisionExtractionResult:
    """Extract markdown from a single page image using LLM vision.

    Uses Claude Haiku's vision capabilities for cost-effective extraction.
    Adapts extraction strategy based on visual document type.

    Args:
        page_num: Page number (1-indexed)
        image_base64: Base64-encoded PNG page image
        document_type: Type of document for context (e.g., "course material")
        total_pages: Total pages in document
        expected_figures: Expected figure count (hint)
        expected_tables: Expected table count (hint)
        visual_doc_type: Detected visual document type (POSTER, INFOGRAPHIC, etc.)

    Returns:
        VisionExtractionResult with markdown and metadata
    """
    start_time = time.time()

    model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

    # Get appropriate system prompt for document type
    system_prompt = get_prompt_for_document_type(visual_doc_type)

    agent: Agent[None, str] = Agent(
        model=model,
        output_type=str,
        system_prompt=system_prompt,
    )

    try:
        image_bytes = base64.b64decode(image_base64)

        # Customize user prompt based on document type
        if visual_doc_type == VisualDocumentType.POSTER:
            user_prompt = (
                "Extract all content from this event poster/flyer. "
                "Include all text, describe the artwork, and organize into semantic sections."
            )
        elif visual_doc_type == VisualDocumentType.INFOGRAPHIC:
            user_prompt = (
                "Extract all content from this infographic. "
                "Describe all charts/visualizations and extract all data points and statistics."
            )
        else:
            user_prompt = (
                f"Transcribe page {page_num} of {total_pages} from this {document_type}. "
                f"Expected elements: approximately {expected_figures} figures, {expected_tables} tables."
            )

        result = await agent.run(
            [
                user_prompt,
                BinaryContent(data=image_bytes, media_type="image/png"),
            ]
        )

        markdown = result.output
        duration_ms = int((time.time() - start_time) * 1000)

        # Extract token usage if available
        tokens_input = 0
        tokens_output = 0
        if hasattr(result, "usage"):
            tokens_input = getattr(result.usage, "input_tokens", 0) or 0
            tokens_output = getattr(result.usage, "output_tokens", 0) or 0

        logger.debug(
            f"Vision extraction page {page_num} ({visual_doc_type.value}): {len(markdown)} chars, "
            f"{tokens_input}+{tokens_output} tokens, {duration_ms}ms"
        )

        return VisionExtractionResult(
            page_num=page_num,
            markdown=markdown,
            success=True,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            duration_ms=duration_ms,
            detected_type=visual_doc_type,
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Vision extraction failed for page {page_num}: {e}")

        return VisionExtractionResult(
            page_num=page_num,
            markdown=f"<!-- Page {page_num} extraction failed: {e} -->",
            success=False,
            error=str(e),
            duration_ms=duration_ms,
            detected_type=visual_doc_type,
        )


async def extract_all_pages_vision(
    page_images_base64: dict[int, str],
    document_type: str = "document",
    max_concurrent: int = 3,
    on_page_complete: callable | None = None,
    filename: str = "",
    auto_detect_visual_type: bool = True,
) -> tuple[dict[int, str], VisionExtractionStats]:
    """Extract markdown from all pages using vision, with parallel processing.

    Automatically detects visual document types (posters, infographics) and
    uses specialized extraction prompts for better results.

    Args:
        page_images_base64: Dict mapping page_num to base64 image
        document_type: Type of document for context
        max_concurrent: Max concurrent LLM calls
        on_page_complete: Optional callback(page_num, markdown, success) for progress
        filename: Original filename (used for document type hints)
        auto_detect_visual_type: If True, detect visual document types automatically

    Returns:
        Tuple of (page_markdowns dict, extraction stats)
    """
    start_time = time.time()
    total_pages = len(page_images_base64)

    logger.info(f"Starting vision extraction for {total_pages} pages (max_concurrent={max_concurrent})")

    # Detect visual document type for single-page documents
    visual_doc_type = VisualDocumentType.STANDARD
    if auto_detect_visual_type and total_pages > 0:
        first_page_num = min(page_images_base64.keys())
        first_page_image = page_images_base64[first_page_num]

        visual_doc_type = await detect_visual_document_type(
            image_base64=first_page_image,
            filename=filename,
            total_pages=total_pages,
        )

        if visual_doc_type != VisualDocumentType.STANDARD:
            logger.info(f"Visual document type detected: {visual_doc_type.value}")

    semaphore = asyncio.Semaphore(max_concurrent)
    results: dict[int, VisionExtractionResult] = {}

    async def extract_with_semaphore(page_num: int, image_base64: str) -> VisionExtractionResult:
        async with semaphore:
            result = await extract_page_markdown_vision(
                page_num=page_num,
                image_base64=image_base64,
                document_type=document_type,
                total_pages=total_pages,
                visual_doc_type=visual_doc_type,
            )

            if on_page_complete:
                try:
                    on_page_complete(page_num, result.markdown, result.success)
                except Exception as e:
                    logger.warning(f"on_page_complete callback failed: {e}")

            return result

    # Create tasks for all pages
    tasks = [
        extract_with_semaphore(page_num, image_base64)
        for page_num, image_base64 in sorted(page_images_base64.items())
    ]

    # Run all tasks
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect results
    page_markdowns: dict[int, str] = {}
    detected_types: dict[int, VisualDocumentType] = {}
    total_tokens_input = 0
    total_tokens_output = 0
    pages_extracted = 0

    for i, task_result in enumerate(task_results):
        page_num = sorted(page_images_base64.keys())[i]

        if isinstance(task_result, Exception):
            logger.error(f"Page {page_num} extraction exception: {task_result}")
            page_markdowns[page_num] = f"<!-- Page {page_num} extraction failed: {task_result} -->"
        else:
            results[page_num] = task_result
            page_markdowns[page_num] = task_result.markdown
            detected_types[page_num] = task_result.detected_type
            total_tokens_input += task_result.tokens_input
            total_tokens_output += task_result.tokens_output
            if task_result.success:
                pages_extracted += 1

    total_duration_ms = int((time.time() - start_time) * 1000)

    # Estimate cost (Haiku pricing: $0.25/M input, $1.25/M output)
    estimated_cost = (total_tokens_input * 0.00025 / 1000) + (total_tokens_output * 0.00125 / 1000)

    stats = VisionExtractionStats(
        pages_extracted=pages_extracted,
        total_tokens_input=total_tokens_input,
        total_tokens_output=total_tokens_output,
        total_duration_ms=total_duration_ms,
        estimated_cost=estimated_cost,
        detected_types=detected_types if detected_types else None,
    )

    logger.info(
        f"Vision extraction complete: {pages_extracted}/{total_pages} pages, "
        f"type={visual_doc_type.value}, "
        f"{total_tokens_input}+{total_tokens_output} tokens, "
        f"${estimated_cost:.4f}, {total_duration_ms}ms"
    )

    return page_markdowns, stats
