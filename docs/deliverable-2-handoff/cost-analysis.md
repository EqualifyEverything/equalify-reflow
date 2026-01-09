# Cost Analysis

This document details the LLM costs associated with document processing and provides guidance for cost estimation.

## Cost Tracking

The system tracks costs at multiple granularities:

### Per-Call Tracking

Every LLM invocation is logged with:
- Agent name
- Purpose (what the call accomplishes)
- Page number
- Input/output tokens
- Cost in cents
- Duration

### Accumulated Totals

Job-level aggregates:
- Total input tokens
- Total output tokens
- Total cost (cents and dollars)

## Cost Structure

### Model Tiers

The system uses AWS Bedrock Claude models with tiered pricing:

| Tier | Use Case | Input (per 1M tokens) | Output (per 1M tokens) |
|------|----------|----------------------|------------------------|
| Efficient | Most processing | ~$3.00 | ~$15.00 |
| Standard | Complex analysis | ~$8.00 | ~$24.00 |

*Prices are approximate and subject to AWS Bedrock pricing changes.*

### Cost by Phase

| Phase | Typical % of Total | Notes |
|-------|-------------------|-------|
| Planning | 15-25% | Sequential page analysis |
| Execution | 50-65% | Parallel job processing |
| Verification | 10-20% | Quality checks |
| Recovery | 0-15% | Only if issues found |

## Per-Document Estimates

### Target Metric

**~$0.20 per typical document**

*Source: Version 1 Buildout, Deliverable 3 Acceptance Criteria*

### Factors Affecting Cost

| Factor | Impact | Example |
|--------|--------|---------|
| Page count | Linear | 10 pages ≈ 2x cost of 5 pages |
| Image count | +$0.01-0.03/image | Alt-text generation |
| Table count | +$0.02-0.05/table | Transcription complexity |
| Text density | Minor | More tokens per page |
| Document complexity | Variable | Multi-column, equations |

### Cost Estimation Formula

```
estimated_cost = base_cost + (pages * per_page_cost) + (images * image_cost) + (tables * table_cost)

Where:
  base_cost ≈ $0.03 (planning overhead)
  per_page_cost ≈ $0.01-0.02
  image_cost ≈ $0.01-0.03
  table_cost ≈ $0.02-0.05
```

## Per-Page Estimates

For comparison with traditional remediation pricing:

| Document Type | Pages | Est. Cost | Per Page |
|---------------|-------|-----------|----------|
| Simple syllabus | 3 | $0.08 | $0.027 |
| Course schedule | 5 | $0.12 | $0.024 |
| Academic paper | 12 | $0.25 | $0.021 |
| Technical report | 20 | $0.45 | $0.023 |

**Average: ~$0.02-0.03 per page**

## Comparison with Manual Remediation

Traditional manual PDF remediation costs:

| Method | Cost per Page | Cost per Document (10 pages) |
|--------|--------------|------------------------------|
| Manual remediation | $1.50-3.60 | $15-36 |
| This system | $0.02-0.03 | $0.20-0.30 |
| **Savings** | **95-99%** | **$14.70-35.70** |

*Manual remediation estimates from industry sources.*

## Cost Visibility in API

### Job Status Response

```json
{
  "llm_cost": {
    "input_tokens": 125000,
    "output_tokens": 8500,
    "total_tokens": 133500,
    "estimated_cost_cents": 18.5,
    "estimated_cost_dollars": 0.185,
    "calls": [
      {
        "agent": "worker",
        "purpose": "alt_text_generation",
        "page": 1,
        "input_tokens": 5200,
        "output_tokens": 350,
        "cost_cents": 0.8,
        "timestamp": "2025-01-09T10:03:15Z",
        "duration_ms": 2340
      }
    ]
  }
}
```

## Cost Optimization

### Current Optimizations

1. **Pre-emptive context loading:** Reduces tool calls by 60-70%
2. **Parallel job execution:** Reduces wall-clock time (not cost)
3. **Confidence-based skipping:** Low-confidence situations skip expensive retries
4. **Efficient model tier:** Uses cost-optimized models where possible

### Enhancements Under Consideration

Pending pilot data analysis:
1. **Pattern caching:** Common icons and standard elements
2. **Batch planning:** Amortize costs across similar documents
3. **Adaptive model selection:** Route by task complexity
4. **Selective verification:** For documents exceeding 0.95 confidence

## Monitoring

### Metrics to Track

| Metric | Purpose |
|--------|---------|
| Cost per document | Primary cost indicator |
| Cost per page | Normalized comparison |
| Tokens per page | Efficiency measure |
| Cost by document type | Identify expensive categories |
| Cost trend over time | Track optimization impact |

### Alerting Thresholds

Consider alerts for:
- Single document > $1.00 (unusual complexity)
- Average cost > $0.50/document (may indicate issues)
- Token usage spikes (prompt injection or loops)

## Budget Planning

### Monthly Estimates

| Volume | Est. Monthly Cost |
|--------|-------------------|
| 100 documents | $20 |
| 500 documents | $100 |
| 1,000 documents | $200 |
| 5,000 documents | $1,000 |

*Based on ~$0.20 average per document*

### Pilot Phase (30 documents)

Estimated total: **~$6-10**

*Source: Version 1 Buildout, Deliverable 3 scope*
