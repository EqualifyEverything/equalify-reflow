#!/usr/bin/env python3
"""Test script for chained analysis approach.

This script demonstrates breaking the monolithic analysis prompt into
focused, sequential steps that build on each other.

The chain:
1. Layout Detection (visual only) - What's the layout of each page?
2. Document Type (visual + layout) - What kind of document is this?
3. Heading Structure (needs layout for reading order) - Build heading tree
4. Page Features (per-page detection) - Images, tables, code, math
5. Agent Routing (logic over data) - Which agents are needed?

Run from project root:
    python scripts/test_chained_analysis.py [pdf_path] [--pages N]

Examples:
    # Test with attention paper (two-column)
    python scripts/test_chained_analysis.py project-docs/pdfs/07_attention_transformer_paper.pdf

    # Test first 3 pages only
    python scripts/test_chained_analysis.py project-docs/pdfs/07_attention_transformer_paper.pdf --pages 3
"""

import asyncio
import sys
import argparse
import json
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.pdf_converter import PDFConverter, PageData
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier


# =============================================================================
# Step 1: Layout Detection
# =============================================================================

class PageLayout(BaseModel):
    """Layout for a single page."""
    page_num: int
    layout_type: Literal["single_column", "two_column", "mixed"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(description="One sentence describing visual evidence")


class LayoutOutput(BaseModel):
    """Output from layout detection step."""
    pages: list[PageLayout]


LAYOUT_SYSTEM_PROMPT = """You analyze PDF page layouts. For each page image, determine the layout type.

Layout types:
- single_column: Text flows across the full page width in one column
- two_column: Page is divided into two columns with a vertical gutter between them
- mixed: Single page has both single-column and two-column sections

Visual cues for two_column:
- Vertical whitespace (gutter) dividing page roughly in half
- Text blocks that don't span full page width
- Parallel content at the same vertical position on left and right

Be concise. State your visual evidence in one sentence."""


# =============================================================================
# Step 2: Document Type Classification
# =============================================================================

class DocumentTypeOutput(BaseModel):
    """Output from document type classification."""
    document_type: Literal["syllabus", "lecture_notes", "exam", "research_paper", "handout", "thesis", "report", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(description="Key visual indicators for this classification")


DOCTYPE_SYSTEM_PROMPT = """You classify academic document types based on visual appearance.

Document types:
- syllabus: Course outline with schedule, grading, policies
- lecture_notes: Class slides or notes
- exam: Test, quiz, or assignment with questions
- research_paper: Academic paper with abstract, sections, citations (often two-column)
- handout: Supplementary material, worksheets
- thesis: Long-form academic work with chapters
- report: Technical or organizational report
- other: Doesn't fit above categories

Look at the first page primarily. Consider:
- Title format and positioning
- Presence of abstract
- Two-column layout (suggests research paper)
- Course codes or instructor names (suggests syllabus)
- Question numbering (suggests exam)

Be concise."""


# =============================================================================
# Step 3: Heading Structure
# =============================================================================

class Heading(BaseModel):
    """A heading in the document."""
    level: int = Field(ge=1, le=6, description="Heading level 1-6 (H1-H6)")
    text: str = Field(description="Exact heading text")
    page_num: int
    section_number: str | None = Field(default=None, description="Section number if present (e.g., '1.1', '2')")


class HeadingsOutput(BaseModel):
    """Output from heading structure extraction."""
    headings: list[Heading]
    reading_order_notes: str = Field(description="Notes about reading order decisions for multi-column pages")


HEADINGS_SYSTEM_PROMPT = """You extract document heading structure from PDF pages.

For each heading you find, record:
- level: H1 (main title) through H6 (deepest subsection)
- text: Exact heading text as it appears
- page_num: Which page it's on
- section_number: If the heading has a number like "1.1" or "Chapter 2"

CRITICAL for reading order:
- For single_column pages: read top to bottom
- For two_column pages: read LEFT column completely (top to bottom), THEN right column (top to bottom)
- This means a heading in the left column comes BEFORE a heading at the same height in the right column

Verify parent-child relationships:
- Section 5.1 must come AFTER section 5
- Don't skip levels (H1 → H3 without H2) unless that's genuinely how the document is structured

Be thorough but concise."""


# =============================================================================
# Step 4: Page Features
# =============================================================================

class PageFeatures(BaseModel):
    """Features detected on a single page."""
    page_num: int
    has_images: bool = Field(description="Informative images (charts, diagrams, photos) - NOT decorative")
    image_count: int = Field(ge=0)
    has_tables: bool = Field(description="Data tables with rows/columns")
    table_count: int = Field(ge=0)
    has_lists: bool = Field(description="Bulleted or numbered lists")
    has_code_blocks: bool = Field(description="Programming code or command-line content")
    has_math: bool = Field(description="Mathematical equations or formulas")
    has_figures_with_captions: bool = Field(description="Figures that have caption text")


class FeaturesOutput(BaseModel):
    """Output from page features detection."""
    pages: list[PageFeatures]


FEATURES_SYSTEM_PROMPT = """You detect content features on PDF pages.

For each page, identify:
- Informative images: Charts, graphs, diagrams, photos, screenshots (NOT decorative borders/logos)
- Data tables: Structured rows and columns of data
- Lists: Bulleted or numbered items
- Code blocks: Programming code, terminal output, monospace technical content
- Math: Equations, formulas, mathematical notation
- Figures with captions: Images that have associated caption text

Be accurate with counts. Only count informative content, not decorative elements."""


# =============================================================================
# Step 5: Agent Routing
# =============================================================================

class RoutingOutput(BaseModel):
    """Output from agent routing decision."""
    required_agents: list[Literal["figures", "tables", "structure", "typography"]]
    reasoning: str = Field(description="Why each agent is or isn't needed")


ROUTING_SYSTEM_PROMPT = """You decide which specialized agents should process a document.

Available agents:
- figures: Analyzes images, generates alt text. Needed if document has informative images.
- tables: Analyzes table structure for accessibility. Needed if document has data tables.
- structure: Validates heading hierarchy and reading order. Needed for complex hierarchies or two-column layouts.
- typography: Checks if visual styling conveys meaning (color-coding, bold for emphasis). Needed if styling is semantic.

Rules:
- Be conservative - only include agents that are clearly needed
- If document has two-column layout, include "structure" (reading order verification)
- If document has images, include "figures"
- If document has tables, include "tables"
- Only include "typography" if you see color-coded content or meaningful bold/italic usage"""


# =============================================================================
# Main Test Logic
# =============================================================================

async def run_chained_analysis(
    pages: list[PageData],
    max_pages: int | None = None,
    use_sonnet: bool = False,
    parallel: bool = False,
) -> dict:
    """Run the chained analysis on PDF pages.

    Args:
        pages: List of PageData from PDF conversion
        max_pages: Limit analysis to first N pages (for faster testing)
        use_sonnet: Use Sonnet instead of Haiku (for comparison)
        parallel: Run steps 2-4 in parallel after step 1

    Returns:
        Dictionary with results from each step
    """
    # Limit pages if requested
    if max_pages:
        pages = pages[:max_pages]

    total_pages = len(pages)
    print(f"\n{'='*60}")
    print(f"Running chained analysis on {total_pages} pages")
    print(f"Model: {'Sonnet' if use_sonnet else 'Haiku'}")
    print(f"Mode: {'PARALLEL (steps 2-4 concurrent)' if parallel else 'SEQUENTIAL'}")
    print(f"{'='*60}\n")

    # Select model
    model_tier = ModelTier.REASONING if use_sonnet else ModelTier.EFFICIENT
    model = BedrockConverseModel(MODEL_TIER_MAP[model_tier])

    # Prepare page images for multimodal input
    page_images = [
        BinaryContent(data=bytes.fromhex(page.image_base64) if len(page.image_base64) < 100
                      else __import__('base64').b64decode(page.image_base64),
                      media_type="image/png")
        for page in pages
    ]

    results = {}
    total_tokens = {"input": 0, "output": 0}

    # -------------------------------------------------------------------------
    # Step 1: Layout Detection
    # -------------------------------------------------------------------------
    print("Step 1: Layout Detection")
    print("-" * 40)
    start = time.time()

    layout_agent = Agent(
        model,
        output_type=LayoutOutput,
        system_prompt=LAYOUT_SYSTEM_PROMPT,
    )

    layout_prompt = [
        f"Analyze the layout of these {total_pages} PDF pages. For each page, determine if it's single_column, two_column, or mixed.",
        *page_images
    ]

    layout_result = await layout_agent.run(layout_prompt)
    layout_output = layout_result.output

    elapsed = time.time() - start
    usage = layout_result.usage()
    total_tokens["input"] += usage.input_tokens or 0
    total_tokens["output"] += usage.output_tokens or 0

    print(f"  Time: {elapsed:.1f}s | Tokens: {usage.input_tokens} in, {usage.output_tokens} out")
    for pl in layout_output.pages:
        print(f"  Page {pl.page_num}: {pl.layout_type} (conf: {pl.confidence:.2f})")
        print(f"    Evidence: {pl.evidence}")

    results["layout"] = layout_output.model_dump()

    # Summarize layouts for next steps
    layout_summary = ", ".join([
        f"Page {p.page_num}: {p.layout_type}"
        for p in layout_output.pages
    ])
    has_two_column = any(p.layout_type == "two_column" for p in layout_output.pages)

    # -------------------------------------------------------------------------
    # Steps 2-4: Run in parallel or sequential based on flag
    # -------------------------------------------------------------------------

    if parallel:
        # PARALLEL MODE: Steps 2, 3, 4 run concurrently
        print("\n[PARALLEL] Running Steps 2, 3, 4 concurrently...")
        print("-" * 40)
        parallel_start = time.time()

        async def run_doctype():
            """Step 2: Document Type Classification"""
            agent = Agent(model, output_type=DocumentTypeOutput, system_prompt=DOCTYPE_SYSTEM_PROMPT)
            prompt = [f"What type of document is this? Layout info: {layout_summary}", page_images[0]]
            if len(page_images) > 1:
                prompt.append(page_images[-1])
            return await agent.run(prompt)

        async def run_headings():
            """Step 3: Heading Structure (uses layout info)"""
            agent = Agent(model, output_type=HeadingsOutput, system_prompt=HEADINGS_SYSTEM_PROMPT)
            reading_order_guidance = ""
            if has_two_column:
                two_col_pages = [p.page_num for p in layout_output.pages if p.layout_type == "two_column"]
                reading_order_guidance = f"\n\nCRITICAL: Pages {two_col_pages} are two-column. Read each column top-to-bottom, left column first, then right column."
            prompt = [f"Extract all headings from this document.{reading_order_guidance}", *page_images]
            return await agent.run(prompt)

        async def run_features():
            """Step 4: Page Features Detection"""
            agent = Agent(model, output_type=FeaturesOutput, system_prompt=FEATURES_SYSTEM_PROMPT)
            prompt = [f"Detect content features on each of these {total_pages} pages.", *page_images]
            return await agent.run(prompt)

        # Run all three in parallel
        doctype_result, headings_result, features_result = await asyncio.gather(
            run_doctype(),
            run_headings(),
            run_features(),
        )

        parallel_elapsed = time.time() - parallel_start

        # Extract outputs
        doctype_output = doctype_result.output
        headings_output = headings_result.output
        features_output = features_result.output

        # Aggregate tokens
        for result in [doctype_result, headings_result, features_result]:
            usage = result.usage()
            total_tokens["input"] += usage.input_tokens or 0
            total_tokens["output"] += usage.output_tokens or 0

        # Print results
        print(f"\n  Step 2 (Doc Type): {doctype_output.document_type} (conf: {doctype_output.confidence:.2f})")
        print(f"    Evidence: {doctype_output.evidence}")

        print(f"\n  Step 3 (Headings): Found {len(headings_output.headings)} headings")
        for h in headings_output.headings[:5]:
            prefix = f"[{h.section_number}] " if h.section_number else ""
            print(f"    H{h.level} (p{h.page_num}): {prefix}{h.text[:40]}{'...' if len(h.text) > 40 else ''}")
        if len(headings_output.headings) > 5:
            print(f"    ... and {len(headings_output.headings) - 5} more")

        total_images = sum(p.image_count for p in features_output.pages)
        total_tables = sum(p.table_count for p in features_output.pages)
        pages_with_code = sum(1 for p in features_output.pages if p.has_code_blocks)
        pages_with_math = sum(1 for p in features_output.pages if p.has_math)

        print(f"\n  Step 4 (Features): {total_images} images, {total_tables} tables, {pages_with_code} code pages, {pages_with_math} math pages")

        print(f"\n  [PARALLEL] Total time for steps 2-4: {parallel_elapsed:.1f}s")

        results["document_type"] = doctype_output.model_dump()
        results["headings"] = headings_output.model_dump()
        results["features"] = features_output.model_dump()

    else:
        # SEQUENTIAL MODE: Original step-by-step execution

        # -------------------------------------------------------------------------
        # Step 2: Document Type Classification
        # -------------------------------------------------------------------------
        print("\nStep 2: Document Type Classification")
        print("-" * 40)
        start = time.time()

        doctype_agent = Agent(
            model,
            output_type=DocumentTypeOutput,
            system_prompt=DOCTYPE_SYSTEM_PROMPT,
        )

        # Only need first page (and maybe last) for document type
        doctype_prompt = [
            f"What type of document is this? Layout info: {layout_summary}",
            page_images[0],  # First page usually enough
        ]
        if len(page_images) > 1:
            doctype_prompt.append(page_images[-1])  # Add last page for context

        doctype_result = await doctype_agent.run(doctype_prompt)
        doctype_output = doctype_result.output

        elapsed = time.time() - start
        usage = doctype_result.usage()
        total_tokens["input"] += usage.input_tokens or 0
        total_tokens["output"] += usage.output_tokens or 0

        print(f"  Time: {elapsed:.1f}s | Tokens: {usage.input_tokens} in, {usage.output_tokens} out")
        print(f"  Type: {doctype_output.document_type} (conf: {doctype_output.confidence:.2f})")
        print(f"  Evidence: {doctype_output.evidence}")

        results["document_type"] = doctype_output.model_dump()

        # -------------------------------------------------------------------------
        # Step 3: Heading Structure
        # -------------------------------------------------------------------------
        print("\nStep 3: Heading Structure Extraction")
        print("-" * 40)
        start = time.time()

        headings_agent = Agent(
            model,
            output_type=HeadingsOutput,
            system_prompt=HEADINGS_SYSTEM_PROMPT,
        )

        # Include layout info in prompt for correct reading order
        reading_order_guidance = ""
        if has_two_column:
            two_col_pages = [p.page_num for p in layout_output.pages if p.layout_type == "two_column"]
            reading_order_guidance = f"\n\nCRITICAL: Pages {two_col_pages} are two-column. Read each column top-to-bottom, left column first, then right column."

        headings_prompt = [
            f"Extract all headings from this {doctype_output.document_type} document.{reading_order_guidance}",
            *page_images
        ]

        headings_result = await headings_agent.run(headings_prompt)
        headings_output = headings_result.output

        elapsed = time.time() - start
        usage = headings_result.usage()
        total_tokens["input"] += usage.input_tokens or 0
        total_tokens["output"] += usage.output_tokens or 0

        print(f"  Time: {elapsed:.1f}s | Tokens: {usage.input_tokens} in, {usage.output_tokens} out")
        print(f"  Found {len(headings_output.headings)} headings:")
        for h in headings_output.headings[:10]:  # Show first 10
            prefix = f"[{h.section_number}] " if h.section_number else ""
            print(f"    H{h.level} (p{h.page_num}): {prefix}{h.text[:50]}{'...' if len(h.text) > 50 else ''}")
        if len(headings_output.headings) > 10:
            print(f"    ... and {len(headings_output.headings) - 10} more")
        print(f"  Reading order notes: {headings_output.reading_order_notes}")

        results["headings"] = headings_output.model_dump()

        # -------------------------------------------------------------------------
        # Step 4: Page Features Detection
        # -------------------------------------------------------------------------
        print("\nStep 4: Page Features Detection")
        print("-" * 40)
        start = time.time()

        features_agent = Agent(
            model,
            output_type=FeaturesOutput,
            system_prompt=FEATURES_SYSTEM_PROMPT,
        )

        features_prompt = [
            f"Detect content features on each of these {total_pages} pages.",
            *page_images
        ]

        features_result = await features_agent.run(features_prompt)
        features_output = features_result.output

        elapsed = time.time() - start
        usage = features_result.usage()
        total_tokens["input"] += usage.input_tokens or 0
        total_tokens["output"] += usage.output_tokens or 0

        print(f"  Time: {elapsed:.1f}s | Tokens: {usage.input_tokens} in, {usage.output_tokens} out")

        total_images = sum(p.image_count for p in features_output.pages)
        total_tables = sum(p.table_count for p in features_output.pages)
        pages_with_code = sum(1 for p in features_output.pages if p.has_code_blocks)
        pages_with_math = sum(1 for p in features_output.pages if p.has_math)

        print(f"  Summary: {total_images} images, {total_tables} tables, {pages_with_code} pages with code, {pages_with_math} pages with math")
        for pf in features_output.pages:
            features_list = []
            if pf.has_images: features_list.append(f"{pf.image_count} img")
            if pf.has_tables: features_list.append(f"{pf.table_count} tbl")
            if pf.has_lists: features_list.append("lists")
            if pf.has_code_blocks: features_list.append("code")
            if pf.has_math: features_list.append("math")
            if features_list:
                print(f"    Page {pf.page_num}: {', '.join(features_list)}")

        results["features"] = features_output.model_dump()

    # -------------------------------------------------------------------------
    # Step 5: Agent Routing
    # -------------------------------------------------------------------------
    print("\nStep 5: Agent Routing Decision")
    print("-" * 40)
    start = time.time()

    routing_agent = Agent(
        model,
        output_type=RoutingOutput,
        system_prompt=ROUTING_SYSTEM_PROMPT,
    )

    # This step doesn't need images - pure logic over collected data
    routing_prompt = f"""Based on the analysis so far, decide which specialized agents are needed.

Document type: {doctype_output.document_type}
Layout: {layout_summary}
Has two-column pages: {has_two_column}
Total images: {total_images}
Total tables: {total_tables}
Heading depth: {max((h.level for h in headings_output.headings), default=1)}
Pages with code: {pages_with_code}
Pages with math: {pages_with_math}

Which agents should process this document?"""

    routing_result = await routing_agent.run(routing_prompt)
    routing_output = routing_result.output

    elapsed = time.time() - start
    usage = routing_result.usage()
    total_tokens["input"] += usage.input_tokens or 0
    total_tokens["output"] += usage.output_tokens or 0

    print(f"  Time: {elapsed:.1f}s | Tokens: {usage.input_tokens} in, {usage.output_tokens} out")
    print(f"  Required agents: {routing_output.required_agents}")
    print(f"  Reasoning: {routing_output.reasoning}")

    results["routing"] = routing_output.model_dump()

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("ANALYSIS COMPLETE")
    print(f"{'='*60}")
    print(f"Total tokens: {total_tokens['input']} input, {total_tokens['output']} output")
    print(f"\nDocument: {doctype_output.document_type}")
    print(f"Layout: {'Two-column' if has_two_column else 'Single-column'}")
    print(f"Headings: {len(headings_output.headings)}")
    print(f"Images: {total_images}, Tables: {total_tables}")
    print(f"Agents needed: {routing_output.required_agents}")

    return results


async def main():
    parser = argparse.ArgumentParser(description="Test chained analysis approach")
    parser.add_argument("pdf_path", help="Path to PDF file to analyze")
    parser.add_argument("--pages", type=int, help="Limit to first N pages")
    parser.add_argument("--sonnet", action="store_true", help="Use Sonnet instead of Haiku")
    parser.add_argument("--parallel", action="store_true", help="Run steps 2-4 in parallel")
    parser.add_argument("--output", "-o", help="Save results to JSON file")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)

    print(f"Loading PDF: {pdf_path}")

    # Convert PDF to page images
    converter = PDFConverter()
    with open(pdf_path, "rb") as f:
        pdf_content = f.read()

    print(f"Converting PDF ({len(pdf_content)} bytes)...")
    conversion_result = await converter.convert_with_page_images(pdf_content)
    print(f"Converted: {conversion_result.total_pages} pages")

    # Run chained analysis
    results = await run_chained_analysis(
        pages=conversion_result.pages,
        max_pages=args.pages,
        use_sonnet=args.sonnet,
        parallel=args.parallel,
    )

    # Save results if requested
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
