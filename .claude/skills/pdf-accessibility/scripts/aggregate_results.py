#!/usr/bin/env python3
"""
Aggregate subagent results and apply edits to create final accessible document.

Usage:
    python aggregate_results.py <workspace_dir>

Example:
    python aggregate_results.py /tmp/pdf-access-123
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime


def load_json_safe(path: Path) -> dict:
    """Load JSON file, return empty dict if not found."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def aggregate_results(workspace: str) -> dict:
    """
    Aggregate all subagent results and create final document.

    Args:
        workspace: Path to the workspace directory

    Returns:
        dict with aggregation summary
    """
    ws = Path(workspace)

    # Load source markdown
    md_path = ws / "docling" / "document.md"
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        sys.exit(1)

    with open(md_path) as f:
        content = f.read()
        lines = content.split('\n')

    # Load subagent results
    alt_text_results = load_json_safe(ws / "work" / "alt_text_results.json")
    table_results = load_json_safe(ws / "work" / "table_results.json")
    heading_results = load_json_safe(ws / "work" / "heading_results.json")

    ledger = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "source": str(md_path),
        "changes": []
    }

    # Apply alt-text changes
    for result in alt_text_results.get("results", []):
        index = result.get("index", 0)
        alt_text = result.get("alt_text", "")
        classification = result.get("classification", "simple")

        # Find image in markdown
        # Pattern: ![anything](elements/picture_NNN.ext)
        pattern = rf'!\[[^\]]*\]\((elements/picture_{index:03d}\.[^)]+)\)'

        def replacer(match):
            img_path = match.group(1)
            return f'![{alt_text}]({img_path})'

        new_content = re.sub(pattern, replacer, content)

        if new_content != content:
            ledger["changes"].append({
                "type": "alt_text",
                "element": f"picture_{index:03d}",
                "classification": classification,
                "new_value": alt_text[:100] + "..." if len(alt_text) > 100 else alt_text
            })
            content = new_content

    # Apply heading changes
    for fix in heading_results.get("results", {}).get("fixes", []):
        line_num = fix.get("line", 0)
        old_heading = fix.get("old", "")
        new_heading = fix.get("new", "")

        if line_num > 0 and line_num <= len(lines):
            # Verify the line matches expected content
            if lines[line_num - 1].strip() == old_heading.strip():
                lines[line_num - 1] = new_heading
                ledger["changes"].append({
                    "type": "heading",
                    "line": line_num,
                    "old": old_heading,
                    "new": new_heading
                })

    # Apply table fixes
    for result in table_results.get("results", []):
        if result.get("status") == "needs_fix":
            fix = result.get("proposed_fix", {})
            line_start = fix.get("line_start", 0)
            line_end = fix.get("line_end", 0)
            new_content_block = fix.get("new_content", "")

            if line_start > 0 and new_content_block:
                # Replace lines
                new_lines = new_content_block.split('\n')
                lines[line_start - 1:line_end] = new_lines
                ledger["changes"].append({
                    "type": "table",
                    "line_start": line_start,
                    "line_end": line_end,
                    "reason": "Fixed table formatting"
                })

    # Reconstruct content from lines (in case heading/table edits modified lines)
    # First, re-apply alt-text to the reconstructed content
    final_content = '\n'.join(lines)

    # Ensure results directory exists
    results_dir = ws / "results"
    results_dir.mkdir(exist_ok=True)

    # Write final document
    output_path = results_dir / "accessible.md"
    with open(output_path, 'w') as f:
        f.write(final_content)
    print(f"Created: {output_path}")

    # Write ledger
    ledger_path = results_dir / "ledger.json"
    with open(ledger_path, 'w') as f:
        json.dump(ledger, f, indent=2)
    print(f"Created: {ledger_path}")

    # Summary
    summary = {
        "output_file": str(output_path),
        "total_changes": len(ledger["changes"]),
        "changes_by_type": {}
    }

    for change in ledger["changes"]:
        change_type = change["type"]
        summary["changes_by_type"][change_type] = \
            summary["changes_by_type"].get(change_type, 0) + 1

    print(f"\n=== Aggregation Complete ===")
    print(f"  Total changes: {summary['total_changes']}")
    for change_type, count in summary["changes_by_type"].items():
        print(f"    {change_type}: {count}")

    return summary


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: aggregate_results.py <workspace_dir>")
        print("Example: python aggregate_results.py /tmp/pdf-access-123")
        sys.exit(1)

    workspace = sys.argv[1]

    if not Path(workspace).exists():
        print(f"Error: Workspace not found: {workspace}")
        sys.exit(1)

    aggregate_results(workspace)
