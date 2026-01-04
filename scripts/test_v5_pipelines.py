#!/usr/bin/env python
"""Test script to compare original vs optimized V5 pipelines.

Usage:
    # From project root, with Docker running:
    make shell
    python scripts/test_v5_pipelines.py path/to/test.pdf

    # Or directly with PYTHONPATH:
    PYTHONPATH=. python scripts/test_v5_pipelines.py path/to/test.pdf

    # Use --optimized-only or --original-only to run just one
    PYTHONPATH=. python scripts/test_v5_pipelines.py test.pdf --optimized-only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add src to path if needed
sys.path.insert(0, str(Path(__file__).parent.parent))


async def load_pdf(pdf_path: str) -> tuple[dict, dict, dict, float]:
    """Load a PDF and extract pages using Docling.

    Returns:
        Tuple of (page_markdowns, page_images, element_bboxes, page_width)
    """
    from docling.document_converter import DocumentConverter
    from PIL import Image

    print(f"Loading PDF: {pdf_path}")

    converter = DocumentConverter()
    result = converter.convert(pdf_path)

    # Get markdown per page
    page_markdowns: dict[int, str] = {}
    for i, page in enumerate(result.document.pages, 1):
        # Simple extraction - in production use proper page-level markdown
        page_markdowns[i] = result.document.export_to_markdown()

    # For testing, just use first few pages
    if len(page_markdowns) > 5:
        page_markdowns = {k: v for k, v in list(page_markdowns.items())[:5]}
        print(f"  (Limited to first 5 pages for testing)")

    # Get page images
    page_images: dict[int, Image.Image] = {}
    # Note: Docling doesn't directly provide page images,
    # you'd need pdf2image or similar
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, first_page=1, last_page=len(page_markdowns))
        for i, img in enumerate(images, 1):
            page_images[i] = img
    except ImportError:
        print("  Warning: pdf2image not available, using placeholder images")
        # Create placeholder images for testing
        for i in page_markdowns:
            page_images[i] = Image.new('RGB', (612, 792), color='white')

    # Empty bboxes for testing
    element_bboxes: dict = {}
    page_width = 612.0

    print(f"  Loaded {len(page_markdowns)} pages")

    return page_markdowns, page_images, element_bboxes, page_width


async def run_original_pipeline(
    filename: str,
    page_markdowns: dict,
    page_images: dict,
    element_bboxes: dict,
    page_width: float,
) -> dict:
    """Run the original V5 pipeline."""
    from src.agents.v5 import process_document_v5

    print("\n" + "="*60)
    print("RUNNING ORIGINAL PIPELINE")
    print("="*60)

    start = time.time()

    result, event_bus = await process_document_v5(
        filename=filename,
        page_markdowns=page_markdowns,
        page_images=page_images,
        element_bboxes=element_bboxes,
        page_width=page_width,
    )

    duration = time.time() - start

    return {
        "pipeline": "original",
        "success": result.success,
        "duration_ms": int(duration * 1000),
        "total_edits": result.total_edits,
        "total_jobs": result.total_jobs,
        "input_tokens": result.total_input_tokens,
        "output_tokens": result.total_output_tokens,
        "cost": result.total_cost,
        "verification_passed": result.verification.passed,
        "pages_passed": result.verification.pages_passed,
        "critical_issues": len(result.verification.critical_issues),
    }


async def run_optimized_pipeline(
    filename: str,
    page_markdowns: dict,
    page_images: dict,
) -> dict:
    """Run the optimized V5 pipeline."""
    from src.agents.v5 import process_document_v5_optimized

    print("\n" + "="*60)
    print("RUNNING OPTIMIZED PIPELINE")
    print("="*60)

    start = time.time()

    result, event_bus = await process_document_v5_optimized(
        filename=filename,
        page_markdowns=page_markdowns,
        page_images=page_images,
    )

    duration = time.time() - start

    return {
        "pipeline": "optimized",
        "success": result.success,
        "duration_ms": int(duration * 1000),
        "total_edits": result.total_edits,
        "total_jobs": result.total_jobs,  # Will be 0 for optimized
        "input_tokens": result.total_input_tokens,
        "output_tokens": result.total_output_tokens,
        "cost": result.total_cost,
        "verification_passed": result.verification.passed,
        "pages_passed": result.verification.pages_passed,
        "critical_issues": len(result.verification.critical_issues),
    }


def print_comparison(original: dict | None, optimized: dict | None):
    """Print side-by-side comparison of results."""
    print("\n" + "="*60)
    print("COMPARISON")
    print("="*60)

    metrics = [
        ("Success", "success"),
        ("Duration (ms)", "duration_ms"),
        ("Total Edits", "total_edits"),
        ("Input Tokens", "input_tokens"),
        ("Output Tokens", "output_tokens"),
        ("Cost ($)", "cost"),
        ("Verification Passed", "verification_passed"),
        ("Pages Passed", "pages_passed"),
        ("Critical Issues", "critical_issues"),
    ]

    print(f"{'Metric':<25} {'Original':>15} {'Optimized':>15} {'Diff':>15}")
    print("-" * 70)

    for label, key in metrics:
        orig_val = original.get(key, "N/A") if original else "N/A"
        opt_val = optimized.get(key, "N/A") if optimized else "N/A"

        # Calculate diff for numeric values
        diff = ""
        if isinstance(orig_val, (int, float)) and isinstance(opt_val, (int, float)):
            if key == "cost":
                diff = f"{opt_val - orig_val:+.4f}"
            elif key == "duration_ms":
                pct = ((opt_val - orig_val) / orig_val * 100) if orig_val else 0
                diff = f"{pct:+.1f}%"
            else:
                diff = f"{opt_val - orig_val:+d}" if isinstance(orig_val, int) else f"{opt_val - orig_val:+.2f}"

        # Format values
        if key == "cost":
            orig_str = f"${orig_val:.4f}" if isinstance(orig_val, float) else str(orig_val)
            opt_str = f"${opt_val:.4f}" if isinstance(opt_val, float) else str(opt_val)
        else:
            orig_str = str(orig_val)
            opt_str = str(opt_val)

        print(f"{label:<25} {orig_str:>15} {opt_str:>15} {diff:>15}")


async def main():
    parser = argparse.ArgumentParser(description="Compare V5 pipelines")
    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file to test")
    parser.add_argument("--original-only", action="store_true", help="Run only original pipeline")
    parser.add_argument("--optimized-only", action="store_true", help="Run only optimized pipeline")
    parser.add_argument("--mock", action="store_true", help="Use mock data instead of real PDF")

    args = parser.parse_args()

    # Load or mock the PDF data
    if args.mock or not args.pdf_path:
        print("Using mock data for testing...")
        page_markdowns = {
            1: "# Course Syllabus\n\n## Instructor Information\n\nDr. Smith\n\n## Course Overview\n\nThis course covers...",
            2: "## Week 1: Introduction\n\n### Learning Objectives\n\n- Understand basics\n- Apply concepts\n\n![](figure1.png)\n",
            3: "## Week 2: Advanced Topics\n\n### Reading Materials\n\nChapter 1-3\n\n<!-- table -->\n",
        }
        from PIL import Image
        page_images = {i: Image.new('RGB', (612, 792), 'white') for i in page_markdowns}
        element_bboxes: dict = {}
        page_width = 612.0
        filename = "mock_document.pdf"
    else:
        page_markdowns, page_images, element_bboxes, page_width = await load_pdf(args.pdf_path)
        filename = Path(args.pdf_path).name

    original_result = None
    optimized_result = None

    # Run original pipeline
    if not args.optimized_only:
        try:
            original_result = await run_original_pipeline(
                filename=filename,
                page_markdowns=page_markdowns,
                page_images=page_images,
                element_bboxes=element_bboxes,
                page_width=page_width,
            )
            print(f"\nOriginal pipeline completed in {original_result['duration_ms']}ms")
        except Exception as e:
            print(f"\nOriginal pipeline failed: {e}")
            import traceback
            traceback.print_exc()

    # Run optimized pipeline
    if not args.original_only:
        try:
            optimized_result = await run_optimized_pipeline(
                filename=filename,
                page_markdowns=page_markdowns,
                page_images=page_images,
            )
            print(f"\nOptimized pipeline completed in {optimized_result['duration_ms']}ms")
        except Exception as e:
            print(f"\nOptimized pipeline failed: {e}")
            import traceback
            traceback.print_exc()

    # Print comparison
    if original_result or optimized_result:
        print_comparison(original_result, optimized_result)


if __name__ == "__main__":
    asyncio.run(main())
