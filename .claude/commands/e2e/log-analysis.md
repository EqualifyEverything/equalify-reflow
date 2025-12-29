# E2E Log Analysis

Analyze Docker logs from recent PDF processing for insights and debugging.

## Arguments

`$ARGUMENTS`

- `--since=Xm`: Time window (default: 10m) - e.g., `--since=5m`, `--since=30m`
- `--job=UUID`: Filter logs for specific job ID
- `--focus=X`: Analysis focus - `errors`, `performance`, `agents`, `phases`, `all` (default: all)
- `--tail=N`: Number of lines to analyze (default: 500)

---

## Log Collection

```bash
# Parse arguments
SINCE="${since:-10m}"
TAIL="${tail:-500}"
JOB_FILTER="${job:-}"

# Collect logs
docker logs equalify-pdf-api-gateway --since $SINCE 2>&1 | tail -$TAIL | tee /tmp/e2e_logs.txt
```

If `--job=UUID` specified, filter:
```bash
grep "$JOB_FILTER" /tmp/e2e_logs.txt | tee /tmp/e2e_logs_filtered.txt
```

---

## Analysis Tasks

### 1. Phase Detection

Search for phase transitions and timing:

```bash
grep -E "Phase [123]|Structure Analysis|Guided Transcription|Specialized Agents|Assembly" /tmp/e2e_logs.txt
```

**Report format:**
```
PHASE TIMELINE
──────────────
[10:15:30] Phase 1: Structure Analysis started
[10:16:15] Phase 1: Complete (45s)
[10:16:16] Phase 2: Guided Transcription started
[10:17:26] Phase 2: Complete (1m 10s)
[10:17:27] Phase 3: Specialized Agents started
[10:17:47] Phase 3: Complete (20s)
```

### 2. Agent Activity

Search for agent execution:

```bash
grep -E "Agent|ExtractionAgent|StructureFixAgent|FiguresAgent|TablesAgent|TypographyAgent" /tmp/e2e_logs.txt
```

**Report format:**
```
AGENT ACTIVITY
──────────────
ExtractionAgent: 3 invocations, 0 errors
StructureFixAgent: 1 invocation, 0 errors
FiguresAgent: 2 invocations, 1 observation generated
TablesAgent: 0 invocations (no tables detected)
TypographyAgent: 1 invocation, 2 observations generated
```

### 3. Bedrock/LLM Calls

Search for AWS Bedrock activity:

```bash
grep -E "Bedrock|Claude|tokens|latency|anthropic" /tmp/e2e_logs.txt
```

**Report format:**
```
LLM ACTIVITY
────────────
Bedrock calls: 5 total
  - Phase 1: 1 call (2,450 tokens, 3.2s latency)
  - Phase 2: 1 call (8,100 tokens, 12.5s latency)
  - Phase 3: 3 calls (4,200 tokens avg, 2.1s latency avg)
Total tokens: 18,234
Estimated cost: $0.015
```

### 4. Error Detection

Search for errors and warnings:

```bash
grep -iE "error|exception|failed|timeout|warning|traceback|500|503" /tmp/e2e_logs.txt
```

**Report format:**
```
ERRORS & WARNINGS
─────────────────
[!] 10:16:42 WARNING: Retry attempt 1 for Bedrock call
[X] 10:17:30 ERROR: Image extraction failed for page 5
    -> Fallback to OCR successful
[!] 10:17:45 WARNING: Low confidence (0.72) for table on page 3
```

### 5. Queue & Job State

Search for job state transitions:

```bash
grep -E "job_id|status|queue|pii_scan|processing|needs_review|completed" /tmp/e2e_logs.txt
```

**Report format:**
```
JOB STATE FLOW
──────────────
[10:15:00] Job created: 550e8400-...
[10:15:00] Queued: pii_scan
[10:15:05] Status: pii_scanning -> awaiting_approval
[10:15:12] Status: awaiting_approval -> processing
[10:17:50] Status: processing -> needs_review
[10:18:02] Status: needs_review -> completed
```

### 6. Performance Metrics

Extract timing information:

```bash
grep -E "duration|elapsed|time|seconds|ms|latency" /tmp/e2e_logs.txt
```

**Report format:**
```
PERFORMANCE SUMMARY
───────────────────
Total processing time: 2m 50s
Breakdown:
  - PII scan:       5.2s   (2%)
  - Docling:       15.3s   (9%)
  - Phase 1:       45.0s  (26%)
  - Phase 2:       70.0s  (41%)
  - Phase 3:       20.0s  (12%)
  - S3 operations:  8.5s   (5%)
  - Other:          6.0s   (4%)

Bottleneck: Phase 2 (Guided Transcription)
```

---

## Final Report

Combine all analysis into a structured report:

```
════════════════════════════════════════════════════════════
  LOG ANALYSIS REPORT
  Window: Last {SINCE} | Lines: {TAIL}
════════════════════════════════════════════════════════════

{PHASE TIMELINE}

{AGENT ACTIVITY}

{LLM ACTIVITY}

{ERRORS & WARNINGS}

{PERFORMANCE SUMMARY}

────────────────────────────────────────────────────────────
RECOMMENDATIONS
────────────────────────────────────────────────────────────
- [If slow Phase 2]: Consider chunking large documents
- [If errors]: Check AWS credentials: aws sso login --profile uic
- [If low confidence]: Review agent observations manually
- [If timeouts]: Check Bedrock throttling limits
════════════════════════════════════════════════════════════
```

---

## Usage Examples

```bash
# Analyze last 10 minutes (default)
/e2e/log-analysis

# Analyze specific job
/e2e/log-analysis --job=550e8400-e29b-41d4-a716-446655440000

# Focus on errors only
/e2e/log-analysis --focus=errors --since=30m

# Performance deep-dive
/e2e/log-analysis --focus=performance --tail=1000
```
