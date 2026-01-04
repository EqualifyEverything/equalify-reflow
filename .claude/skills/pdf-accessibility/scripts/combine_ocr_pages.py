#!/usr/bin/env python3
"""
Combine per-page OCR results into a single document.

Usage:
    python combine_ocr_pages.py <workspace_dir>

Example:
    python combine_ocr_pages.py /tmp/pdf-access-123
"""

import json
import sys
from pathlib import Path
from datetime import datetime


def combine_ocr_pages(workspace: str) -> dict:
    """
    Combine all ocr_page_NNN.md files into a single document.

    Args:
        workspace: Path to the workspace directory

    Returns:
        dict with combination stats
    """
    ws = Path(workspace)
    work_dir = ws / "work"

    if not work_dir.exists():
        print(f"Error: work directory not found: {work_dir}")
        sys.exit(1)

    # Find all OCR page files
    page_files = sorted(work_dir.glob("ocr_page_*.md"))

    if not page_files:
        print("Error: No OCR page files found (ocr_page_*.md)")
        sys.exit(1)

    print(f"Found {len(page_files)} OCR page files")

    # Combine content - clean output only, no metadata header
    combined_content = []

    stats = {
        "total_pages": len(page_files),
        "pages_combined": 0,
        "total_illegible_sections": 0,
        "low_confidence_pages": [],
        "per_page": []
    }

    for page_file in page_files:
        # Read markdown content
        with open(page_file) as f:
            content = f.read()

        combined_content.append(content.strip())
        combined_content.append("\n\n")
        stats["pages_combined"] += 1

        # Try to read corresponding JSON metadata
        json_file = page_file.with_suffix(".json")
        if json_file.exists():
            try:
                with open(json_file) as f:
                    page_meta = json.load(f)

                stats["per_page"].append(page_meta)

                illegible = page_meta.get("illegible_sections", 0)
                stats["total_illegible_sections"] += illegible

                if page_meta.get("confidence") == "low":
                    stats["low_confidence_pages"].append(page_meta.get("page", "?"))

            except (json.JSONDecodeError, KeyError):
                pass

        print(f"  Combined: {page_file.name}")

    # Write combined document
    output_path = work_dir / "ocr_results.md"
    with open(output_path, "w") as f:
        f.write("\n".join(combined_content))
    print(f"\nWritten: {output_path}")

    # Write combined stats
    stats_path = work_dir / "ocr_results.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Written: {stats_path}")

    # Report
    print(f"\n=== OCR Combination Complete ===")
    print(f"  Pages combined: {stats['pages_combined']}")
    print(f"  Illegible sections: {stats['total_illegible_sections']}")
    if stats["low_confidence_pages"]:
        print(f"  Low confidence pages: {stats['low_confidence_pages']}")

    return stats


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: combine_ocr_pages.py <workspace_dir>")
        sys.exit(1)

    workspace = sys.argv[1]

    if not Path(workspace).exists():
        print(f"Error: Workspace not found: {workspace}")
        sys.exit(1)

    combine_ocr_pages(workspace)
