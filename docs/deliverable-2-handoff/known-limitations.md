# Known Limitations

This document outlines the explicit boundaries of Version 1 and content types that may have degraded quality.

## V1 Scope Boundaries

### Document Size

| Constraint | Limit | Notes |
|------------|-------|-------|
| Page count | Under 40 pages | Optimal for 1-25 pages |
| File size | 10 MB max | API upload limit |

### Document Types

**Supported (V1):**
- Course syllabi
- Academic papers
- Administrative documents
- Schedules and calendars
- Posters and flyers
- Basic reports

**Planned for Future Phases:**
- Theses and dissertations (50+ pages)
- Textbooks
- Multi-volume documents

## V1 Scope Exclusions

The following content types are **explicitly out of scope for V1** and will be flagged in processing results:

### 1. Mathematical Content

**Limitation:** Complex LaTeX equations

**What this means:**
- Simple inline math is supported
- Display equations with complex notation require manual review
- Equation numbering and cross-references require verification

**Confidence impact:** Documents with significant math content receive appropriate confidence scores reflecting this scope.

**Review practice:** Mathematical content is part of standard QA. Equation-heavy documents benefit from specialized math remediation tools.

### 2. Advanced Tables

**Limitation:** Merged cells and complex data relationships

**What this means:**
- Simple tables convert well
- Tables with merged headers may lose structure
- Multi-level headers may not associate correctly
- Nested tables are not supported

**Confidence impact:** Complex tables are flagged with `needs_review: true`.

**Review practice:** Table structure verification, especially header associations, is part of standard QA.

### 3. Scientific Figures

**Limitation:** Complex accessible alternatives

**What this means:**
- Simple images get basic alt-text
- Complex charts may have incomplete data descriptions
- Multi-panel figures may not be fully described
- Interactive or 3D visualizations are not supported

**Confidence impact:** Complex figures receive lower confidence scores.

**Review practice:** Alt-text review for data-rich charts is part of standard QA.

### 4. Long Documents

**Limitation:** 50+ page optimization and cross-reference

**What this means:**
- Processing time increases significantly
- Cross-references may not resolve across many pages
- Table of contents linking may be incomplete
- Memory usage increases

**Confidence impact:** Processing time scales with document length.

**Review practice:** Long documents benefit from splitting. Extended document support is planned for future phases.

**Update:** Cross-page paragraph merging now automatically detects and merges paragraphs split across page boundaries using the paragraph_merge subagent.

### 5. OCR-Only Content

**Limitation:** Poor quality scanned documents

**What this means:**
- Scanned PDFs with low resolution struggle
- Handwritten content is not supported
- Faded or damaged originals produce errors
- Non-standard fonts may misread

**Confidence impact:** Low OCR quality results in many flagged corrections.

**Review practice:** Higher resolution scans or dedicated OCR preprocessing improves results.

## Additional Limitations

### Language Support

- **Primary:** English
- **Limited:** Other Latin-alphabet languages
- **Not supported:** Right-to-left languages, CJK characters

### Layout Types

| Layout | Support |
|--------|---------|
| Single column | Excellent |
| Two column | Good |
| Multi-column (3+) | Limited |
| Mixed layouts | Variable |
| Forms with fields | Not supported |

### Interactive Elements

Not supported:
- Form fields
- JavaScript interactions
- Embedded videos
- 3D models
- Animations

### Security

- Password-protected PDFs must be unlocked first
- Encrypted content cannot be processed
- DRM-protected documents are not supported

## Multi-Round Processing Limitations

The system supports iterative refinement through multi-round processing (`max_rounds` parameter, 1-5).

### Round Constraints

| Constraint | Value | Notes |
|------------|-------|-------|
| Maximum rounds | 5 | API enforces `max_rounds` between 1-5 |
| Default | 1 | Single-pass processing |
| Quality threshold | 0.85 | Stops if reached with no critical issues |

### CriticAgent Limitations (Rounds 2+)

The CriticAgent operates on merged markdown **without vision capabilities**:
- Cannot re-verify complex visual elements (charts, diagrams, scientific figures)
- Detects only text-based issues: structure, accessibility, content, formatting
- Visual analysis only occurs in Round 1 (page-based agents with images)

**Implication:** Documents with complex visual content should rely on Round 1 output. Multi-round is best for structural and accessibility refinement.

### Convergence Edge Cases

Processing stops when ANY condition is met:
- `MAX_ROUNDS_REACHED`: Hit the max_rounds limit
- `QUALITY_THRESHOLD_MET`: Quality ≥ 0.85 AND no critical issues
- `NO_IMPROVEMENT`: Current round quality ≤ previous round
- `NO_ISSUES_FOUND`: CriticAgent reports zero issues
- `CRITIC_MARKED_READY`: CriticAgent explicitly marks document ready

**Edge cases:**
- Documents plateauing at 0.82-0.84 quality may run to max_rounds
- CriticAgent accuracy varies; may mark ready prematurely
- Very small quality degradation triggers NO_IMPROVEMENT exit

### Cost Implications

| Round | Typical Cost | Cumulative |
|-------|--------------|------------|
| Round 1 | $0.10-0.30 | $0.10-0.30 |
| Each additional | +$0.05-0.15 | Scales linearly |
| 5 rounds total | - | $0.30-0.90 |

**Guidance:** Default to `max_rounds=1`. Use multi-round for:
- Documents with known accessibility issues
- Critical content requiring high confidence
- Large documents (50+ pages) that may need refinement

## What Gets Flagged

The system automatically flags content for review:

| Flag | Trigger | Appears In |
|------|---------|------------|
| `needs_review: true` | Confidence 0.5-0.8 | Ledger entries |
| Low document confidence | Many flagged items | Job status |
| Verification warning | Missing expected content | Verification phase |

## Handling Limitations

| Context | Guidance |
|---------|----------|
| Reviewers | Focus on low confidence and `needs_review: true` items; verify tables, figures, equations |
| Document Selection | Prefer native PDFs over scans, under 40 pages, simple layouts |

## Future Extensions

The original proposal outlined extensions to address these limitations:

| Extension | Addresses |
|-----------|-----------|
| Mathematical Content Processing | LaTeX, MathML, equation accessibility |
| Advanced Visual Content | Scientific figures, interactive charts |
| Performance & Scale Optimization | Long documents, cross-references |
| Production Automation | Batch processing, Canvas integration |

## Reporting Issues

When encountering limitations, keep the original PDF and ledger export, note what went wrong and the confidence scores. This information helps improve future versions.
