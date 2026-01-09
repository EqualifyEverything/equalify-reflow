# PDF Accessibility Pipeline

Transform PDFs into accessible markdown with proper alt-text, heading hierarchy, and table formatting.

## When to Use

Use this skill when:
- Processing PDFs for accessibility compliance
- Adding alt-text to document images
- Fixing heading hierarchy issues
- Verifying table accessibility
- User asks to "make this PDF accessible"

## Overview

This skill orchestrates a multi-stage pipeline:
1. **Extract**: Call Docling API to convert PDF to markdown + images
2. **Analyze**: Generate context files for each element
3. **Process**: Dispatch subagents for specific accessibility tasks
4. **Verify**: Run accessibility checks on final output

## Pipeline Stages

### Stage 1: Setup Workspace

Create a temp directory with the standard structure:

```bash
# Create workspace
WORKSPACE="/tmp/pdf-access-$(date +%s)-$(openssl rand -hex 4)"
mkdir -p "$WORKSPACE"/{docling,context,work,results}
cp "$PDF_PATH" "$WORKSPACE/input.pdf"
```

The directory structure will be:
```
/tmp/pdf-access-{timestamp}-{random}/
├── input.pdf              # Original PDF
├── docling/               # Docling outputs
│   ├── document.md        # Markdown with file paths
│   ├── document.json      # Structured DoclingDocument
│   ├── pages/             # Full page renders (8.5x11)
│   └── elements/          # Individual figures/tables
├── context/               # Context files for subagents
│   ├── metadata.json      # Document-level info
│   ├── picture_000.json   # Per-image context
│   └── headings.json      # Heading analysis
├── work/                  # Subagent outputs
└── results/               # Final outputs
```

### Stage 2: Docling Extraction

First, determine if the PDF is tagged (has embedded text) or untagged (scanned/image-based):

**For tagged PDFs (normal):**
```bash
uv run python .claude/skills/pdf-accessibility/scripts/call_docling.py \
  "$WORKSPACE/input.pdf" \
  "$WORKSPACE/docling"
```

**For untagged/scanned PDFs (skip Docling OCR, Haiku will do it):**
```bash
uv run python .claude/skills/pdf-accessibility/scripts/call_docling.py \
  "$WORKSPACE/input.pdf" \
  "$WORKSPACE/docling" \
  --no-ocr
```

This will:
- Call Docling API (localhost:5001) with `image_export_mode=embedded`
- Extract page images to `docling/pages/page_NNN.png` (always)
- Extract element images to `docling/elements/picture_NNN.png`
- Save cleaned markdown to `docling/document.md`
- Save structured JSON to `docling/document.json`

**Check the output**: Read `$WORKSPACE/docling/extraction_stats.json` to see:
- `needs_haiku_ocr`: true if OCR was skipped and Haiku should process page images

### Stage 3: Generate Context

Run the context extraction script:

```bash
uv run python .claude/skills/pdf-accessibility/scripts/extract_context.py \
  "$WORKSPACE/docling" \
  "$WORKSPACE/context"
```

Then read the metadata to understand the document:

```python
# Key file: context/metadata.json
{
  "document_type": "native" | "scanned" | "hybrid",
  "total_pages": N,
  "total_pictures": N,
  "total_tables": N,
  "total_headings": N
}
```

### Stage 4: Haiku OCR (for untagged PDFs only)

If `extraction_stats.json` shows `"needs_haiku_ocr": true`, run per-page OCR.

**IMPORTANT: Launch ONE subagent PER PAGE for accurate transcription.**

First, read `context/metadata.json` to get the total page count, then launch parallel OCR tasks:

```
# For each page N (1 to total_pages), launch in parallel:
Task(subagent_type="ocr-extractor", model="haiku"):
  "OCR page {N} of {WORKSPACE}.

   1. First read {WORKSPACE}/context/metadata.json for document context
   2. Then read {WORKSPACE}/docling/pages/page_{N:03d}.png
   3. Transcribe EXACTLY what you see - no fabrication
   4. Mark unclear text as [illegible] or [?word?]
   5. Write to {WORKSPACE}/work/ocr_page_{N:03d}.md
   6. Write metadata to {WORKSPACE}/work/ocr_page_{N:03d}.json"
```

After ALL page OCR tasks complete, combine results:

```bash
uv run python .claude/skills/pdf-accessibility/scripts/combine_ocr_pages.py "$WORKSPACE"
cp "$WORKSPACE/work/ocr_results.md" "$WORKSPACE/docling/document.md"
```

**Why per-page?**
- Prevents hallucination from context overload
- Each agent focuses on ONE page only
- Parallel processing for speed
- Easier to identify problematic pages

**Skip this stage for tagged PDFs** - they already have text from Docling.

### Stage 5: Clean Text Flow (all PDFs)

First, run the deterministic page-break cleaner:

```bash
uv run python .claude/skills/pdf-accessibility/scripts/clean_page_breaks.py \
  "$WORKSPACE/docling/document.md"
```

Then for complex multi-page documents, run the text-flow-fixer:

```
Task(subagent_type="text-flow-fixer", model="haiku"):
  "Fix text flow issues in {WORKSPACE}.
   Read docling/document.md and page images.
   Fix dehyphenation, footnotes, page artifacts.
   Write results to work/text_flow_results.json"
```

### Stage 6: Process Elements

Launch subagents in parallel for each task type (all use haiku model):

#### For Images (alt-text generation):
```
Task(subagent_type="alt-text-writer", model="haiku"):
  "Generate alt-text for images in {WORKSPACE}.
   Read context/picture_*.json for each image.
   Read the actual images in docling/elements/.
   Write results to work/alt_text_results.json"
```

#### For Tables (verification):
```
Task(subagent_type="table-verifier", model="haiku"):
  "Verify tables in {WORKSPACE}.
   Read context/table_*.json for each table.
   Compare with page images in docling/pages/.
   Write results to work/table_results.json"
```

#### For Headings (hierarchy fixes):
```
Task(subagent_type="heading-fixer", model="haiku"):
  "Fix heading hierarchy in {WORKSPACE}.
   Read context/headings.json for current structure.
   Propose fixes for skipped levels.
   Write results to work/heading_results.json"
```

### Stage 7: Aggregate Results

Combine all subagent outputs:

1. Read `work/alt_text_results.json`
2. Read `work/table_results.json`
3. Read `work/heading_results.json`
4. Apply all edits to `docling/document.md`
5. Save to `results/accessible.md`

Create a ledger of all changes:
```json
// results/ledger.json
{
  "changes": [
    {"type": "alt_text", "element": "picture_000", "old": "", "new": "Chart showing..."},
    {"type": "heading", "line": 45, "old": "### Section", "new": "## Section"}
  ]
}
```

### Stage 8: Verify

Run final accessibility checks:

```
Task(subagent_type="accessibility-checker"):
  "Verify accessibility of {WORKSPACE}/results/accessible.md.
   Check: all images have alt-text, heading hierarchy is valid, tables are accessible.
   Report any remaining issues."
```

### Stage 9: Open for Inspection

Open the workspace directory so the user can inspect all outputs:

```bash
open "$WORKSPACE"
```

This reveals:
- `docling/` - Extracted content and images
- `context/` - Element context files
- `work/` - Subagent outputs
- `results/` - Final accessible document

## Key Files Reference

### Docling Output
- `docling/document.md` - Markdown with `![Image](elements/picture_NNN.png)` references
- `docling/document.json` - DoclingDocument with full structure
- `docling/pages/page_NNN.png` - Full page renders (use for visual context)
- `docling/elements/picture_NNN.png` - Individual figures (use for alt-text)

### Context Files
- `context/metadata.json` - Document summary
- `context/picture_NNN.json` - Per-image context with surrounding text
- `context/table_NNN.json` - Per-table context with structure info
- `context/headings.json` - Heading hierarchy analysis

### Work Files (Subagent Outputs)
- `work/alt_text_results.json` - Generated alt-text for each image
- `work/table_results.json` - Table verification results
- `work/heading_results.json` - Heading fix proposals

### Results
- `results/accessible.md` - Final accessible document
- `results/ledger.json` - All changes made
- `results/report.json` - Accessibility report

## Important Notes

### Image Analysis
Claude Code's `Read` tool can read PNG files directly. No MCP server needed!
- Read element images: `Read("$WORKSPACE/docling/elements/picture_000.png")`
- Read page images: `Read("$WORKSPACE/docling/pages/page_001.png")`

### Two Types of Images
1. **Page Images** (`pages/page_NNN.png`) - Full 8.5x11 renders for layout context
2. **Element Images** (`elements/picture_NNN.png`) - Individual figures for alt-text

### Alt-Text Guidelines
- Max 150 characters for simple descriptions
- Use extended descriptions for complex charts/diagrams
- Mark decorative images with empty alt: `![](image.png)`
- Never start with "image of" or "picture of"

### Heading Hierarchy
- Never skip levels (H1 -> H3 is wrong)
- Each document should have one H1
- Subheadings should be H2, then H3, etc.
