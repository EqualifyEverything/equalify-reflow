# PDF Accessibility Skills System - Design Plan

## Overview

A Claude Code skill system that transforms PDFs into accessible markdown using:
- **Docling API** (running on localhost:5001) for extraction
- **Claude's native Read tool** for image analysis (it can read images!)
- **Well-organized temp files** for sharing context between agents
- **Hooks** for validation

No fancy MCP servers needed - just clever use of existing tools.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         /pdf-accessibility                               │
│                    (Main Slash Command Entry Point)                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    PDF Accessibility Orchestrator                        │
│                         (SKILL.md)                                       │
│                                                                          │
│  1. Create temp workspace                                                │
│  2. Call Docling API → extract markdown + JSON + images                  │
│  3. Analyze document metadata (scanned? native? tables? figures?)        │
│  4. Generate context files for each element                              │
│  5. Dispatch subagents for specific tasks                                │
│  6. Aggregate results → final accessible markdown                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│  Alt-Text Agent │      │  Table Agent    │      │  Heading Agent  │
│                 │      │                 │      │                 │
│ • Read image    │      │ • Read table    │      │ • Analyze       │
│   files directly│      │   image         │      │   structure     │
│ • Read context  │      │ • Verify/fix    │      │ • Fix hierarchy │
│   JSON          │      │   markdown      │      │                 │
│ • Write alt-text│      │                 │      │                 │
└─────────────────┘      └─────────────────┘      └─────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       Verification Agent                                 │
│                                                                          │
│  • Check all images have alt-text                                        │
│  • Verify heading hierarchy                                              │
│  • Validate table formatting                                             │
│  • Report issues or approve                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Temp Directory Structure

```
/tmp/pdf-access-{timestamp}-{random}/
├── input.pdf                          # Original PDF
│
├── docling/                           # Docling API output
│   ├── document.md                    # Markdown (with file paths, not base64)
│   ├── document.json                  # DoclingDocument (structured)
│   │
│   ├── pages/                         # FULL PAGE RENDERS (8.5x11)
│   │   ├── page_001.png               # Full visual of page 1
│   │   ├── page_002.png               # Full visual of page 2
│   │   └── ...
│   │
│   └── elements/                      # EXTRACTED FIGURES/TABLES
│       ├── picture_000.png            # Individual figure
│       ├── picture_001.png
│       ├── table_000.png              # Table crop (if available)
│       └── ...
│
├── context/                           # Context files for subagents
│   ├── metadata.json                  # Document-level metadata
│   ├── picture_000.json               # Context for picture 0
│   ├── picture_001.json               # Context for picture 1
│   ├── table_000.json                 # Context for table 0
│   └── ...
│
├── work/                              # Subagent working files
│   ├── alt_text_results.json          # Alt-text agent output
│   ├── table_results.json             # Table agent output
│   └── heading_results.json           # Heading agent output
│
└── results/
    ├── accessible.md                  # Final accessible markdown
    ├── ledger.json                    # All changes made
    └── report.json                    # Processing report
```

### Two Types of Images

1. **Page Images** (`pages/page_NNN.png`)
   - Full 8.5x11 visual render of each page
   - Source: `json_content.pages[N].image` (base64)
   - Use for: Context, layout understanding, table verification

2. **Element Images** (`elements/picture_NNN.png`)
   - Individual figures/charts/tables extracted by Docling
   - Source: Base64 in markdown `![](data:image/png;base64,...)`
   - Use for: Alt-text generation, detailed analysis

---

## Key Design Decisions

### 1. Image Analysis Without MCP

**Problem**: V5 uses AWS Bedrock vision. How do we do this in Claude Code?

**Solution**: Claude Code's `Read` tool can read images directly!

```markdown
# In alt-text agent prompt:
1. Read the image: Read("/tmp/pdf-access-xxx/docling/images/picture_0.png")
2. Read the context: Read("/tmp/pdf-access-xxx/context/picture_0.json")
3. Generate alt-text based on both
```

### 2. Docling API Integration

**API Call Pattern**:
```bash
curl -X POST http://localhost:5001/v1/convert/file \
  -F "files=@input.pdf" \
  -F "to_formats=md" \
  -F "to_formats=json" \
  -F "image_export_mode=referenced" \
  -F "do_ocr=true"
```

**Key Options**:
- `to_formats=["md", "json"]` - Get both markdown AND structured data
- `image_export_mode=referenced` - Export images as separate PNG files
- `do_ocr=true` - Run OCR on scanned content

### 3. Context Files for Subagents

Each element gets a context JSON file:

```json
// context/picture_0.json
{
  "element_type": "picture",
  "index": 0,
  "page": 1,
  "image_path": "/tmp/pdf-access-xxx/docling/images/picture_0.png",
  "label": "chart",
  "caption": "Figure 1: Revenue Growth",
  "surrounding_text": "As shown in the chart below, revenue grew...",
  "bounding_box": {"x": 100, "y": 200, "width": 400, "height": 300},
  "current_markdown": "![](images/picture_0.png)",
  "markdown_location": {"line_start": 45, "line_end": 45}
}
```

### 4. Scanned PDF Detection

Check `docling/document.json` for:
- `origin.mimetype` - PDF type
- Presence of text elements with low confidence
- OCR metadata

```json
// context/metadata.json
{
  "document_type": "scanned" | "native" | "hybrid",
  "total_pages": 5,
  "total_pictures": 12,
  "total_tables": 3,
  "ocr_applied": true,
  "ocr_confidence": 0.87,
  "headings": [...],
  "warnings": ["Page 3 has low OCR confidence"]
}
```

---

## File Structure

```
.claude/
├── commands/
│   └── pdf-accessibility.md           # Main entry point
│
├── skills/
│   └── pdf-accessibility/
│       ├── SKILL.md                    # Main orchestration
│       ├── DOCLING_GUIDE.md            # Docling API reference
│       ├── ALT_TEXT_GUIDE.md           # Alt-text best practices
│       └── scripts/
│           ├── call_docling.sh         # Docling API wrapper
│           ├── extract_context.py      # Parse DoclingDocument
│           └── aggregate_results.py    # Combine subagent outputs
│
├── agents/
│   ├── alt-text-writer.md              # Image description agent
│   ├── table-verifier.md               # Table validation agent
│   ├── heading-fixer.md                # Heading hierarchy agent
│   └── accessibility-checker.md        # Final verification agent
│
└── hooks/
    ├── validate_alt_text.py            # Check alt-text quality
    └── validate_accessibility.py       # Check final output
```

---

## Slash Command: `/pdf-accessibility`

```yaml
---
description: Transform a PDF into accessible markdown with alt-text, proper headings, and accessible tables
allowed-tools: Read, Write, Bash, Glob, Grep, Task, Edit, TodoWrite
---
```

**Triggers**:
- `/pdf-accessibility path/to/document.pdf`
- `/pdf-accessibility` (prompts for file)
- "Make this PDF accessible"
- "Add alt-text to this document"

---

## Skill: PDF Accessibility Orchestrator

### SKILL.md Structure

```markdown
---
name: pdf-accessibility
description: Transform PDFs into accessible markdown. Extracts images, generates
alt-text, fixes heading hierarchy, and validates tables. Use when processing
PDFs for accessibility or when user asks to make documents accessible.
allowed-tools: Read, Write, Bash, Glob, Grep, Task, Edit, TodoWrite
---

# PDF Accessibility Pipeline

## Overview
[Brief description]

## Pipeline Stages

### Stage 1: Setup Workspace
- Create temp directory
- Copy input PDF
- Initialize metadata

### Stage 2: Docling Extraction
- Call Docling API with image_export_mode=referenced
- Save markdown, JSON, and images
- Parse DoclingDocument for metadata

### Stage 3: Generate Context Files
- For each picture: create context JSON with surrounding text
- For each table: create context JSON with expected structure
- Generate document metadata (scanned/native, OCR confidence)

### Stage 4: Process Elements (Parallel)
- Launch alt-text-writer for images
- Launch table-verifier for tables
- Launch heading-fixer for structure

### Stage 5: Aggregate Results
- Combine all edits
- Apply to markdown
- Generate ledger

### Stage 6: Verify
- Run accessibility-checker
- Report issues or approve
```

---

## Subagents

### 1. Alt-Text Writer (`agents/alt-text-writer.md`)

```yaml
---
name: alt-text-writer
description: Generate semantic alt-text for images. Reads image files directly
and considers document context. MUST BE USED for image accessibility tasks.
tools: Read, Write
model: sonnet
---
```

**Capabilities**:
- Read PNG/JPG images directly with `Read` tool
- Read context JSON for surrounding text
- Generate alt-text following accessibility guidelines
- Classify as decorative/simple/complex
- Write results to work directory

**Workflow**:
```
1. Read context/picture_N.json
2. Read docling/images/picture_N.png (Claude can see images!)
3. Determine: decorative? informative? complex?
4. Generate alt-text (max 150 chars)
5. For complex: add extended description
6. Write to work/alt_text_results.json
```

### 2. Table Verifier (`agents/table-verifier.md`)

```yaml
---
name: table-verifier
description: Verify and fix markdown table formatting. Reads table images to
verify accuracy. MUST BE USED for table accessibility tasks.
tools: Read, Write, Edit
model: sonnet
---
```

**Capabilities**:
- Read table images
- Compare with Docling's markdown output
- Fix formatting issues
- Add captions if missing

### 3. Heading Fixer (`agents/heading-fixer.md`)

```yaml
---
name: heading-fixer
description: Analyze and fix document heading hierarchy. Ensures proper H1→H2→H3
structure without skipped levels. MUST BE USED for heading accessibility.
tools: Read, Write, Edit, Grep
model: haiku
---
```

**Capabilities**:
- Analyze heading structure
- Detect skipped levels (H1→H3)
- Propose fixes
- Maintain document outline

### 4. Accessibility Checker (`agents/accessibility-checker.md`)

```yaml
---
name: accessibility-checker
description: Final verification of accessible document. Checks all accessibility
criteria and reports issues. MUST BE USED before finalizing documents.
tools: Read, Grep
model: haiku
---
```

**Capabilities**:
- Verify all images have alt-text
- Check heading hierarchy
- Validate table formatting
- Generate accessibility report

---

## Hooks

### 1. Alt-Text Validation (`hooks/validate_alt_text.py`)

**Triggers**: After Write to `*alt_text*` files

**Checks**:
- Alt-text length (10-150 chars for simple, extended for complex)
- No "image of", "picture of" patterns
- Decorative images have empty alt
- Alt-text exists for all informative images

### 2. Accessibility Validation (`hooks/validate_accessibility.py`)

**Triggers**: After Write to `results/accessible.md`

**Checks**:
- No `![]()` (empty alt-text on informative images)
- No `<!-- image -->` placeholders remaining
- Heading hierarchy valid
- Tables have proper formatting

---

## Scripts

### 1. `call_docling.py` (Main extraction script)

```python
#!/usr/bin/env python3
"""
Call Docling API and extract all images to files.
Extracts both PAGE images (full 8.5x11 renders) and ELEMENT images (figures/tables).
"""

import json
import base64
import re
import os
import sys
from pathlib import Path

def call_docling(pdf_path: str, output_dir: str):
    """Call Docling API and extract everything."""
    import subprocess

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "pages").mkdir(exist_ok=True)
    (output / "elements").mkdir(exist_ok=True)

    # Call Docling API with embedded images
    result = subprocess.run([
        "curl", "-s", "-X", "POST", "http://localhost:5001/v1/convert/file",
        "-F", f"files=@{pdf_path}",
        "-F", "to_formats=md",
        "-F", "to_formats=json",
        "-F", "image_export_mode=embedded",  # Get base64 in markdown
        "-F", "do_ocr=true"
    ], capture_output=True, text=True)

    resp = json.loads(result.stdout)

    if resp.get('status') != 'success':
        print(f"Error: {resp.get('errors')}")
        sys.exit(1)

    doc = resp['document']
    json_content = doc.get('json_content', {})

    # 1. Extract PAGE IMAGES (full 8.5x11 renders)
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
                    filepath = output / "pages" / f"page_{int(page_num):03d}.{fmt}"
                    with open(filepath, 'wb') as f:
                        f.write(img_data)
                    print(f"  Extracted: {filepath} ({len(img_data):,} bytes)")

    # 2. Extract ELEMENT IMAGES from markdown (figures/tables)
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

        # Update markdown to use file path
        rel_path = f"elements/{filename}"
        new_md = new_md.replace(full_data, rel_path, 1)
        print(f"  Extracted: {filepath} ({len(img_data):,} bytes)")

    # 3. Save cleaned markdown (with file paths)
    with open(output / "document.md", 'w') as f:
        f.write(new_md)

    # 4. Save structured JSON
    with open(output / "document.json", 'w') as f:
        json.dump(json_content, f, indent=2)

    print(f"\nExtraction complete!")
    print(f"  Pages: {len(pages)}")
    print(f"  Elements: {len(matches)}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: call_docling.py <pdf_path> <output_dir>")
        sys.exit(1)
    call_docling(sys.argv[1], sys.argv[2])
```

### 2. `extract_context.py`

```python
#!/usr/bin/env python3
"""Extract context for each element from DoclingDocument."""

import json
import sys
from pathlib import Path

def extract_context(docling_dir: Path, context_dir: Path):
    """Generate context files for subagents."""

    with open(docling_dir / "document.json") as f:
        doc = json.load(f)

    # Document metadata
    metadata = {
        "name": doc.get("name", ""),
        "total_pictures": len(doc.get("pictures", [])),
        "total_tables": len(doc.get("tables", [])),
        "texts_count": len(doc.get("texts", [])),
    }

    with open(context_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # Picture contexts
    for i, pic in enumerate(doc.get("pictures", [])):
        context = {
            "element_type": "picture",
            "index": i,
            "label": pic.get("label", "picture"),
            "image_path": str(docling_dir / "images" / f"picture_{i}.png"),
            "captions": pic.get("captions", []),
            "prov": pic.get("prov", []),  # Provenance (page, bbox)
        }
        with open(context_dir / f"picture_{i}.json", "w") as f:
            json.dump(context, f, indent=2)

    # Table contexts
    for i, table in enumerate(doc.get("tables", [])):
        context = {
            "element_type": "table",
            "index": i,
            "image_path": str(docling_dir / "images" / f"table_{i}.png"),
            "data": table.get("data", {}),
            "prov": table.get("prov", []),
        }
        with open(context_dir / f"table_{i}.json", "w") as f:
            json.dump(context, f, indent=2)

if __name__ == "__main__":
    docling_dir = Path(sys.argv[1])
    context_dir = Path(sys.argv[2])
    context_dir.mkdir(exist_ok=True)
    extract_context(docling_dir, context_dir)
```

---

## Example Workflow

```
User: /pdf-accessibility project-docs/quarterly-report.pdf

Claude:
1. Creates /tmp/pdf-access-1704412345-a1b2/
2. Copies PDF to input.pdf
3. Calls Docling API → extracts to docling/
4. Runs extract_context.py → generates context/
5. Checks metadata: "Native PDF, 3 figures, 2 tables, 15 pages"

6. Launches subagents in parallel:
   - Task(alt-text-writer): "Process 3 figures in /tmp/pdf-access-xxx/"
   - Task(table-verifier): "Verify 2 tables in /tmp/pdf-access-xxx/"
   - Task(heading-fixer): "Fix headings in /tmp/pdf-access-xxx/"

7. Aggregates results from work/ directory
8. Applies all edits to docling/document.md
9. Runs accessibility-checker for final validation
10. Saves to results/accessible.md

Output:
✓ Processed quarterly-report.pdf
  - 3 figures: 2 informative (alt-text added), 1 decorative
  - 2 tables: verified correct
  - 2 heading fixes applied (H1→H3 → H1→H2→H3)
  - Output: results/accessible.md
```

---

## Implementation Order

1. **Phase 1: Core Infrastructure**
   - [ ] Create directory structure
   - [ ] Write `call_docling.sh` script
   - [ ] Write `extract_context.py` script
   - [ ] Test Docling integration

2. **Phase 2: Main Skill**
   - [ ] Create `SKILL.md` with orchestration logic
   - [ ] Create `/pdf-accessibility` slash command
   - [ ] Test basic flow

3. **Phase 3: Subagents**
   - [ ] Create `alt-text-writer.md`
   - [ ] Create `table-verifier.md`
   - [ ] Create `heading-fixer.md`
   - [ ] Create `accessibility-checker.md`

4. **Phase 4: Hooks**
   - [ ] Create `validate_alt_text.py`
   - [ ] Create `validate_accessibility.py`
   - [ ] Configure in settings.json

5. **Phase 5: Testing & Refinement**
   - [ ] Test with native PDFs
   - [ ] Test with scanned PDFs
   - [ ] Test with complex documents
   - [ ] Refine prompts based on results

---

## Verified Findings (from testing)

### Image Export Strategy

**CONFIRMED**: We need to use `image_export_mode=embedded` and extract images ourselves.

- `referenced` mode: Only provides URI references, no actual image data
- `embedded` mode: Base64 data URLs in markdown - we extract these to files

**Extraction Script** (tested and working):
```python
import json, base64, re, os

# Parse base64 images from markdown
pattern = r'!\[([^\]]*)\]\((data:image/(\w+);base64,([^)]+))\)'
matches = re.findall(pattern, markdown)

for i, (alt, full_data, fmt, b64) in enumerate(matches):
    img_data = base64.b64decode(b64)
    with open(f'images/image_{i:03d}.{fmt}', 'wb') as f:
        f.write(img_data)
    # Replace data URL with file path in markdown
```

### Claude Can Read Images

**CONFIRMED**: Claude Code's `Read` tool can directly read PNG files!

Test result: Reading `/tmp/docling-test/images/image_001.png` showed the image
(a logo with speech bubbles and "ABCD Dialogues" text).

This means subagents can:
1. `Read` the image file directly
2. See and analyze the image content
3. Generate appropriate alt-text

**No MCP server needed for vision!**

---

## Open Questions

1. **Parallel subagent execution**: Can we launch multiple Task calls in one message for true parallelism?

2. **Context window management**: For large documents with many images, how do we batch the work?

3. **Error handling**: What happens if Docling fails on certain pages?

---

## Next Steps

1. ~~Test Docling's `image_export_mode=referenced` with a real PDF~~ DONE
2. ~~Verify Claude Code can read extracted images with Read tool~~ DONE
3. Create the directory structure
4. Implement Phase 1 scripts
