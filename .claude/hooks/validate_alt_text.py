#!/usr/bin/env python3
"""
Hook: Validate alt-text quality after Write to alt_text_results.json

Triggers: PostToolUse on Write to *alt_text*

Checks:
- Alt-text length (10-150 chars for simple, extended for complex)
- No "image of", "picture of" patterns
- Decorative images have empty alt
- All informative images have alt-text
"""

import json
import sys
import re


def validate_alt_text(results_path: str) -> dict:
    """
    Validate alt-text results.

    Returns:
        dict with validation results
    """
    try:
        with open(results_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {
            "valid": False,
            "error": f"Could not read results: {e}"
        }

    issues = []
    warnings = []

    results = data.get("results", [])

    for result in results:
        index = result.get("index", "?")
        alt_text = result.get("alt_text", "")
        classification = result.get("classification", "unknown")

        # Check 1: Decorative images should have empty alt
        if classification == "decorative" and alt_text:
            issues.append({
                "index": index,
                "type": "decorative_with_alt",
                "message": f"Decorative image has alt-text: '{alt_text[:30]}...'"
            })

        # Check 2: Informative images should have alt-text
        if classification in ("simple", "complex") and not alt_text:
            issues.append({
                "index": index,
                "type": "missing_alt",
                "message": f"Informative image missing alt-text"
            })

        # Check 3: Alt-text length
        if alt_text:
            if len(alt_text) < 10 and classification != "decorative":
                warnings.append({
                    "index": index,
                    "type": "too_short",
                    "message": f"Alt-text very short ({len(alt_text)} chars): '{alt_text}'"
                })
            elif len(alt_text) > 150:
                warnings.append({
                    "index": index,
                    "type": "too_long",
                    "message": f"Alt-text over 150 chars ({len(alt_text)}). Consider shortening."
                })

        # Check 4: Bad patterns
        bad_patterns = [
            (r'^image\s+of\s+', "Starts with 'image of'"),
            (r'^picture\s+of\s+', "Starts with 'picture of'"),
            (r'^graphic\s+of\s+', "Starts with 'graphic of'"),
            (r'^photo\s+of\s+', "Starts with 'photo of'"),
            (r'^icon\s+of\s+', "Starts with 'icon of'"),
        ]

        for pattern, message in bad_patterns:
            if re.match(pattern, alt_text.lower()):
                issues.append({
                    "index": index,
                    "type": "bad_pattern",
                    "message": f"{message}: '{alt_text[:50]}...'"
                })
                break

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "total_checked": len(results),
        "summary": {
            "critical_issues": len(issues),
            "warnings": len(warnings)
        }
    }


def main():
    """Main entry point for hook."""
    if len(sys.argv) < 2:
        print("Usage: validate_alt_text.py <results_path>")
        sys.exit(1)

    results_path = sys.argv[1]
    validation = validate_alt_text(results_path)

    if not validation["valid"]:
        print("ALT-TEXT VALIDATION FAILED")
        print("=" * 40)
        for issue in validation["issues"]:
            print(f"  [{issue['index']}] {issue['type']}: {issue['message']}")
        sys.exit(1)

    if validation["warnings"]:
        print("ALT-TEXT VALIDATION PASSED (with warnings)")
        print("=" * 40)
        for warning in validation["warnings"]:
            print(f"  [{warning['index']}] {warning['type']}: {warning['message']}")
    else:
        print("ALT-TEXT VALIDATION PASSED")

    print(f"\nChecked {validation['total_checked']} images")
    sys.exit(0)


if __name__ == "__main__":
    main()
