---
description: Transform a PDF into accessible markdown with alt-text, proper headings, and accessible tables
argument-hint: <PDF_PATH>
---

# PDF Accessibility Converter

Transform a PDF into accessible markdown with alt-text, proper headings, and accessible tables.

**All subagents run with model: haiku for cost efficiency.**

## Arguments

`<PDF_PATH>` - Path to the PDF file to convert

Example: `/pdf project-docs/pdfs/document.pdf`

---

## Process

### 1. Setup Workspace

```bash
WORKSPACE="/tmp/pdf-access-$(date +%s)-$(openssl rand -hex 4)"
mkdir -p "$WORKSPACE"/{docling,context,work,results}
cp "$PDF_PATH" "$WORKSPACE/input.pdf"
```

### 2. Extract with Docling

**For tagged PDFs (has embedded text):**
```bash
uv run python .claude/skills/pdf-accessibility/scripts/call_docling.py \
  "$WORKSPACE/input.pdf" "$WORKSPACE/docling"
```

**For untagged/scanned PDFs (Haiku will do OCR):**
```bash
uv run python .claude/skills/pdf-accessibility/scripts/call_docling.py \
  "$WORKSPACE/input.pdf" "$WORKSPACE/docling" --no-ocr
```

### 3. Generate Context Files

```bash
uv run python .claude/skills/pdf-accessibility/scripts/extract_context.py \
  "$WORKSPACE/docling" "$WORKSPACE/context"
```

### 4. Haiku OCR (untagged PDFs only)

If `extraction_stats.json` shows `"needs_haiku_ocr": true`:

**Launch ONE ocr-extractor subagent PER PAGE (in parallel):**

```
# Read metadata.json to get total_pages, then for each page N:
Task(subagent_type="ocr-extractor", model="haiku"):
  "OCR page N of WORKSPACE. Read metadata first, then page image.
   Transcribe EXACTLY what you see. Write to work/ocr_page_NNN.md"
```

After all pages complete, combine:
```bash
uv run python .claude/skills/pdf-accessibility/scripts/combine_ocr_pages.py "$WORKSPACE"
cp "$WORKSPACE/work/ocr_results.md" "$WORKSPACE/docling/document.md"
```

### 5. Clean Text Flow

```bash
uv run python .claude/skills/pdf-accessibility/scripts/clean_page_breaks.py \
  "$WORKSPACE/docling/document.md"
```

For complex multi-page documents, also run text-flow-fixer subagent.

### 6. Process Elements (Parallel)

Launch these subagents in parallel with model="haiku":
- `alt-text-writer` - Generate alt-text for images
- `table-verifier` - Verify table formatting
- `heading-fixer` - Fix heading hierarchy

### 7. Aggregate Results

```bash
uv run python .claude/skills/pdf-accessibility/scripts/aggregate_results.py "$WORKSPACE"
```

### 8. Verify Accessibility

Run `accessibility-checker` subagent with model="haiku" for final validation.

### 9. Open Results for Inspection

```bash
open "$WORKSPACE"
```

This opens the temp directory in Finder so you can inspect:
- `docling/` - Extracted markdown, JSON, and images
- `context/` - Context files for each element
- `work/` - Subagent outputs
- `results/` - Final accessible markdown and reports

---

## Output

Report:
- Number of images processed with alt-text
- Number of tables verified
- Number of heading fixes applied
- Text flow issues fixed
- Path to final accessible document
- Any remaining accessibility issues

## Reference

See `.claude/skills/pdf-accessibility/SKILL.md` for detailed documentation.
