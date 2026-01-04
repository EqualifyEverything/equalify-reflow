#!/usr/bin/env python3
"""
Extract context for each element from DoclingDocument.
Creates JSON context files that subagents can read to understand each element.

Usage:
    python extract_context.py <docling_dir> <context_dir>

Example:
    python extract_context.py /tmp/pdf-access-123/docling /tmp/pdf-access-123/context
"""

import json
import re
import sys
from pathlib import Path


def get_surrounding_text(texts: list, page_no: int, bbox: dict, window: int = 3) -> str:
    """
    Get text elements near the given bounding box on the same page.

    Args:
        texts: List of text elements from DoclingDocument
        page_no: Page number to search
        bbox: Bounding box of the target element
        window: Number of text elements to include before/after

    Returns:
        Concatenated surrounding text
    """
    # Find text elements on the same page
    page_texts = []
    for text in texts:
        prov = text.get('prov', [])
        if prov and prov[0].get('page_no') == page_no:
            page_texts.append(text)

    # Sort by position (top to bottom, roughly)
    # This is a simplified approach - could be improved with better spatial analysis
    page_texts.sort(key=lambda t: t.get('prov', [{}])[0].get('bbox', {}).get('t', 0), reverse=True)

    # Find texts near our element's bbox
    target_y = bbox.get('t', 0) if bbox else 0
    nearby = []
    for text in page_texts:
        text_bbox = text.get('prov', [{}])[0].get('bbox', {})
        text_y = text_bbox.get('t', 0)
        # Include texts within a reasonable vertical distance
        if abs(text_y - target_y) < 200:  # ~2 inches at 100 DPI
            nearby.append(text.get('text', ''))

    return ' '.join(nearby[:window * 2])


def extract_context(docling_dir: str, context_dir: str) -> dict:
    """
    Generate context files for subagents.

    Args:
        docling_dir: Directory containing Docling outputs
        context_dir: Directory to write context files

    Returns:
        dict with extraction statistics
    """
    docling_path = Path(docling_dir)
    context_path = Path(context_dir)
    context_path.mkdir(parents=True, exist_ok=True)

    # Load DoclingDocument JSON
    json_path = docling_path / "document.json"
    if not json_path.exists():
        print(f"Error: document.json not found in {docling_dir}")
        sys.exit(1)

    with open(json_path) as f:
        doc = json.load(f)

    # Load markdown to get line numbers
    md_path = docling_path / "document.md"
    md_content = ""
    md_lines = []
    if md_path.exists():
        with open(md_path) as f:
            md_content = f.read()
            md_lines = md_content.split('\n')

    stats = {
        "metadata_created": True,
        "picture_contexts": 0,
        "table_contexts": 0,
        "heading_contexts": 0
    }

    # Extract page info
    pages_info = {}
    for page_id, page_data in doc.get('pages', {}).items():
        page_no = page_data.get('page_no', page_id)
        pages_info[page_no] = {
            "page_no": page_no,
            "width": page_data.get('size', {}).get('width'),
            "height": page_data.get('size', {}).get('height'),
            "image_path": str(docling_path / "pages" / f"page_{int(page_no):03d}.png")
        }

    # 1. Document metadata
    texts = doc.get('texts', [])
    pictures = doc.get('pictures', [])
    tables = doc.get('tables', [])

    # Detect if document is scanned (heuristic: if OCR was applied and confidence is available)
    # For now, we'll mark as "unknown" - could be enhanced with more Docling metadata
    document_type = "native"  # Could be "scanned" or "hybrid"

    # Count headings
    headings = [t for t in texts if t.get('label', '').startswith('section_header')]

    metadata = {
        "name": doc.get("name", ""),
        "document_type": document_type,
        "total_pages": len(doc.get('pages', {})),
        "total_pictures": len(pictures),
        "total_tables": len(tables),
        "total_headings": len(headings),
        "texts_count": len(texts),
        "pages": pages_info,
        "warnings": []
    }

    with open(context_path / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Created: metadata.json")

    # 2. Picture contexts
    print("\nCreating picture contexts...")
    for i, pic in enumerate(pictures):
        prov = pic.get('prov', [{}])[0]
        page_no = prov.get('page_no', 1)
        bbox = prov.get('bbox', {})

        # Find the image in markdown to get line number
        # Pattern: ![Image](elements/picture_NNN.png) or similar
        md_line = None
        for line_no, line in enumerate(md_lines, 1):
            if f"picture_{i:03d}" in line or f"image_{i:03d}" in line:
                md_line = line_no
                break

        # Get surrounding text for context
        surrounding = get_surrounding_text(texts, page_no, bbox)

        context = {
            "element_type": "picture",
            "index": i,
            "image_path": str(docling_path / "elements" / f"picture_{i:03d}.png"),
            "page_image_path": str(docling_path / "pages" / f"page_{int(page_no):03d}.png"),
            "page_no": page_no,
            "label": pic.get("label", "picture"),
            "captions": pic.get("captions", []),
            "bounding_box": bbox,
            "surrounding_text": surrounding,
            "markdown_line": md_line,
            "current_alt_text": ""  # Will be populated if alt-text exists
        }

        # Try to extract current alt-text from markdown
        if md_line and md_line <= len(md_lines):
            line = md_lines[md_line - 1]
            alt_match = re.search(r'!\[([^\]]*)\]', line)
            if alt_match:
                context["current_alt_text"] = alt_match.group(1)

        with open(context_path / f"picture_{i:03d}.json", "w") as f:
            json.dump(context, f, indent=2)
        print(f"  picture_{i:03d}.json (page {page_no})")
        stats["picture_contexts"] += 1

    # 3. Table contexts
    print("\nCreating table contexts...")
    for i, table in enumerate(tables):
        prov = table.get('prov', [{}])[0]
        page_no = prov.get('page_no', 1)
        bbox = prov.get('bbox', {})

        # Find table in markdown
        md_line = None
        in_table = False
        for line_no, line in enumerate(md_lines, 1):
            if line.strip().startswith('|') and not in_table:
                # This is a heuristic - tables start with |
                # Could be improved with better tracking
                md_line = line_no
                in_table = True
            elif in_table and not line.strip().startswith('|'):
                in_table = False

        surrounding = get_surrounding_text(texts, page_no, bbox)

        context = {
            "element_type": "table",
            "index": i,
            "page_image_path": str(docling_path / "pages" / f"page_{int(page_no):03d}.png"),
            "page_no": page_no,
            "bounding_box": bbox,
            "surrounding_text": surrounding,
            "markdown_line_start": md_line,
            "data": table.get("data", {}),  # Table grid data if available
            "num_rows": table.get("data", {}).get("num_rows", 0),
            "num_cols": table.get("data", {}).get("num_cols", 0)
        }

        with open(context_path / f"table_{i:03d}.json", "w") as f:
            json.dump(context, f, indent=2)
        print(f"  table_{i:03d}.json (page {page_no})")
        stats["table_contexts"] += 1

    # 4. Heading analysis
    print("\nAnalyzing heading structure...")
    heading_analysis = {
        "headings": [],
        "issues": []
    }

    current_level = 0
    for text in texts:
        label = text.get('label', '')
        if label.startswith('section_header'):
            # Extract level from markdown (# = 1, ## = 2, etc.)
            text_content = text.get('text', '')
            level = 0

            # Find in markdown to get actual level
            for line in md_lines:
                if text_content in line and line.strip().startswith('#'):
                    level = len(line.split()[0])  # Count #s
                    break

            heading_info = {
                "text": text_content,
                "level": level,
                "page_no": text.get('prov', [{}])[0].get('page_no', 1)
            }
            heading_analysis["headings"].append(heading_info)

            # Check for skipped levels
            if level > current_level + 1 and current_level > 0:
                heading_analysis["issues"].append({
                    "type": "skipped_level",
                    "text": text_content,
                    "expected": current_level + 1,
                    "actual": level
                })

            current_level = level

    with open(context_path / "headings.json", "w") as f:
        json.dump(heading_analysis, f, indent=2)
    print(f"Created: headings.json ({len(heading_analysis['headings'])} headings, {len(heading_analysis['issues'])} issues)")

    # 5. Save stats
    with open(context_path / "extraction_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    print(f"\n=== Context Extraction Complete ===")
    print(f"  Picture contexts: {stats['picture_contexts']}")
    print(f"  Table contexts: {stats['table_contexts']}")
    print(f"  Heading issues found: {len(heading_analysis['issues'])}")

    return stats


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: extract_context.py <docling_dir> <context_dir>")
        print("Example: python extract_context.py /tmp/pdf-access-123/docling /tmp/pdf-access-123/context")
        sys.exit(1)

    docling_dir = sys.argv[1]
    context_dir = sys.argv[2]

    if not Path(docling_dir).exists():
        print(f"Error: Docling directory not found: {docling_dir}")
        sys.exit(1)

    extract_context(docling_dir, context_dir)
