# System Overview

## What This System Does

The AI PDF Converter transforms PDF documents into accessible markdown. It uses artificial intelligence to perform document remediation tasks that would traditionally require manual human effort:

- **Generate alt-text** for images and figures
- **Transcribe tables** into accessible markdown format
- **Correct heading hierarchy** for proper document structure
- **Fix typography semantics** (emphasis, lists, citations)
- **Remove page artifacts** (page breaks, orphaned numbers)
- **Merge split paragraphs** across page boundaries

## Core Concept: Tool-Based Intelligence

The system uses an architecture inspired by how modern AI coding assistants work. Rather than having separate AI systems for each task, the system has **agents** that can invoke **specialized tools** based on what the document needs.

```
┌─────────────────────────────────────────────────────────────┐
│                        PDF Document                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Planning Phase                           │
│  Analyze document structure, identify what needs fixing      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Processing Phase                          │
│                                                              │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│   │ Alt-Text    │  │   Table     │  │  Heading    │        │
│   │   Tool      │  │    Tool     │  │    Tool     │        │
│   └─────────────┘  └─────────────┘  └─────────────┘        │
│                                                              │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│   │ Typography  │  │  Citation   │  │  Footnote   │        │
│   │   Tool      │  │    Tool     │  │    Tool     │        │
│   └─────────────┘  └─────────────┘  └─────────────┘        │
│                                                              │
│   Agent selects and invokes tools based on document needs    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Verification Phase                         │
│  Check all figures have alt-text, tables transcribed, etc.  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Accessible Markdown                        │
└─────────────────────────────────────────────────────────────┘
```

**Diagram description:** A vertical flowchart showing the document processing pipeline. A PDF Document enters the Planning Phase (which analyzes structure and identifies fixes needed), then flows to the Processing Phase. The Processing Phase contains six specialized tools arranged in two rows: Alt-Text Tool, Table Tool, and Heading Tool in the first row; Typography Tool, Citation Tool, and Footnote Tool in the second row. The agent selects tools based on document needs. After processing, the flow continues to the Verification Phase (which checks completeness), and finally outputs Accessible Markdown.

## Why This Architecture Works

### Efficiency Through Specialization

Each tool encapsulates expert knowledge for a specific remediation task. When an agent encounters a figure that needs alt-text, it invokes the alt-text tool with the image and surrounding context. The tool returns a result with a confidence score, and the agent decides whether to apply it.

### Compute Scales With Complexity

Simple documents (text-only syllabi) require fewer tool invocations than complex documents (research papers with figures and tables). The system automatically allocates more compute to documents that need it.

### Confidence-Based Decision Making

Every edit the system proposes includes a confidence score:

| Confidence | Action |
|------------|--------|
| ≥ 0.8 (High) | Apply automatically |
| 0.5 - 0.8 (Medium) | Apply but flag for review |
| < 0.5 (Low) | Skip and log for manual review |

This ensures high-quality automated remediation while flagging uncertain cases for human oversight.

### Transparent Reasoning

Every change is logged with:
- **Before:** Original text
- **After:** Modified text
- **Reasoning:** Why the change was made
- **Confidence:** How certain the system is

This creates a complete audit trail that educators can review.

## Processing Pipeline Overview

### Phase 1: Planning

The system analyzes the document structure:
- Identifies page types (title page, table of contents, content, references)
- Detects figures, tables, and headings
- Creates a processing plan with specific jobs for each page

### Phase 2: Execution

Jobs run in parallel across pages:
- **Structure jobs:** Fix headings, remove artifacts
- **Content jobs:** Generate alt-text, transcribe tables
- **Paragraph jobs:** Fix typography, citations, footnotes

### Phase 2.5: Cross-Page Merge

Handles content split across page boundaries (incomplete sentences, hyphenated words).

### Phase 3: Verification

Quality checks: all figures have alt-text, tables transcribed, heading hierarchy correct.

### Phase 4: Recovery (If Needed)

Attempts targeted fixes for issues found during verification.

## Key Metrics

| Metric | Target | Purpose |
|--------|--------|---------|
| Processing Time | 2-8 minutes | Practical for faculty workflow |
| Cost per Document | ~$0.20 | 95-99% cheaper than manual remediation |
| Automation Rate | 80% | Percentage handled without manual intervention |
| Confidence Threshold | 0.8 | Minimum for automatic application |

*Source: Version 1 Buildout, Deliverable 3 Acceptance Criteria*

## Security Model

Course materials only—no student records or PII. Documents are scanned with Microsoft Presidio before AI processing; flagged documents require approval. All transfers use TLS; storage is encrypted; originals deleted after processing.

*Source: Version 1 Buildout, Security section*

## What Success Looks Like

A successfully processed document:

1. **Maintains semantic structure** - Headings, lists, and sections preserve meaning
2. **Provides accessible alternatives** - Images have alt-text, tables are properly marked up
3. **Reads correctly with assistive technology** - Screen readers can navigate the content
4. **Includes confidence metadata** - Reviewers know which sections need attention
5. **Provides complete audit trail** - Every change is documented with reasoning
