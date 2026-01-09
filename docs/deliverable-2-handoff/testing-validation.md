# Testing & Validation

This document covers how to test the system and validate processing results.

## Running the System

### Start Services

```bash
# Start all services (Docker)
make dev

# Verify health
make health

# View logs
make logs
```

### Quick Test

```bash
# Upload a test document via curl
curl -X POST http://localhost:8080/api/v1/documents/submit \
  -H "X-API-Key: dev-api-key" \
  -F "file=@test.pdf" \
  -F "skip_pii_scan=true"
```

## Test Suites

| Suite | Command | Duration | Scope |
|-------|---------|----------|-------|
| Unit | `make test-fast` | ~30s | Service classes, utilities, models |
| Integration | `make test-integration` | ~2min | Redis, S3, API endpoints |
| End-to-End | `make test-e2e` | ~5min | Full pipeline: upload → markdown |
| Coverage | `make coverage` | ~2min | Generates `htmlcov/` report |

## Validating Processing Results

### Manual Validation Checklist

| Area | Critical Checks |
|------|-----------------|
| Structure | Heading hierarchy correct (no level skips), page breaks don't interrupt sentences |
| Images | All figures have descriptive alt-text |
| Tables | Markdown formatted, headers identified, complex tables flagged |
| Text | No OCR artifacts, typography preserved, citations linked |
| Accessibility | Screen reader navigable, logical reading order |

### Automated Validation

The system performs verification automatically:

```bash
# Check verification results in API response
curl http://localhost:8080/api/v1/documents/{job_id}/phases \
  -H "X-API-Key: dev-api-key"
```

Verification phase output:
```json
{
  "verification": {
    "status": "completed",
    "total_pages": 12,
    "corrections_applied": 42,
    "corrections_failed": 0,
    "issues_found": 0,
    "all_pages_accurate": true
  }
}
```

## Debug Bundle

For detailed investigation, request a debug bundle:

```bash
# Submit with debug bundle enabled
curl -X POST http://localhost:8080/api/v1/documents/submit \
  -H "X-API-Key: dev-api-key" \
  -F "file=@test.pdf" \
  -F "skip_pii_scan=true" \
  -F "generate_debug_bundle=true"

# Download bundle after completion
curl http://localhost:8080/api/v1/documents/{job_id}/debug-bundle \
  -H "X-API-Key: dev-api-key" \
  -o debug.zip
```

Bundle contents:
```
debug_{job_id}.zip
├── README.md                    # Analysis guide
├── input/
│   ├── original.pdf
│   └── pages/*.png
├── phase_planning/
│   └── page_chain.json          # Planning traces
├── phase_execution/
│   ├── worker_*.json            # Worker agent traces
│   └── paragraph_*.json         # Paragraph agent traces
└── output/
    ├── manifest.json
    ├── observations.json
    └── final_markdown.md
```

## Test Document Selection

### For Deliverable 3 Pilot (30 documents)

Select documents representing:

| Category | Count | Examples |
|----------|-------|----------|
| Syllabi | 5 | Course outlines, schedules |
| Academic papers | 5 | Research papers, preprints |
| Administrative | 5 | Policies, procedures |
| Posters | 3 | Conference posters, infographics |
| Schedules | 3 | Calendars, timetables |
| Technical | 5 | STEM papers with equations |
| Mixed media | 4 | Documents with varied content |

### Selection Criteria

- Under 40 pages (V1 scope)
- Mix of departments and subjects
- Range from simple text to complex figures
- Include known edge cases

## Performance Validation

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Processing time | 2-8 min/doc | SSE timestamps, `time curl ...` |
| Cost | ~$0.20/doc | `GET /documents/{job_id}` → `llm_cost` |
| Resources | 0.4 vCPU, 2GB RAM | `docker stats` |

*Source: Version 1 Buildout, Deliverable 3 Acceptance Criteria*

## Common Issues

### Test Failures

| Issue | Cause | Solution |
|-------|-------|----------|
| Redis connection | Services not running | `make dev` |
| S3 timeout | LocalStack not ready | Wait, retry |
| Auth failure | Invalid API key | Check `.env` |

### Processing Failures

| Issue | Cause | Solution |
|-------|-------|----------|
| Timeout | Document too complex | Check page count, complexity |
| Low confidence | Poor input quality | Check PDF source |
| Missing elements | Extraction failed | Check Docling logs |

### Debugging

```bash
# View API logs
make logs-api

# Access container shell
make shell

# Redis CLI
make redis-cli
```

## Accessibility Testing

### Screen Reader Testing

*Source: Original Proposal, Manual Quality Review section*

| Platform | Browser | Screen Reader |
|----------|---------|---------------|
| Windows | Chrome | NVDA |
| Android | Chrome | TalkBack |
| macOS | Safari | VoiceOver |
| iOS | Safari | VoiceOver |

### Automated Accessibility Checks

```bash
# Run axe-core on output (example)
npx axe-cli http://localhost:8080/viewer --results=axe-results.json
```

Target: WCAG 2.1 AA compliance

*Source: Original Proposal, Success Metrics section*
