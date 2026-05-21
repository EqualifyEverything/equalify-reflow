#!/usr/bin/env python3
"""
Extract fillable PDF form fields as structured JSON.

Usage:
  python extract_pdf_form.py input.pdf --pretty
  python extract_pdf_form.py input.pdf -o fields.json --pretty
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.generic import IndirectObject


MAX_DEPTH = 8


def pdf_value_to_json(value: Any, depth: int = 0, seen: set[tuple[int, int]] | None = None) -> Any:
    """Convert pypdf/PDF object values into JSON-safe values, resolving indirect objects."""
    if seen is None:
        seen = set()

    if depth > MAX_DEPTH:
        return "[Max recursion depth reached]"

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()

    if isinstance(value, IndirectObject):
        ref = (value.idnum, value.generation)
        if ref in seen:
            return f"[Circular indirect reference: {value.idnum} {value.generation} R]"

        seen.add(ref)

        try:
            resolved = value.get_object()
        except Exception as exc:
            return {
                "_indirect_ref": f"{value.idnum} {value.generation} R",
                "_error": str(exc),
            }

        return {
            "_indirect_ref": f"{value.idnum} {value.generation} R",
            "_resolved": pdf_value_to_json(resolved, depth + 1, seen),
        }

    if isinstance(value, (list, tuple)):
        return [pdf_value_to_json(v, depth + 1, seen.copy()) for v in value]

    if isinstance(value, dict):
        return {
            str(k): pdf_value_to_json(v, depth + 1, seen.copy())
            for k, v in value.items()
        }

    if hasattr(value, "items"):
        return {
            str(k): pdf_value_to_json(v, depth + 1, seen.copy())
            for k, v in value.items()
        }

    return str(value)


def extract_form_fields(pdf_path: Path) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    fields = reader.get_fields() or {}

    return {
        "source_file": str(pdf_path),
        "field_count": len(fields),
        "fields": [
            {
                "name": name,
                "value": pdf_value_to_json(field.get("/V")),
                "default_value": pdf_value_to_json(field.get("/DV")),
                "type": pdf_value_to_json(field.get("/FT")),
                "alternate_name": pdf_value_to_json(field.get("/TU")),
                "mapping_name": pdf_value_to_json(field.get("/TM")),
                "flags": pdf_value_to_json(field.get("/Ff")),
                "raw": {
                    str(k): pdf_value_to_json(v)
                    for k, v in field.items()
                    if str(k) not in {"/Kids", "/Parent"}
                },
            }
            for name, field in fields.items()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract fillable PDF form fields as structured JSON."
    )
    parser.add_argument("pdf", help="Path to the input PDF")
    parser.add_argument("-o", "--output", help="Write JSON to this file instead of stdout")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")

    args = parser.parse_args()
    pdf_path = Path(args.pdf)

    if not pdf_path.is_file():
        print(f"Error: file not found: {pdf_path}", file=sys.stderr)
        return 1

    try:
        data = extract_form_fields(pdf_path)
    except Exception as exc:
        print(f"Error reading PDF: {exc}", file=sys.stderr)
        return 1

    json_text = json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None)

    if args.output:
        Path(args.output).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())