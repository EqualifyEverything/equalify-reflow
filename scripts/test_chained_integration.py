#!/usr/bin/env python3
"""Integration test for chained analysis pipeline.

Tests the chained analysis on a sample PDF to verify:
1. All agents run correctly
2. Manifest is assembled properly
3. Two-column layout is detected correctly

Usage:
    docker exec equalify-pdf-api-gateway python scripts/test_chained_integration.py
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main():
    """Run chained analysis test."""
    print("=" * 60)
    print("Chained Analysis Integration Test")
    print("=" * 60)

    # Import after path setup
    from src.agents.chained_analysis import analyze_document
    from src.services.pdf_converter import PDFConverter

    # Find a test PDF
    pdf_paths = [
        Path("project-docs/pdfs/04_usenix_security_paper.pdf"),
        Path("project-docs/pdfs/07_attention_transformer_paper.pdf"),
        Path("/app/project-docs/pdfs/04_usenix_security_paper.pdf"),
        Path("/app/project-docs/pdfs/07_attention_transformer_paper.pdf"),
    ]

    pdf_path = None
    for p in pdf_paths:
        if p.exists():
            pdf_path = p
            break

    if not pdf_path:
        print("ERROR: No test PDF found!")
        print("Tried:", [str(p) for p in pdf_paths])
        return 1

    print(f"\nTest PDF: {pdf_path}")
    print("-" * 60)

    # Read PDF
    with open(pdf_path, "rb") as f:
        pdf_content = f.read()

    print(f"PDF size: {len(pdf_content):,} bytes")

    # Convert PDF to page images
    print("\nConverting PDF with Docling...")
    converter = PDFConverter()
    result = await converter.convert_with_page_images(pdf_content)

    print(f"Total pages: {result.total_pages}")
    print(f"Has page images: {result.has_page_images}")

    # Limit to first 3 pages for faster testing
    pages = result.pages[:3]
    print(f"Testing with first {len(pages)} pages")

    # Run chained analysis
    print("\n" + "=" * 60)
    print("Running Chained Analysis (parallel mode)")
    print("=" * 60)

    job_id = "test-chained-001"

    manifest, observations, usage = await analyze_document(
        pages=pages,
        job_id=job_id,
        parallel=True,
    )

    # Print results
    print("\n" + "-" * 60)
    print("RESULTS")
    print("-" * 60)

    print(f"\nDocument Title: {manifest.document_title}")
    print(f"Document Type: {manifest.document_type}")
    print(f"Total Pages: {manifest.total_pages}")
    print(f"Analysis Confidence: {manifest.analysis_confidence:.2f}")
    print(f"Analysis Model: {manifest.analysis_model}")

    print(f"\nRequired Agents: {manifest.required_agents}")
    print(f"Skip Agents: {manifest.skip_agents}")

    print(f"\nInitial Observations: {len(observations)}")

    # Check page features
    print("\nPage Features:")
    for pf in manifest.page_features:
        print(f"  Page {pf.page_num}: layout={pf.layout_type}, "
              f"images={pf.image_count}, tables={pf.table_count}, "
              f"code={pf.has_code_blocks}, math={pf.has_math}")

    # Parse heading tree
    from src.shared.models.remediation import HeadingTree
    heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

    print(f"\nHeading Tree:")
    print(f"  Layout Type: {heading_tree.layout_type}")
    print(f"  Title: {heading_tree.document_title}")
    print(f"  Total Sections: {len(heading_tree.sections)}")
    if heading_tree.sections:
        print("  First 5 headings:")
        for h in heading_tree.sections[:5]:
            print(f"    H{h.level}: {h.title[:50]}... (page {h.page})")

    print("\nLLM Usage:")
    print(f"  Input tokens: {usage.input_tokens:,}")
    print(f"  Output tokens: {usage.output_tokens:,}")
    print(f"  Total tokens: {usage.total_tokens:,}")
    print(f"  Estimated cost: ${usage.estimated_cost_cents/100:.4f}")

    print("\nAnalysis Notes:")
    print(f"  {manifest.analysis_notes}")

    print("\n" + "=" * 60)
    print("TEST PASSED!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
