#!/usr/bin/env python3
"""
Call Docling API and extract all images to files.
Extracts both PAGE images (full 8.5x11 renders) and ELEMENT images (figures/tables).

For untagged/scanned PDFs, skips Docling OCR so Haiku subagents can do vision-based OCR.

Usage:
    python call_docling.py <pdf_path> <output_dir> [--no-ocr]

Example:
    python call_docling.py /path/to/document.pdf /tmp/pdf-access-123/docling
    python call_docling.py /path/to/scanned.pdf /tmp/pdf-access-123/docling --no-ocr
"""

import json
import base64
import re
import sys
from pathlib import Path
import subprocess
import argparse


def call_docling(pdf_path: str, output_dir: str, skip_ocr: bool = False) -> dict:
    """
    Call Docling API and extract everything.

    Args:
        pdf_path: Path to the input PDF file
        output_dir: Directory to save outputs (markdown, JSON, images)
        skip_ocr: If True, skip Docling OCR (for untagged PDFs where Haiku will do OCR)

    Returns:
        dict with extraction statistics
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "pages").mkdir(exist_ok=True)
    (output / "elements").mkdir(exist_ok=True)

    ocr_mode = "skip OCR (Haiku will process)" if skip_ocr else "with OCR"
    print(f"Calling Docling API for: {pdf_path} ({ocr_mode})")

    # Build curl command
    curl_cmd = [
        "curl", "-s", "-X", "POST", "http://localhost:5001/v1/convert/file",
        "-F", f"files=@{pdf_path}",
        "-F", "to_formats=md",
        "-F", "to_formats=json",
        "-F", "image_export_mode=embedded",  # Get base64 in markdown
    ]

    # Only enable Docling OCR for tagged PDFs
    if not skip_ocr:
        curl_cmd.extend(["-F", "do_ocr=true"])

    result = subprocess.run(curl_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error calling Docling: {result.stderr}")
        sys.exit(1)

    try:
        resp = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Error parsing Docling response: {e}")
        print(f"Response: {result.stdout[:500]}...")
        sys.exit(1)

    if resp.get('status') != 'success':
        print(f"Docling error: {resp.get('errors')}")
        sys.exit(1)

    doc = resp['document']
    json_content = doc.get('json_content', {})

    stats = {
        "pages_extracted": 0,
        "elements_extracted": 0,
        "page_files": [],
        "element_files": [],
        "ocr_skipped": skip_ocr,
        "needs_haiku_ocr": skip_ocr
    }

    # 1. Extract PAGE IMAGES (full 8.5x11 renders)
    print("\nExtracting page images...")
    pages = json_content.get('pages', {})
    for page_id, page_data in pages.items():
        if 'image' in page_data and page_data['image']:
            img = page_data['image']
            uri = img.get('uri', '')
            if uri.startswith('data:image/'):
                # Parse base64
                match = re.match(r'data:image/(\w+);base64,(.+)', uri)
                if match:
                    fmt, b64 = match.groups()
                    img_data = base64.b64decode(b64)
                    page_num = page_data.get('page_no', page_id)
                    filename = f"page_{int(page_num):03d}.{fmt}"
                    filepath = output / "pages" / filename
                    with open(filepath, 'wb') as f:
                        f.write(img_data)
                    print(f"  {filename} ({len(img_data):,} bytes)")
                    stats["pages_extracted"] += 1
                    stats["page_files"].append(str(filepath))

    # 2. Extract ELEMENT IMAGES from markdown (figures/tables)
    print("\nExtracting element images...")
    md = doc.get('md_content', '')
    pattern = r'!\[([^\]]*)\]\((data:image/(\w+);base64,([^)]+))\)'
    matches = re.findall(pattern, md)

    new_md = md
    for i, (alt, full_data, fmt, b64) in enumerate(matches):
        filename = f"picture_{i:03d}.{fmt}"
        filepath = output / "elements" / filename

        img_data = base64.b64decode(b64)
        with open(filepath, 'wb') as f:
            f.write(img_data)

        # Update markdown to use relative file path
        rel_path = f"elements/{filename}"
        new_md = new_md.replace(full_data, rel_path, 1)
        print(f"  {filename} ({len(img_data):,} bytes)")
        stats["elements_extracted"] += 1
        stats["element_files"].append(str(filepath))

    # 3. Save cleaned markdown (with file paths instead of base64)
    md_path = output / "document.md"
    with open(md_path, 'w') as f:
        f.write(new_md)
    print(f"\nSaved: {md_path}")

    # 4. Save structured JSON (remove base64 from page images to reduce size)
    # Keep structure but remove large base64 data
    clean_json = json_content.copy()
    if 'pages' in clean_json:
        for page_id, page_data in clean_json['pages'].items():
            if 'image' in page_data and page_data['image']:
                page_num = page_data.get('page_no', page_id)
                page_data['image'] = {
                    'uri': f"pages/page_{int(page_num):03d}.png",
                    'width': page_data['image'].get('size', {}).get('width'),
                    'height': page_data['image'].get('size', {}).get('height')
                }

    json_path = output / "document.json"
    with open(json_path, 'w') as f:
        json.dump(clean_json, f, indent=2)
    print(f"Saved: {json_path}")

    # 5. Save extraction stats
    stats_path = output / "extraction_stats.json"
    with open(stats_path, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"Saved: {stats_path}")

    print(f"\n=== Extraction Complete ===")
    print(f"  Total pages: {len(pages)}")
    print(f"  Page images extracted: {stats['pages_extracted']}")
    print(f"  Element images extracted: {stats['elements_extracted']}")

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Call Docling API and extract images from PDF'
    )
    parser.add_argument('pdf_path', help='Path to the PDF file')
    parser.add_argument('output_dir', help='Directory to save outputs')
    parser.add_argument('--no-ocr', action='store_true',
                        help='Skip Docling OCR (for untagged PDFs where Haiku will do OCR)')

    args = parser.parse_args()

    if not Path(args.pdf_path).exists():
        print(f"Error: PDF file not found: {args.pdf_path}")
        sys.exit(1)

    call_docling(args.pdf_path, args.output_dir, skip_ocr=args.no_ocr)
