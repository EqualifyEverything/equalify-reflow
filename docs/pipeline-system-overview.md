# Pipeline System Overview

The pipeline transforms PDF documents into accessible, semantically-correct markdown through an event-driven, multi-phase processing system with real-time streaming feedback.

## Architecture at a Glance

```
PDF Upload (FastAPI)
    ↓
Docling Conversion (thread pool)
    ↓
Vision Extraction (optional - if scanned)
    ↓
Phase 1: Planning (sequential page analysis)
    ↓
Phase 2: Execution (parallel worker agents)
    ↓
Phase 2.5: Issue Fixing (structural cleanup)
    ↓
Phase 3: Verification (quality gates)
    ↓
Phase 4: Recovery (optional - if verification fails)
    ↓
Final Result (markdown + ledger + report)
```

## Core Principles

### 1. Event-Driven Architecture
- All operations emit `StreamEvent` objects
- Events collected in in-memory `EventBus`
- Real-time streaming via Server-Sent Events (SSE)
- Complete audit trail of all processing

### 2. In-Memory State Management
- Job state stored in `_job_store` dictionary
- No Redis/persistent queue for pipeline
- Fast access, no network overhead
- Event history maintained for reconnection

### 3. Agent-Based Processing
- PydanticAI agents with tool use
- Structured outputs via Pydantic models
- Validation gates for all edits
- Complete reasoning traces

### 4. Quality-First Approach
- Multi-layer verification checks
- Automatic recovery for failed pages
- Conservative decision-making
- Accept-with-caveats option

## Key Files

| Component | Location |
|-----------|----------|
| **API Endpoints** | `src/api/documents.py` |
| **Main Orchestrator** | `src/agents/orchestrator.py` |
| **Data Models** | `src/agents/models.py` |
| **Events** | `src/agents/events.py` |
| **Planner** | `src/agents/planner.py` |
| **Page Chain** | `src/agents/page_chain.py` |
| **Worker** | `src/agents/worker.py` |
| **Validation** | `src/agents/validation.py` |
| **Verification** | `src/agents/plan_verification.py` |
| **Recovery** | `src/agents/recovery.py` |
| **Issue Fixing** | `src/agents/issue_fixer.py` |

## Documentation Structure

### Getting Started
- **[API Integration Guide](./pipeline-api-integration.md)** - How to use the pipeline endpoints
- **[Data Models Reference](./pipeline-data-models.md)** - Complete model documentation

### Phase Documentation
- **[Phase 1: Planning](./pipeline-phase-1-planning.md)** - Document structure analysis
- **[Phase 2: Execution](./pipeline-phase-2-execution.md)** - Worker agent processing
- **[Phase 3: Verification](./pipeline-phase-3-verification.md)** - Quality gates
- **[Phase 4: Recovery](./pipeline-phase-4-recovery.md)** - Error recovery

## Quick Start

### Submit a Document
```bash
curl -X POST http://localhost:8080/api/v1/documents/submit \
  -F "file=@document.pdf" \
  | jq -r '.job_id'
```

### Stream Real-Time Progress
```bash
curl -N http://localhost:8080/api/v1/documents/{job_id}/stream
```

### Get Job Status (includes result when complete)
```bash
curl http://localhost:8080/api/v1/documents/{job_id} | jq
```

## Job Lifecycle

### States
- `pending` - Job created, not yet started
- `docling` - PDF conversion in progress
- `vision_extraction` - OCR/vision processing (scanned PDFs)
- `planning` - Document structure analysis
- `executing` - Worker agents processing jobs
- `verifying` - Quality checks running
- `recovering` - Attempting to fix failed pages
- `complete` - Successfully processed
- `failed` - Unrecoverable errors

### Timeline (Typical 10-page Document)
- Docling: 5-10 seconds
- Vision (if needed): 20-30 seconds
- Planning: 15-30 seconds
- Execution: 30-60 seconds
- Verification: 5-10 seconds
- Recovery (if needed): 10-20 seconds
- **Total: 1-3 minutes**

## Data Flow Overview

### Input
- PDF file (uploaded bytes)
- Optional `optimized` flag (two-phase pipeline)

### Intermediate Artifacts
- `page_markdowns: dict[int, str]` - Raw Docling output
- `page_images: dict[int, Image.Image]` - Page screenshots
- `DocumentPlan` - Structure + jobs to execute
- `Ledger` - Append-only change log
- `JobResult[]` - Per-job outcomes

### Output
- `ProcessingResult` containing:
  - `final_markdown: str` - Complete accessible markdown
  - `ledger: Ledger` - All changes with reasoning
  - `verification: VerificationReport` - Quality check results
  - `recovery_report: RecoveryReport | None` - Recovery attempts
  - Token counts, costs, timing metrics

## Agent Behavior

### Planner Agent (Page Chain)
- **Model**: Haiku 4.5
- **Mode**: Sequential with context chaining
- **Tools**: None (pure analysis)
- **Output**: Structured `PageAnalysisOutput` per page

### Worker Agent
- **Model**: Haiku 4.5
- **Mode**: Parallel (max 3 concurrent jobs)
- **Tools**: `view_page()`, `view_figure()`, `view_table()`, `find_text()`, `propose_edit()`
- **Output**: Validated `LedgerEntry[]` per job

### Recovery Agent
- **Model**: Haiku 4.5
- **Mode**: Sequential (one page at a time)
- **Tools**: `view_page()`, `view_markdown()`, `view_history()`, `propose_cleanup()`
- **Output**: `RecoveryAttempt` per page

## Validation & Quality

### Edit Validation (Before Commit)
1. **Spell Check** - Against document dictionary
2. **Markdown Linting** - Syntax validation
3. **Consistency Check** - Text existence, format validation

### Verification Layers
1. **Per-page basics** - Placeholders, empty alt-text, heading hierarchy
2. **Plan-based checks** - Heading structure, figure completeness, table completeness
3. **Spelling verification** - Using document dictionary (first 10 issues)

### Recovery Criteria
- Triggered if: verification fails AND >= 50% pages passed
- Max 2 attempts per failed page
- Actions: cleanup_edit, remove_placeholder, accept_with_caveat, escalate
- Conservative approach: when uncertain, escalate

## Event Streaming

### Event Categories
- **Lifecycle**: Started/Complete events for each phase
- **Progress**: Per-page, per-job, per-edit events
- **Diagnostics**: Agent thinking, validation feedback
- **Errors**: Failure events with details

### SSE Connection
```javascript
const eventSource = new EventSource(`/api/v1/documents/${jobId}/stream`);

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data.event_type, data);
};

eventSource.addEventListener('processing:complete', () => {
  eventSource.close();
});
```

### Reconnection Support
- Events have sequential IDs
- `get_events_since(event_id)` for client reconnection
- Keepalive pings every 15 seconds

## Performance & Costs

### Token Usage (Typical 10-page Document)
- Planning: ~15-20K input, ~5-10K output
- Execution: ~50-100K input, ~20-40K output
- Recovery (if needed): ~10-20K input, ~5-10K output
- **Total: ~75-140K input, ~30-60K output**

### Pricing (Haiku 4.5 on Bedrock)
- Input: $0.00025 per 1K tokens
- Output: $0.00125 per 1K tokens
- **Typical cost per document: $0.05-0.15**

### Optimization Opportunities
- Vision extraction only for scanned PDFs (auto-detected)
- Worker job parallelization (configurable concurrency)
- Issue fixing before verification (catch early)
- Recovery only when >= 50% pages passed

## Common Patterns

### Adding a New Task Type
1. Add enum to `TaskType` in `models.py`
2. Add generation logic in `planner.py:stage3_job_generation()`
3. Add handling in `worker.py` system prompt
4. Add validation rules in `validation.py`
5. Add verification check in `plan_verification.py` (if needed)

### Adding a New Verification Check
1. Add check function in `plan_verification.py`
2. Call from `verify_document()` in `orchestrator.py`
3. Add event type in `events.py` (if needed)
4. Update recovery agent to handle new issue type

### Adding a New Event Type
1. Define model in `events.py` (inherit from `StreamEvent`)
2. Emit via `event_bus.emit(YourEvent(...))`
3. Handle in frontend SSE listener
4. Update documentation

## Troubleshooting

### Job Stuck in "pending"
- Check logs for background task errors
- Verify Docling service is running
- Check file upload size limits

### High Token Usage
- Check if vision extraction is running unnecessarily
- Review page count (tokens scale linearly)
- Consider optimized mode for simple documents

### Verification Always Failing
- Review verification criteria (may be too strict)
- Check document dictionary completeness
- Review critical issues list

### Recovery Not Helping
- May need human review (escalated pages)
- Check if >= 50% pages passed (recovery trigger)
- Review recovery attempt details in report

## Next Steps

1. Read the **[API Integration Guide](./pipeline-api-integration.md)** to understand how to use the endpoints
2. Review the **[Data Models Reference](./pipeline-data-models.md)** for complete schema documentation
3. Dive into individual phase documentation for implementation details:
   - [Phase 1: Planning](./pipeline-phase-1-planning.md)
   - [Phase 2: Execution](./pipeline-phase-2-execution.md)
   - [Phase 3: Verification](./pipeline-phase-3-verification.md)
   - [Phase 4: Recovery](./pipeline-phase-4-recovery.md)

## Related Documentation

- [Architecture Documentation](.claude/docs/architecture.md) - Overall system design
- [Testing Strategy](.claude/docs/testing.md) - How to test pipeline changes
