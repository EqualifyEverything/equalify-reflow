# Deliverable 2: TLDR

## What It Does

- **Transforms PDFs into accessible markdown** using AI-powered document remediation
- **Generates alt-text, transcribes tables, fixes headings** automatically with confidence scoring
- **Tracks every change** in an auditable ledger with before/after/reasoning

## How It Works

```
PDF Upload --> PII Scan --> Docling Extract --> Planning --> Execution --> Verification --> Accessible Markdown
                              (PDF to MD)      (Analyze)   (AI Tools)     (Quality Check)
```

1. **Upload:** PDF submitted via API or Pipeline Viewer UI
2. **Extract:** Docling converts PDF to markdown with image/table placeholders
3. **Plan:** System analyzes structure, creates jobs for each page
4. **Execute:** Specialized AI tools run in parallel (alt-text, tables, headings, typography)
5. **Verify:** Quality checks ensure completeness; recovery phase fixes failures

## Key Metrics

| Metric | Target | Status |
|--------|--------|--------|
| Processing Time | 2-8 minutes | Ready for pilot validation |
| Cost per Document | ~$0.20 | ~$0.02-0.03/page |
| Automation Rate | 80% | Confidence-based thresholds |
| Manual Savings | 95-99% | vs $15-36/doc manual remediation |

## Limitations

- **Math/equations:** Complex LaTeX may not render properly
- **Advanced tables:** Merged cells and nested tables need manual review
- **Document size:** Optimized for <40 pages; 50+ may timeout
- **OCR quality:** Low-resolution scans produce more errors
- **Language:** English primary; RTL and CJK not supported

## Try It

```bash
make dev                                    # Start services
open http://localhost:8080/viewer           # Web UI (drag & drop PDF)
curl -X POST localhost:8080/api/v1/documents/submit -F "file=@doc.pdf" -H "X-API-Key: dev-api-key"
```

## Dive Deeper

| Topic | Document | Key Section |
|-------|----------|-------------|
| Architecture overview | [system-overview.md](./system-overview.md) | Core Concept: Tool-Based Intelligence |
| Pipeline phases | [pipeline-architecture.md](./pipeline-architecture.md) | Phase 2: Execution |
| API endpoints | [api-reference.md](./api-reference.md) | Submit Document, Stream Events |
| Demo interface | [demo-ui-guide.md](./demo-ui-guide.md) | Interface Overview |
| Confidence scoring | [confidence-and-ledger.md](./confidence-and-ledger.md) | Decision Thresholds |
| Cost breakdown | [cost-analysis.md](./cost-analysis.md) | Per-Document Estimates |
| Running tests | [testing-validation.md](./testing-validation.md) | Test Suites |
| What is not supported | [known-limitations.md](./known-limitations.md) | V1 Scope Boundaries |
| Terminology | [glossary.md](./glossary.md) | Core Concepts |
