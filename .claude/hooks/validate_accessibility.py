#!/usr/bin/env python3
"""
Hook: Validate final accessible markdown

Triggers: PostToolUse on Write to *accessible.md

Checks:
- No ![](image) (empty alt on informative images)
- No <!-- image --> placeholders remaining
- Valid heading hierarchy
- Tables have proper formatting
"""

import sys
import re
from pathlib import Path


def validate_accessibility(md_path: str) -> dict:
    """
    Validate accessible markdown file.

    Returns:
        dict with validation results
    """
    try:
        with open(md_path) as f:
            content = f.read()
            lines = content.split('\n')
    except FileNotFoundError as e:
        return {
            "valid": False,
            "error": f"Could not read file: {e}"
        }

    issues = []
    warnings = []

    # Check 1: Empty alt-text
    # Pattern: ![](anything) but not ![decorative description](...)
    empty_alt_pattern = r'!\[\]\([^)]+\)'
    for i, line in enumerate(lines, 1):
        matches = re.findall(empty_alt_pattern, line)
        for match in matches:
            issues.append({
                "line": i,
                "type": "empty_alt",
                "message": f"Image with empty alt-text: {match[:50]}"
            })

    # Check 2: Placeholders remaining
    placeholder_patterns = [
        r'<!--\s*image\s*-->',
        r'<!--\s*TODO\s*-->',
        r'\[IMAGE\]',
        r'\[FIGURE\]',
        r'ALT_TEXT_NEEDED',
    ]
    for i, line in enumerate(lines, 1):
        for pattern in placeholder_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                issues.append({
                    "line": i,
                    "type": "placeholder",
                    "message": f"Placeholder found: {line.strip()[:50]}"
                })

    # Check 3: Heading hierarchy
    heading_pattern = r'^(#{1,6})\s+(.+)$'
    headings = []
    for i, line in enumerate(lines, 1):
        match = re.match(heading_pattern, line)
        if match:
            level = len(match.group(1))
            text = match.group(2)
            headings.append({"line": i, "level": level, "text": text})

    # Check for skipped levels
    if headings:
        prev_level = 0
        for h in headings:
            if h["level"] > prev_level + 1 and prev_level > 0:
                issues.append({
                    "line": h["line"],
                    "type": "skipped_heading",
                    "message": f"Skipped heading level: H{prev_level} -> H{h['level']} ('{h['text'][:30]}')"
                })
            prev_level = h["level"]

        # Check for multiple H1s
        h1_count = sum(1 for h in headings if h["level"] == 1)
        if h1_count > 1:
            warnings.append({
                "type": "multiple_h1",
                "message": f"Document has {h1_count} H1 headings (should have 1)"
            })
        elif h1_count == 0:
            warnings.append({
                "type": "no_h1",
                "message": "Document has no H1 heading"
            })

    # Check 4: Table formatting
    in_table = False
    table_start = 0
    table_has_separator = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        if stripped.startswith('|') and stripped.endswith('|'):
            if not in_table:
                in_table = True
                table_start = i
                table_has_separator = False

            # Check for separator row
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                table_has_separator = True
        else:
            if in_table:
                # End of table
                if not table_has_separator:
                    issues.append({
                        "line": table_start,
                        "type": "table_no_separator",
                        "message": f"Table starting at line {table_start} missing separator row"
                    })
                in_table = False

    # Handle table at end of file
    if in_table and not table_has_separator:
        issues.append({
            "line": table_start,
            "type": "table_no_separator",
            "message": f"Table starting at line {table_start} missing separator row"
        })

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "stats": {
            "total_lines": len(lines),
            "total_headings": len(headings),
            "images_found": len(re.findall(r'!\[', content)),
            "tables_found": content.count('|---|') + content.count('| --- |')
        }
    }


def main():
    """Main entry point for hook."""
    if len(sys.argv) < 2:
        print("Usage: validate_accessibility.py <markdown_path>")
        sys.exit(1)

    md_path = sys.argv[1]
    validation = validate_accessibility(md_path)

    if not validation["valid"]:
        print("ACCESSIBILITY VALIDATION FAILED")
        print("=" * 40)
        for issue in validation["issues"]:
            line_info = f"Line {issue['line']}: " if 'line' in issue else ""
            print(f"  {line_info}{issue['type']}: {issue['message']}")
        sys.exit(1)

    if validation["warnings"]:
        print("ACCESSIBILITY VALIDATION PASSED (with warnings)")
        print("=" * 40)
        for warning in validation["warnings"]:
            print(f"  {warning['type']}: {warning['message']}")
    else:
        print("ACCESSIBILITY VALIDATION PASSED")

    stats = validation["stats"]
    print(f"\nStats: {stats['total_lines']} lines, {stats['total_headings']} headings, "
          f"{stats['images_found']} images, {stats['tables_found']} tables")
    sys.exit(0)


if __name__ == "__main__":
    main()
