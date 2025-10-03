# PRD: Core Processing Pipeline Test Coverage

**Story ID:** STORY-009
**Title:** Comprehensive Test Coverage for Core Processing Pipeline
**Priority:** CRITICAL
**Status:** Ready for Implementation
**Created:** 2025-10-03
**Estimated Effort:** 3 weeks (15 days)

---

## 1. Problem Statement

### Critical Risk Assessment

The Equalify PDF Converter's core processing pipeline - the heart of the entire application - currently has **ZERO test coverage**. This represents a critical business risk:

**Affected Components (100% Untested):**
- `ProcessingService` - Main orchestrator for the 8-step processing pipeline
- `PDFConverter` - Docling integration for PDF-to-markdown conversion
- `AIEnhancementService` - Concurrent AI processing with retry logic
- `AccessibilityAgent` - PydanticAI agent for Claude-based enhancement
- `ProcessingWorker` - Background task queue consumer

**Business Impact:**
- **Production Risk:** Cannot detect regressions in core business logic
- **Debugging Difficulty:** No isolated test failures to identify broken components
- **Deployment Safety:** Zero confidence in changes to AI processing or PDF conversion
- **Code Quality:** Complex retry/concurrency logic has no validation
- **Development Velocity:** Fear of breaking production slows down feature work

**Complexity of Untested Code:**
- 8-step processing pipeline with interdependent services
- Concurrent AI processing with semaphore limiting (max 5 pages)
- Exponential backoff retry logic (3 attempts per page, 2^n seconds)
- Multimodal AI inputs (text + base64 images to Claude)
- Error handling across Redis, S3, Docling, and Anthropic APIs
- Graceful shutdown with job requeuing
- Confidence scoring aggregation

**What Could Go Wrong (Without Tests):**
1. Page images not generated → AI processing fails silently
2. Retry logic exhausts → Jobs marked "completed" with partial data
3. Semaphore misconfigured → Claude API rate limits exceeded
4. Worker shutdown → Jobs lost without requeue
5. Confidence scoring bug → High-quality docs flagged as low confidence
6. S3 upload failure → Results lost, job status inconsistent

---

## 2. Scope & Priorities

### Total Test Breakdown

**109 Total Tests to Implement:**
- Unit Tests: 87 (isolated component logic)
- Integration Tests: 19 (multi-component interactions)
- E2E Tests: 3 (full pipeline validation)

### 3-Week Implementation Plan

#### Week 1 (Critical Path) - 53 Tests
**Risk Mitigation:** Core orchestration and AI processing

1. **ProcessingService** (20 unit tests) - 2 days
   - Main pipeline orchestrator
   - Highest complexity, most dependencies
   - Blocks all other testing

2. **AIEnhancementService** (18 unit tests) - 2 days
   - Concurrent processing logic
   - Retry/backoff mechanisms
   - Critical for AI reliability

3. **PDFConverter** (15 unit tests) - 1 day
   - Docling integration
   - Page image validation
   - Foundation for AI processing

**Week 1 Outcome:** 60% of critical path tested

#### Week 2 (Supporting Components) - 34 Tests
**Risk Mitigation:** AI agent and worker lifecycle

4. **AccessibilityAgent** (12 unit tests) - 1.5 days
   - PydanticAI agent configuration
   - Prompt loading (YAML + fallback)
   - Structured output validation

5. **ProcessingWorker** (14 unit tests) - 1.5 days
   - Worker lifecycle (start/stop)
   - Graceful shutdown + job requeue
   - Error recovery

6. **confidence_scoring** (8 unit tests) - 0.5 days
   - Aggregate scoring logic
   - Classification thresholds
   - Simple utility validation

**Week 2 Outcome:** All unit tests complete (87/87)

#### Week 3 (Integration & E2E) - 22 Tests
**Risk Mitigation:** Multi-component validation

7. **Integration Tests** (19 tests) - 2 days
   - Real Redis + LocalStack S3
   - Mock only AI/Docling
   - Cross-component workflows

8. **E2E Tests** (3 tests) - 1 day
   - Full pipeline (PDF → Result)
   - Error recovery flow
   - Concurrent document processing

**Week 3 Outcome:** 100% test coverage (109/109)

---

## 3. Test Architecture

### Unit Tests (87 Tests - Fast, Isolated)

**Principles:**
- Mock ALL external dependencies (Redis, S3, Claude, Docling)
- Test individual methods in isolation
- Execution time: < 1 second per test
- Focus: Logic, error handling, edge cases

**Mock Fixtures:**
```python
# Core service mocks
@pytest.fixture
def mock_storage_service(mocker):
    storage = mocker.AsyncMock(spec=StorageService)
    storage.download_temp_file = AsyncMock(return_value=b"fake pdf")
    storage.upload_result = AsyncMock(return_value="s3://results/job123.md")
    return storage

@pytest.fixture
def mock_pdf_converter(mocker):
    converter = mocker.Mock(spec=PDFConverter)
    converter.convert_with_page_images = AsyncMock(return_value=PDFConversionResult(
        pages=[PageData(page_num=1, markdown="# Test", image_base64="base64")],
        total_pages=1,
        full_markdown="# Test",
        has_page_images=True
    ))
    return converter

@pytest.fixture
def mock_ai_enhancement(mocker):
    service = mocker.Mock(spec=AIEnhancementService)
    service.process_pages_concurrently = AsyncMock(return_value=[
        PageImprovementResult(
            improved_markdown="# Improved",
            confidence_score=0.92,
            processing_notes="Fixed headings"
        )
    ])
    service.combine_page_markdown = Mock(return_value="# Combined")
    return service

@pytest.fixture
def mock_pydantic_agent(mocker):
    agent = mocker.Mock(spec=Agent)
    agent.run = AsyncMock(return_value=PageImprovementResult(...))
    return agent
```

### Integration Tests (19 Tests - Real Infrastructure)

**Principles:**
- Use real LocalStack S3 (from docker-compose)
- Use real Redis (from docker-compose)
- Mock only Claude API and Docling internals
- Execution time: 5-10 seconds per test

**Infrastructure Setup:**
```python
# Use existing docker-compose services
@pytest.fixture
async def real_s3_client():
    """Real S3 client pointing to LocalStack."""
    client = boto3.client(
        "s3",
        endpoint_url="http://localstack:4566",
        aws_access_key_id="test",
        aws_secret_access_key="test"
    )
    # Create test bucket
    client.create_bucket(Bucket=settings.s3_bucket_name)
    yield client
    # Cleanup
    objects = client.list_objects_v2(Bucket=settings.s3_bucket_name)
    for obj in objects.get('Contents', []):
        client.delete_object(Bucket=settings.s3_bucket_name, Key=obj['Key'])

@pytest.fixture
async def real_redis_client():
    """Real Redis client from docker-compose."""
    redis = Redis.from_url("redis://redis:6379/0")
    yield redis
    # Cleanup test keys
    await redis.flushdb()
```

### E2E Tests (3 Tests - Full Pipeline)

**Principles:**
- Full Docker environment (API + Workers + Redis + LocalStack)
- Mock ONLY Claude API (cost/rate limit protection)
- Test complete user flows
- Execution time: 1-2 minutes per test

**Environment:**
```bash
# Start full stack
make dev

# Run E2E tests
pytest tests/e2e/ -v --e2e
```

---

## 4. Component Test Plans

### 4.1 ProcessingService (20 Unit Tests)

**File:** `tests/services/test_processing_service.py`

**Critical Responsibilities:**
- Orchestrate 8-step processing pipeline
- Coordinate StorageService, PDFConverter, AIEnhancementService, JobService
- Handle PageProcessingError and generic exceptions
- Retry S3/Redis operations with exponential backoff
- Calculate and store confidence metrics
- Track processing time

#### Test Breakdown

**Initialization (2 tests):**
1. Test default PDFConverter/AIEnhancement created if not provided
2. Test custom dependencies injected correctly

**Success Path (4 tests):**
3. Test complete pipeline with mock dependencies (happy path)
4. Test confidence score calculation integration
5. Test processing time measurement accuracy
6. Test metadata stored in JobService correctly

**Error Handling (5 tests):**
7. Test PageProcessingError caught, job marked "failed", re-raised
8. Test generic exception caught, job marked "failed", error message stored
9. Test job status update on AI failure
10. Test job status update on unexpected error
11. Test error message format and content

**Retry Logic (4 tests):**
12. Test retry_with_backoff called for job status updates
13. Test retry_with_backoff called for S3 downloads
14. Test retry_with_backoff called for S3 uploads
15. Test retry_with_backoff called for final job update

**Edge Cases (5 tests):**
16. Test missing page images raises RuntimeError (critical validation)
17. Test empty PDF handling (0 pages)
18. Test single page document
19. Test S3 download failure after retries exhausted
20. Test S3 upload failure after retries exhausted

#### Mock Strategy

**Always Mock:**
- `StorageService`: S3 operations
- `QueueService`: Queue operations
- `JobService`: Redis job status
- `PDFConverter`: Docling conversion
- `AIEnhancementService`: Claude API calls
- `retry_with_backoff`: Retry logic (test separately)

**Key Assertions:**
```python
# Verify pipeline order
mock_job_service.update_job_status.assert_any_call(job_id, "processing")
mock_storage.download_temp_file.assert_called_once()
mock_pdf_converter.convert_with_page_images.assert_called_once()
mock_ai_enhancement.process_pages_concurrently.assert_called_once()
mock_storage.upload_result.assert_called_once()
mock_job_service.update_job_status.assert_any_call(job_id, "completed", metadata=ANY)

# Verify confidence scoring
assert result.confidence_score == 0.92
assert result.confidence_level == "high"

# Verify timing
assert result.processing_time_seconds > 0
```

#### Sample Test

```python
@pytest.mark.asyncio
async def test_process_document_no_page_images_raises_error(
    processing_service, mock_pdf_converter, mock_job_service
):
    """Test RuntimeError when Docling fails to generate page images."""
    # Arrange
    payload = ProcessingQueuePayload(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        s3_key="temp/test.pdf",
        approved_at=None
    )

    # Configure converter to return result WITHOUT images (critical failure)
    mock_pdf_converter.convert_with_page_images.return_value = PDFConversionResult(
        pages=[PageData(page_num=1, markdown="# Test", image_base64="")],
        total_pages=1,
        full_markdown="# Test",
        has_page_images=False  # CRITICAL: AI requires images
    )

    # Act
    result = await processing_service.process_document(payload)

    # Assert
    assert result.error_message is not None
    assert "page images" in result.error_message.lower()

    # Verify job marked failed
    mock_job_service.update_job_status.assert_any_call(
        payload.job_id, "failed", error=ANY
    )
```

**Risk if Untested:**
- Silent failures in pipeline steps
- Jobs marked "completed" with partial data
- Confidence scores calculated incorrectly
- S3 upload failures not retried
- Processing time not tracked

---

### 4.2 PDFConverter (15 Unit Tests)

**File:** `tests/services/test_pdf_converter.py`

**Critical Responsibilities:**
- Configure Docling with correct pipeline options
- Convert PDF bytes to markdown + page images
- Extract per-page markdown from Docling document
- Convert PIL images to base64 PNG strings
- Validate page images were generated
- Handle table extraction failures gracefully

#### Test Breakdown

**Initialization (2 tests):**
1. Test Docling converter initialized with correct PdfPipelineOptions
2. Test pipeline options configuration (generate_page_images=True, do_ocr=True, etc.)

**Success Path (5 tests):**
3. Test valid PDF conversion returns PDFConversionResult
4. Test page count matches expected
5. Test full markdown generated correctly
6. Test has_page_images flag set to True
7. Test base64 encoding format (PNG)

**Page Extraction (3 tests):**
8. Test single page extraction
9. Test multi-page extraction
10. Test page numbering (1-indexed Docling → 0-indexed Python)

**Error Handling (4 tests):**
11. Test invalid PDF raises ValueError
12. Test corrupted PDF raises ValueError
13. Test no page images raises RuntimeError
14. Test exception message includes helpful context

**Edge Cases (1 test):**
15. Test PDF with complex tables (fallback to "[Table content]")

#### Mock Strategy

**Always Mock:**
- `docling.DocumentConverter`: Conversion logic
- Docling document structure (pages, items, images)
- PIL.Image operations

**Real Implementations:**
- `_extract_page_markdown`: Test actual logic
- `_image_to_base64`: Test actual encoding

**Key Assertions:**
```python
# Verify Docling configuration
assert converter.pipeline_options.generate_page_images is True
assert converter.pipeline_options.do_ocr is True
assert converter.pipeline_options.images_scale == 2.0

# Verify conversion result
assert isinstance(result, PDFConversionResult)
assert result.total_pages == expected_pages
assert result.has_page_images is True
assert all(page.image_base64 for page in result.pages)

# Verify page data structure
assert result.pages[0].page_num == 1
assert result.pages[0].markdown.startswith("# ")
assert result.pages[0].image_base64.startswith("iVBOR")  # PNG signature
```

#### Sample Test

```python
@pytest.mark.asyncio
async def test_convert_no_page_images_raises_runtime_error(pdf_converter, mocker):
    """Test RuntimeError when Docling doesn't generate page images."""
    # Arrange
    pdf_content = b"%PDF-1.4\n...test content..."

    # Mock Docling to return document without images
    mock_doc = mocker.Mock()
    mock_doc.pages = {
        1: mocker.Mock(image=None)  # CRITICAL: No image
    }
    mock_doc.export_to_markdown = mocker.Mock(return_value="# Test")

    mock_result = mocker.Mock()
    mock_result.document = mock_doc

    pdf_converter.converter.convert = mocker.Mock(return_value=mock_result)
    pdf_converter._extract_page_markdown = mocker.Mock(return_value="# Test")

    # Act & Assert
    with pytest.raises(RuntimeError) as exc_info:
        await pdf_converter.convert_with_page_images(pdf_content)

    assert "failed to generate page images" in str(exc_info.value).lower()
    assert "multimodal AI processing requires page images" in str(exc_info.value)
```

**Risk if Untested:**
- Docling misconfiguration (no images, no OCR)
- Invalid PDFs crash the worker
- Page numbering bugs (off-by-one)
- Base64 encoding failures
- Complex tables break conversion

---

### 4.3 AIEnhancementService (18 Unit Tests)

**File:** `tests/services/test_ai_enhancement_service.py`

**Critical Responsibilities:**
- Process pages concurrently with semaphore limiting (max 5)
- Retry individual page processing (3 attempts, exponential backoff)
- Combine improved markdown with page separators
- Propagate PageProcessingError on failure
- Rate limit API calls (2s delay per page)

#### Test Breakdown

**Initialization (4 tests):**
1. Test default max_concurrent_pages = 5
2. Test custom max_concurrent_pages
3. Test default AccessibilityAgent created if not provided
4. Test custom agent injected

**process_page_with_retry (6 tests):**
5. Test success on first attempt
6. Test success after 1 retry
7. Test success after 2 retries
8. Test failure after max retries raises PageProcessingError
9. Test exponential backoff timing (2s, 4s, 8s)
10. Test retry_attempt passed to agent correctly

**process_pages_concurrently (5 tests):**
11. Test single page processing
12. Test multiple pages (< max_concurrent)
13. Test multiple pages (> max_concurrent, semaphore queuing)
14. Test one page fails → entire batch fails
15. Test semaphore limiting (verify max concurrent = 5)

**combine_page_markdown (3 tests):**
16. Test single page (no separator)
17. Test multiple pages (separators added)
18. Test markdown order preservation

#### Mock Strategy

**Always Mock:**
- `AccessibilityAgent.process_page`: Claude API call
- `asyncio.sleep`: Time delays (use mock to verify timing)

**Real Implementations:**
- Semaphore logic
- asyncio.gather concurrent execution
- Retry loop with backoff calculation

**Key Assertions:**
```python
# Verify retry logic
assert mock_agent.process_page.call_count == 3  # All retries exhausted

# Verify exponential backoff
mock_sleep.assert_any_call(2)  # 2^1
mock_sleep.assert_any_call(4)  # 2^2

# Verify semaphore limiting
assert max_concurrent_calls <= 5

# Verify error propagation
with pytest.raises(PageProcessingError) as exc_info:
    await service.process_pages_concurrently(pages)
assert exc_info.value.page_num == 2
```

#### Sample Test

```python
@pytest.mark.asyncio
async def test_exponential_backoff_timing(ai_service, mock_agent, mocker):
    """Test exponential backoff delays (2s, 4s, 8s)."""
    # Arrange
    page_data = PageData(page_num=1, markdown="# Test", image_base64="base64")

    # Mock asyncio.sleep to track delays
    mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)

    # Configure agent to fail twice, succeed on third
    mock_agent.process_page = AsyncMock(side_effect=[
        Exception("Temporary API error"),
        Exception("Still failing"),
        PageImprovementResult(
            improved_markdown="# Success",
            confidence_score=0.85,
            processing_notes="Worked on third try"
        )
    ])

    # Act
    result = await ai_service.process_page_with_retry(page_data, max_retries=3)

    # Assert
    assert result.confidence_score == 0.85
    assert mock_agent.process_page.call_count == 3

    # Verify exponential backoff: 2^1=2s, 2^2=4s
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(2)  # First retry delay
    mock_sleep.assert_any_call(4)  # Second retry delay
```

**Risk if Untested:**
- Semaphore misconfigured → rate limits exceeded
- Retry logic broken → first failure kills job
- Backoff timing wrong → API throttling
- Page separator bugs → malformed markdown
- One page failure not propagated

---

### 4.4 AccessibilityAgent (12 Unit Tests)

**File:** `tests/agents/test_accessibility_agent.py`

**Critical Responsibilities:**
- Initialize PydanticAI agent with Claude model
- Load prompts from YAML (with fallback to defaults)
- Format multimodal input (text + base64 image)
- Execute AI processing with structured output
- Implement singleton pattern for agent reuse

#### Test Breakdown

**Initialization (5 tests):**
1. Test agent initialized with correct model (claude-3-5-haiku)
2. Test prompts loaded from YAML file
3. Test fallback prompts used if YAML missing
4. Test system prompt set correctly
5. Test model_settings configured (max_tokens, temperature)

**process_page (4 tests):**
6. Test successful processing returns PageImprovementResult
7. Test multimodal input formatted correctly (text + BinaryContent)
8. Test model_settings passed to agent.run()
9. Test retry_attempt logged

**Error Handling (2 tests):**
10. Test Claude API failure raises exception
11. Test invalid base64 raises exception

**Singleton Pattern (1 test):**
12. Test get_accessibility_agent() reuses same instance

#### Mock Strategy

**Always Mock:**
- `pydantic_ai.Agent`: AI agent initialization
- `agent.run()`: Claude API calls
- `yaml.safe_load`: Prompt loading (for error tests)

**Real Implementations:**
- Base64 decoding logic
- Multimodal input formatting
- Singleton logic

**Key Assertions:**
```python
# Verify agent configuration
assert agent.model == "claude-3-5-haiku-20241022"
assert agent.model_settings["max_tokens"] == 4096
assert agent.model_settings["temperature"] == 0.2

# Verify prompts loaded
assert agent.system_prompt.startswith("You are an accessibility")
assert "markdown" in agent.user_prompt_template.lower()

# Verify multimodal input
agent.run.assert_called_once_with(
    [
        "user_message text",
        BinaryContent(data=ANY, media_type="image/png")
    ]
)

# Verify singleton
agent1 = get_accessibility_agent()
agent2 = get_accessibility_agent()
assert agent1 is agent2
```

#### Sample Test

```python
@pytest.mark.asyncio
async def test_prompts_loaded_from_yaml(mocker):
    """Test prompts loaded from config/accessibility_prompts.yaml."""
    # Arrange
    mock_yaml_data = {
        "system_prompt": "Test system prompt",
        "user_prompt_template": "Test user prompt: {markdown}"
    }

    # Mock yaml.safe_load
    mock_open = mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("yaml.safe_load", return_value=mock_yaml_data)

    # Mock PydanticAI Agent
    mock_agent = mocker.Mock(spec=Agent)
    mocker.patch("pydantic_ai.Agent", return_value=mock_agent)

    # Act
    agent = AccessibilityAgent()

    # Assert
    assert agent.system_prompt == "Test system prompt"
    assert agent.user_prompt_template == "Test user prompt: {markdown}"

    # Verify YAML file opened
    mock_open.assert_called_once_with("config/accessibility_prompts.yaml", "r")
```

**Risk if Untested:**
- Prompt loading fails silently
- Wrong Claude model used
- Multimodal input malformed
- Singleton creates multiple agents
- Structured output validation broken

---

### 4.5 ProcessingWorker (14 Unit Tests)

**File:** `tests/workers/test_processing_worker.py`

**Critical Responsibilities:**
- Poll Redis queue with 60s timeout
- Deserialize queue payload to ProcessingQueuePayload
- Invoke ProcessingService.process_document()
- Handle graceful shutdown with job requeue
- Update Prometheus metrics (active, processed, errors)
- Recover from exceptions and continue loop

#### Test Breakdown

**Initialization (2 tests):**
1. Test ProcessingService created with correct dependencies
2. Test running flag initialized to False

**Worker Loop (5 tests):**
3. Test start() sets running=True and worker_active_gauge=1
4. Test worker processes jobs from queue
5. Test worker continues on empty queue (timeout)
6. Test worker stops when running=False
7. Test worker respects shutdown_event

**Graceful Shutdown (3 tests):**
8. Test shutdown during queue wait
9. Test shutdown before job processing (job requeued)
10. Test worker_active_gauge set to 0 on shutdown

**Error Handling (4 tests):**
11. Test exception during processing (continues loop)
12. Test invalid queue payload (Pydantic validation error)
13. Test metrics updated on error (worker_errors_total)
14. Test 5-second error sleep delay

#### Mock Strategy

**Always Mock:**
- `QueueService.dequeue`: Queue polling
- `ProcessingService.process_document`: Main processing
- `MetricsService`: Prometheus gauges/counters
- `asyncio.Event`: Shutdown signaling

**Real Implementations:**
- Worker loop logic
- Running flag state
- Exception handling

**Key Assertions:**
```python
# Verify worker started
assert worker.running is True
mock_metrics.worker_active_gauge.set.assert_called_with(1)

# Verify job processing
mock_queue.dequeue.assert_called_with(PROCESSING_QUEUE, timeout=60)
mock_processing_service.process_document.assert_called_once_with(job_payload)

# Verify graceful shutdown
mock_queue.enqueue.assert_called_with(PROCESSING_QUEUE, job_data)  # Requeued
assert worker.running is False
mock_metrics.worker_active_gauge.set.assert_called_with(0)

# Verify error handling
mock_metrics.worker_errors_total.inc.assert_called_once()
```

#### Sample Test

```python
@pytest.mark.asyncio
async def test_graceful_shutdown_requeues_job(worker, mock_queue, mock_processing):
    """Test job requeued when shutdown event set before processing."""
    # Arrange
    shutdown_event = asyncio.Event()

    job_data = {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/test.pdf",
        "approved_at": None
    }

    # Configure queue to return job, then shutdown
    async def dequeue_then_shutdown(*args, **kwargs):
        shutdown_event.set()  # Trigger shutdown
        return job_data

    mock_queue.dequeue = AsyncMock(side_effect=dequeue_then_shutdown)

    # Act
    await worker.start(shutdown_event=shutdown_event)

    # Assert - job should be requeued, not processed
    mock_queue.enqueue.assert_called_once_with(PROCESSING_QUEUE, job_data)
    mock_processing.process_document.assert_not_called()

    # Worker should stop gracefully
    assert worker.running is False
```

**Risk if Untested:**
- Worker doesn't stop on shutdown
- Jobs lost during shutdown
- Metrics not updated
- Validation errors crash worker
- Exceptions kill worker loop

---

### 4.6 confidence_scoring.py (8 Unit Tests)

**File:** `tests/utils/test_confidence_scoring.py`

**Critical Responsibilities:**
- Classify confidence levels (high/medium/low)
- Aggregate page confidence scores (average)
- Calculate document confidence (score + level tuple)

#### Test Breakdown

**classify_confidence_level (4 tests):**
1. Test high threshold (>= 0.85)
2. Test medium threshold (>= 0.60)
3. Test low threshold (< 0.60)
4. Test boundary values (0.85, 0.60, 0.59, 0.84)

**aggregate_page_confidences (3 tests):**
5. Test empty list returns 0.0
6. Test single page returns that score
7. Test multiple pages (average calculation)

**calculate_document_confidence (1 test):**
8. Test tuple return format (score, level)

#### Mock Strategy

**No Mocks Needed** - Pure functions with no dependencies

**Key Assertions:**
```python
# Classify confidence
assert classify_confidence_level(0.92) == "high"
assert classify_confidence_level(0.75) == "medium"
assert classify_confidence_level(0.45) == "low"
assert classify_confidence_level(0.85) == "high"  # Boundary
assert classify_confidence_level(0.84) == "medium"  # Boundary

# Aggregate scores
assert aggregate_page_confidences([0.9, 0.8, 0.7]) == 0.8
assert aggregate_page_confidences([]) == 0.0

# Calculate document confidence
score, level = calculate_document_confidence([0.9, 0.85, 0.88])
assert score == pytest.approx(0.876)
assert level == "high"
```

**Risk if Untested:**
- Wrong thresholds applied
- Average calculation bugs
- Boundary conditions handled incorrectly

---

## 5. Mock Strategies

### What to Mock (External Dependencies)

**Always Mock in Unit Tests:**

1. **Anthropic Claude API**
   - Reason: Cost, rate limits, unpredictability
   - Mock: `pydantic_ai.Agent.run()`
   - Return: Realistic `PageImprovementResult` instances

2. **Docling Internals**
   - Reason: Complex, slow, external library
   - Mock: `DocumentConverter.convert()`
   - Return: Mock Docling document structure

3. **S3 Operations**
   - Reason: External service, slow
   - Mock: `StorageService` methods
   - Return: Fake S3 URLs, byte content

4. **Redis Operations**
   - Reason: External service
   - Mock: `QueueService`, `JobService` methods
   - Return: Queue payloads, job status

5. **Time Operations**
   - Reason: Predictable test timing
   - Mock: `asyncio.sleep`, `time.time()`
   - Return: Controlled delays, timestamps

### When to Use Real Dependencies (Integration Tests)

**Real in Integration Tests:**

1. **Redis (from docker-compose)**
   - Service: `redis:6379`
   - Setup: Real Redis client
   - Cleanup: `flushdb()` per test

2. **LocalStack S3 (from docker-compose)**
   - Service: `localstack:4566`
   - Setup: Real boto3 client
   - Cleanup: Delete test objects per test

3. **File System**
   - Real YAML files for prompt loading
   - Real test PDF fixtures

**Still Mock in Integration Tests:**

1. **Claude API** - Cost/rate limits
2. **Docling conversion** - Slow, complex

### Mock Fixture Patterns

**AsyncMock for Async Functions:**
```python
from unittest.mock import AsyncMock

@pytest.fixture
def mock_storage_service(mocker):
    storage = mocker.AsyncMock(spec=StorageService)
    storage.download_temp_file = AsyncMock(return_value=b"pdf content")
    storage.upload_result = AsyncMock(return_value="s3://results/job.md")
    return storage
```

**Mock.return_value for Sync Functions:**
```python
@pytest.fixture
def mock_pdf_converter(mocker):
    converter = mocker.Mock(spec=PDFConverter)
    converter.convert_with_page_images = AsyncMock(return_value=PDFConversionResult(...))
    return converter
```

**side_effect for Sequential Returns:**
```python
@pytest.fixture
def mock_agent_with_retries(mocker):
    agent = mocker.Mock()
    agent.process_page = AsyncMock(side_effect=[
        Exception("First failure"),
        Exception("Second failure"),
        PageImprovementResult(...)  # Success on third attempt
    ])
    return agent
```

**Spy on Real Objects:**
```python
@pytest.fixture
def processing_service_with_spy(mocker, real_storage, real_queue, real_job):
    service = ProcessingService(real_storage, real_queue, real_job)

    # Spy on specific method
    mocker.spy(service, 'process_document')

    return service
```

---

## 6. Test File Organization

```
tests/
├── conftest.py                           # Shared fixtures
│   ├── Mock service fixtures
│   ├── Real Redis/S3 fixtures
│   ├── Test data fixtures
│   └── Pytest configuration
│
├── services/
│   ├── test_processing_service.py        # 20 unit tests
│   │   ├── TestInitialization
│   │   ├── TestProcessDocument
│   │   ├── TestErrorHandling
│   │   ├── TestRetryLogic
│   │   └── TestEdgeCases
│   │
│   ├── test_pdf_converter.py             # 15 unit tests
│   │   ├── TestInitialization
│   │   ├── TestConvertWithPageImages
│   │   ├── TestPageExtraction
│   │   ├── TestErrorHandling
│   │   └── TestEdgeCases
│   │
│   └── test_ai_enhancement_service.py    # 18 unit tests
│       ├── TestInitialization
│       ├── TestProcessPageWithRetry
│       ├── TestProcessPagesConcurrently
│       └── TestCombinePageMarkdown
│
├── agents/
│   └── test_accessibility_agent.py       # 12 unit tests
│       ├── TestInitialization
│       ├── TestProcessPage
│       ├── TestErrorHandling
│       └── TestSingletonPattern
│
├── workers/
│   └── test_processing_worker.py         # 14 unit tests
│       ├── TestInitialization
│       ├── TestWorkerLoop
│       ├── TestGracefulShutdown
│       └── TestErrorHandling
│
├── utils/
│   └── test_confidence_scoring.py        # 8 unit tests
│       ├── TestClassifyConfidenceLevel
│       ├── TestAggregatePageConfidences
│       └── TestCalculateDocumentConfidence
│
├── integration/
│   └── workflows/
│       ├── test_processing_pipeline.py   # 5 integration tests
│       │   ├── Test full pipeline with real Redis/S3
│       │   ├── Test concurrent job processing
│       │   ├── Test error recovery
│       │   ├── Test job status transitions
│       │   └── Test processing time tracking
│       │
│       ├── test_pdf_conversion.py        # 3 integration tests
│       │   ├── Test real Docling conversion
│       │   ├── Test scanned PDF with OCR
│       │   └── Test large multi-page PDF
│       │
│       ├── test_concurrent_ai.py         # 4 integration tests
│       │   ├── Test concurrent processing with mock agent
│       │   ├── Test retry behavior with intermittent failures
│       │   ├── Test semaphore limiting under load
│       │   └── Test performance with 10+ pages
│       │
│       ├── test_agent_prompts.py         # 3 integration tests
│       │   ├── Test real YAML prompt loading
│       │   ├── Test structured output validation
│       │   └── Test multimodal input formatting
│       │
│       └── test_worker_lifecycle.py      # 4 integration tests
│           ├── Test worker lifecycle with real Redis
│           ├── Test multiple jobs processed sequentially
│           ├── Test graceful shutdown with job requeue
│           └── Test error recovery and continuation
│
└── e2e/
    ├── test_full_pipeline.py             # 1 E2E test
    │   └── Test complete document processing (submit → results)
    │
    ├── test_error_recovery.py            # 1 E2E test
    │   └── Test AI failure → retry → success flow
    │
    └── test_concurrent_documents.py      # 1 E2E test
        └── Test 5 concurrent document processing
```

**Total:** 109 tests across 16 files

---

## 7. Sample Test Implementations

### 7.1 ProcessingService Complete Test

```python
# tests/services/test_processing_service.py

import pytest
from unittest.mock import AsyncMock, Mock, ANY
from src.services.processing_service import ProcessingService
from src.services.pdf_converter import PDFConversionResult, PageData
from src.agents.accessibility_agent import PageImprovementResult
from src.shared.models.queue import ProcessingQueuePayload
from src.services.ai_enhancement_service import PageProcessingError

# ========== Fixtures ==========

@pytest.fixture
def mock_storage(mocker):
    storage = mocker.AsyncMock()
    storage.download_temp_file = AsyncMock(return_value=b"fake pdf content")
    storage.upload_result = AsyncMock(return_value="s3://results/job123/output.md")
    return storage

@pytest.fixture
def mock_queue(mocker):
    return mocker.AsyncMock()

@pytest.fixture
def mock_job_service(mocker):
    job_service = mocker.AsyncMock()
    job_service.update_job_status = AsyncMock()
    return job_service

@pytest.fixture
def mock_pdf_converter(mocker):
    converter = mocker.Mock()

    conversion_result = PDFConversionResult(
        pages=[
            PageData(page_num=1, markdown="# Test Page 1", image_base64="base64data1"),
            PageData(page_num=2, markdown="# Test Page 2", image_base64="base64data2"),
        ],
        total_pages=2,
        full_markdown="# Test Page 1\n\n# Test Page 2",
        has_page_images=True
    )
    converter.convert_with_page_images = AsyncMock(return_value=conversion_result)
    return converter

@pytest.fixture
def mock_ai_enhancement(mocker):
    service = mocker.Mock()

    improvement_results = [
        PageImprovementResult(
            improved_markdown="# Improved Page 1\n\nAccessible content",
            confidence_score=0.92,
            processing_notes="Fixed heading hierarchy, added alt text"
        ),
        PageImprovementResult(
            improved_markdown="# Improved Page 2\n\nMore accessible content",
            confidence_score=0.88,
            processing_notes="Converted table to semantic markup"
        ),
    ]
    service.process_pages_concurrently = AsyncMock(return_value=improvement_results)
    service.combine_page_markdown = Mock(
        return_value="# Improved Page 1\n\n<!-- Page 2 -->\n# Improved Page 2"
    )
    return service

@pytest.fixture
def processing_service(mock_storage, mock_queue, mock_job_service,
                       mock_pdf_converter, mock_ai_enhancement):
    return ProcessingService(
        storage_service=mock_storage,
        queue_service=mock_queue,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement
    )

# ========== Tests ==========

class TestProcessDocument:
    """Tests for process_document main pipeline."""

    @pytest.mark.asyncio
    async def test_process_document_success_happy_path(
        self, processing_service, mock_storage, mock_job_service,
        mock_pdf_converter, mock_ai_enhancement, mocker
    ):
        """Test successful document processing end-to-end."""
        # Arrange
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/test.pdf",
            approved_at=None
        )

        # Mock retry_with_backoff to pass through
        mocker.patch(
            "src.services.processing_service.retry_with_backoff",
            side_effect=lambda func, **kwargs: func()
        )

        # Act
        result = await processing_service.process_document(payload)

        # Assert - ProcessingResult
        assert result.job_id == payload.job_id
        assert result.markdown_url == "s3://results/job123/output.md"
        assert result.confidence_score == pytest.approx(0.9)  # Average of 0.92, 0.88
        assert result.confidence_level == "high"
        assert result.error_message is None
        assert result.processing_time_seconds > 0

        # Assert - Pipeline steps executed in order

        # Step 1: Update job to "processing"
        mock_job_service.update_job_status.assert_any_call(
            payload.job_id, "processing"
        )

        # Step 2: Download PDF
        mock_storage.download_temp_file.assert_called_once_with(s3_key=payload.s3_key)

        # Step 3: Convert PDF
        mock_pdf_converter.convert_with_page_images.assert_called_once_with(
            b"fake pdf content"
        )

        # Step 4: Process pages with AI
        mock_ai_enhancement.process_pages_concurrently.assert_called_once()
        call_args = mock_ai_enhancement.process_pages_concurrently.call_args[0][0]
        assert len(call_args) == 2  # 2 pages
        assert call_args[0].page_num == 1
        assert call_args[1].page_num == 2

        # Step 5: Combine markdown
        mock_ai_enhancement.combine_page_markdown.assert_called_once()

        # Step 6: Upload result
        mock_storage.upload_result.assert_called_once()
        upload_call = mock_storage.upload_result.call_args
        assert upload_call[1]["job_id"] == payload.job_id
        assert upload_call[1]["content"] == "# Improved Page 1\n\n<!-- Page 2 -->\n# Improved Page 2"

        # Step 7: Update job to "completed"
        mock_job_service.update_job_status.assert_any_call(
            payload.job_id,
            "completed",
            metadata={
                "markdown_url": "s3://results/job123/output.md",
                "confidence_score": pytest.approx(0.9),
                "confidence_level": "high",
                "processing_time_seconds": ANY,
                "total_pages": 2,
            }
        )

    @pytest.mark.asyncio
    async def test_process_document_no_page_images_raises_error(
        self, processing_service, mock_pdf_converter, mock_job_service, mocker
    ):
        """Test RuntimeError when Docling fails to generate page images."""
        # Arrange
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/test.pdf",
            approved_at=None
        )

        # Configure converter to return result WITHOUT images (critical failure)
        mock_pdf_converter.convert_with_page_images.return_value = PDFConversionResult(
            pages=[PageData(page_num=1, markdown="# Test", image_base64="")],
            total_pages=1,
            full_markdown="# Test",
            has_page_images=False  # CRITICAL: AI requires images for visual comparison
        )

        mocker.patch(
            "src.services.processing_service.retry_with_backoff",
            side_effect=lambda func, **kwargs: func()
        )

        # Act
        result = await processing_service.process_document(payload)

        # Assert - Error captured
        assert result.error_message is not None
        assert "page images" in result.error_message.lower()

        # Assert - Job marked failed
        mock_job_service.update_job_status.assert_any_call(
            payload.job_id, "failed", error=ANY
        )

    @pytest.mark.asyncio
    async def test_process_document_ai_processing_error(
        self, processing_service, mock_ai_enhancement, mock_job_service, mocker
    ):
        """Test handling of AI processing failure (PageProcessingError)."""
        # Arrange
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/test.pdf",
            approved_at=None
        )

        # Configure AI service to raise PageProcessingError
        mock_ai_enhancement.process_pages_concurrently.side_effect = PageProcessingError(
            page_num=2,
            original_error=Exception("Claude API timeout after 3 retries")
        )

        mocker.patch(
            "src.services.processing_service.retry_with_backoff",
            side_effect=lambda func, **kwargs: func()
        )

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await processing_service.process_document(payload)

        assert "AI processing failed" in str(exc_info.value)
        assert "page 2" in str(exc_info.value).lower()

        # Verify job marked as failed
        mock_job_service.update_job_status.assert_any_call(
            payload.job_id, "failed", error=ANY
        )

    @pytest.mark.asyncio
    async def test_process_document_s3_upload_failure(
        self, processing_service, mock_storage, mock_job_service, mocker
    ):
        """Test handling of S3 upload failure after retries."""
        # Arrange
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/test.pdf",
            approved_at=None
        )

        # Configure S3 upload to fail
        mock_storage.upload_result.side_effect = Exception("S3 connection timeout")

        # Mock retry_with_backoff to fail after retries
        async def mock_retry(func, **kwargs):
            await func()  # Execute once
            raise Exception("S3 connection timeout (after 3 retries)")

        mocker.patch(
            "src.services.processing_service.retry_with_backoff",
            side_effect=mock_retry
        )

        # Act
        result = await processing_service.process_document(payload)

        # Assert - Error captured
        assert result.error_message is not None
        assert "S3" in result.error_message or "timeout" in result.error_message

        # Verify job marked failed
        mock_job_service.update_job_status.assert_any_call(
            payload.job_id, "failed", error=ANY
        )

    @pytest.mark.asyncio
    async def test_process_document_confidence_score_calculation(
        self, processing_service, mock_ai_enhancement, mocker
    ):
        """Test confidence score calculated correctly (average of pages)."""
        # Arrange
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/test.pdf",
            approved_at=None
        )

        # Configure AI to return specific confidence scores
        improvement_results = [
            PageImprovementResult(
                improved_markdown="# Page 1",
                confidence_score=0.95,
                processing_notes="High confidence"
            ),
            PageImprovementResult(
                improved_markdown="# Page 2",
                confidence_score=0.75,
                processing_notes="Medium confidence"
            ),
            PageImprovementResult(
                improved_markdown="# Page 3",
                confidence_score=0.80,
                processing_notes="Medium-high confidence"
            ),
        ]
        mock_ai_enhancement.process_pages_concurrently.return_value = improvement_results

        mocker.patch(
            "src.services.processing_service.retry_with_backoff",
            side_effect=lambda func, **kwargs: func()
        )

        # Act
        result = await processing_service.process_document(payload)

        # Assert - Confidence is average: (0.95 + 0.75 + 0.80) / 3 = 0.833
        assert result.confidence_score == pytest.approx(0.833, abs=0.01)
        assert result.confidence_level == "medium"  # 0.833 is in medium range (0.60-0.84)

class TestInitialization:
    """Tests for ProcessingService initialization."""

    def test_init_with_custom_dependencies(
        self, mock_storage, mock_queue, mock_job_service,
        mock_pdf_converter, mock_ai_enhancement
    ):
        """Test custom dependencies injected correctly."""
        # Act
        service = ProcessingService(
            storage_service=mock_storage,
            queue_service=mock_queue,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
            ai_enhancement=mock_ai_enhancement
        )

        # Assert
        assert service.storage_service is mock_storage
        assert service.queue_service is mock_queue
        assert service.job_service is mock_job_service
        assert service.pdf_converter is mock_pdf_converter
        assert service.ai_enhancement is mock_ai_enhancement

    def test_init_creates_default_dependencies(
        self, mock_storage, mock_queue, mock_job_service
    ):
        """Test default PDFConverter and AIEnhancementService created if not provided."""
        # Act
        service = ProcessingService(
            storage_service=mock_storage,
            queue_service=mock_queue,
            job_service=mock_job_service
            # No pdf_converter or ai_enhancement
        )

        # Assert
        from src.services.pdf_converter import PDFConverter
        from src.services.ai_enhancement_service import AIEnhancementService

        assert isinstance(service.pdf_converter, PDFConverter)
        assert isinstance(service.ai_enhancement, AIEnhancementService)
```

### 7.2 AIEnhancementService Concurrency Test

```python
# tests/services/test_ai_enhancement_service.py

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock
from src.services.ai_enhancement_service import (
    AIEnhancementService,
    PageProcessingError
)
from src.services.pdf_converter import PageData
from src.agents.accessibility_agent import PageImprovementResult

class TestProcessPagesConcurrently:
    """Tests for concurrent page processing with semaphore limiting."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_processing(self, mock_agent):
        """Test semaphore enforces max_concurrent_pages limit."""
        # Arrange - max_concurrent_pages=2
        ai_service = AIEnhancementService(max_concurrent_pages=2, agent=mock_agent)

        pages = [
            PageData(page_num=i, markdown=f"# Page {i}", image_base64="base64")
            for i in range(1, 6)  # 5 pages
        ]

        # Track concurrent calls
        concurrent_calls = []
        max_concurrent = 0

        async def track_concurrent_calls(*args, **kwargs):
            """Track how many calls are running concurrently."""
            concurrent_calls.append(1)
            current = len(concurrent_calls)
            nonlocal max_concurrent
            max_concurrent = max(max_concurrent, current)

            await asyncio.sleep(0.1)  # Simulate AI processing

            concurrent_calls.pop()
            return PageImprovementResult(
                improved_markdown=f"# Improved Page {kwargs['page_num']}",
                confidence_score=0.85,
                processing_notes="Done"
            )

        mock_agent.process_page = track_concurrent_calls

        # Act
        results = await ai_service.process_pages_concurrently(pages)

        # Assert
        assert len(results) == 5  # All pages processed
        assert max_concurrent <= 2  # Semaphore limited to 2
        assert all(r.confidence_score == 0.85 for r in results)

    @pytest.mark.asyncio
    async def test_one_page_failure_fails_entire_batch(self, mock_agent):
        """Test that one page failure propagates to entire batch."""
        # Arrange
        ai_service = AIEnhancementService(max_concurrent_pages=5, agent=mock_agent)

        pages = [
            PageData(page_num=1, markdown="# Page 1", image_base64="base64"),
            PageData(page_num=2, markdown="# Page 2", image_base64="base64"),
            PageData(page_num=3, markdown="# Page 3", image_base64="base64"),
        ]

        # Configure page 2 to fail after retries
        call_count = 0

        async def mixed_results(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            if kwargs['page_num'] == 2:
                # Fail page 2 on all attempts
                raise Exception("Page 2 processing failed")

            return PageImprovementResult(
                improved_markdown=f"# Success {kwargs['page_num']}",
                confidence_score=0.88,
                processing_notes="OK"
            )

        mock_agent.process_page = mixed_results

        # Act & Assert
        with pytest.raises(PageProcessingError) as exc_info:
            await ai_service.process_pages_concurrently(pages)

        assert exc_info.value.page_num == 2
        assert "Page 2 processing failed" in str(exc_info.value.original_error)
```

### 7.3 AccessibilityAgent Prompt Loading Test

```python
# tests/agents/test_accessibility_agent.py

import pytest
from unittest.mock import Mock, mock_open
from src.agents.accessibility_agent import AccessibilityAgent

class TestInitialization:
    """Tests for AccessibilityAgent initialization and prompt loading."""

    def test_prompts_loaded_from_yaml_file(self, mocker):
        """Test prompts loaded from config/accessibility_prompts.yaml."""
        # Arrange
        mock_yaml_data = {
            "system_prompt": "You are an accessibility expert improving PDFs.",
            "user_prompt_template": "Improve this markdown:\n\n{markdown}"
        }

        # Mock file operations
        m_open = mocker.patch(
            "builtins.open",
            mock_open(read_data="system_prompt: Test\nuser_prompt_template: Test")
        )
        mocker.patch("yaml.safe_load", return_value=mock_yaml_data)

        # Mock PydanticAI Agent initialization
        mock_agent_class = mocker.patch("pydantic_ai.Agent")
        mock_agent_instance = Mock()
        mock_agent_class.return_value = mock_agent_instance

        # Act
        agent = AccessibilityAgent()

        # Assert - Prompts loaded
        assert agent.system_prompt == "You are an accessibility expert improving PDFs."
        assert agent.user_prompt_template == "Improve this markdown:\n\n{markdown}"

        # Assert - YAML file opened
        m_open.assert_called_once_with("config/accessibility_prompts.yaml", "r")

    def test_fallback_prompts_when_yaml_missing(self, mocker):
        """Test fallback prompts used when YAML file not found."""
        # Arrange - Mock file not found
        mocker.patch("builtins.open", side_effect=FileNotFoundError)

        # Mock PydanticAI Agent
        mock_agent_class = mocker.patch("pydantic_ai.Agent")
        mock_agent_instance = Mock()
        mock_agent_class.return_value = mock_agent_instance

        # Mock logger to verify warning
        mock_logger = mocker.patch("src.agents.accessibility_agent.logger")

        # Act
        agent = AccessibilityAgent()

        # Assert - Fallback prompts used
        assert "accessibility" in agent.system_prompt.lower()
        assert "markdown" in agent.user_prompt_template.lower()

        # Assert - Warning logged
        mock_logger.warning.assert_called_once()
        assert "YAML" in str(mock_logger.warning.call_args)
```

---

## 8. Migration Plan (3 Weeks)

### Week 1: Critical Path (Days 1-5)

**Objective:** Test core orchestration and AI processing logic

**Day 1-2: ProcessingService (20 unit tests)**
- Priority: CRITICAL
- Blocks: All pipeline testing
- Deliverable: Complete test coverage for main orchestrator
- Files: `tests/services/test_processing_service.py`

**Day 3-4: AIEnhancementService (18 unit tests)**
- Priority: CRITICAL
- Blocks: Concurrent processing validation
- Deliverable: Retry/semaphore logic validated
- Files: `tests/services/test_ai_enhancement_service.py`

**Day 5: PDFConverter (15 unit tests)**
- Priority: CRITICAL
- Blocks: Docling integration validation
- Deliverable: Page extraction and image validation
- Files: `tests/services/test_pdf_converter.py`

**Week 1 Exit Criteria:**
- 53 tests written and passing
- 60% of critical path covered
- CI pipeline green for unit tests

---

### Week 2: Supporting Components (Days 6-10)

**Objective:** Complete unit test coverage for all components

**Day 6-7: AccessibilityAgent (12 unit tests)**
- Priority: HIGH
- Blocks: AI agent validation
- Deliverable: Prompt loading, structured output tested
- Files: `tests/agents/test_accessibility_agent.py`

**Day 8-9: ProcessingWorker (14 unit tests)**
- Priority: HIGH
- Blocks: Background task validation
- Deliverable: Worker lifecycle and graceful shutdown tested
- Files: `tests/workers/test_processing_worker.py`

**Day 10: confidence_scoring (8 unit tests)**
- Priority: MEDIUM
- Blocks: Quality metrics validation
- Deliverable: Scoring logic validated
- Files: `tests/utils/test_confidence_scoring.py`

**Week 2 Exit Criteria:**
- 87 total unit tests passing
- 90%+ unit test coverage for core pipeline
- All public methods tested

---

### Week 3: Integration & E2E (Days 11-15)

**Objective:** Validate multi-component interactions and full pipeline

**Day 11-12: Integration Tests (19 tests)**
- `tests/integration/workflows/test_processing_pipeline.py` (5 tests)
- `tests/integration/workflows/test_pdf_conversion.py` (3 tests)
- `tests/integration/workflows/test_concurrent_ai.py` (4 tests)
- `tests/integration/workflows/test_agent_prompts.py` (3 tests)
- `tests/integration/workflows/test_worker_lifecycle.py` (4 tests)

**Day 13: E2E Tests (3 tests)**
- `tests/e2e/test_full_pipeline.py` (1 test)
- `tests/e2e/test_error_recovery.py` (1 test)
- `tests/e2e/test_concurrent_documents.py` (1 test)

**Day 14: Test Infrastructure**
- Configure pytest markers (`@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`)
- Set up coverage reporting (pytest-cov)
- Update CI/CD pipeline for test execution
- Create test data fixtures (sample PDFs)

**Day 15: Documentation & Review**
- Update testing documentation
- Create test execution guide
- Code review of all tests
- Performance optimization for slow tests

**Week 3 Exit Criteria:**
- 109 total tests passing (87 unit + 19 integration + 3 E2E)
- Coverage reports show >90% for core modules
- CI/CD pipeline includes all test suites
- Documentation updated

---

## 9. Success Metrics

### Coverage Goals

**Unit Test Coverage (Target: 90%+):**
- `src/services/processing_service.py`: 95%
- `src/services/pdf_converter.py`: 92%
- `src/services/ai_enhancement_service.py`: 93%
- `src/agents/accessibility_agent.py`: 88%
- `src/workers/processing_worker.py`: 90%
- `src/utils/confidence_scoring.py`: 100%

**Integration Test Coverage (Target: 75%+):**
- Multi-component workflows: 80%
- Redis/S3 interactions: 85%
- Worker lifecycle: 75%

**E2E Coverage (Target: 100% of critical flows):**
- Complete processing pipeline: ✓
- Error recovery: ✓
- Concurrent processing: ✓

### Quality Gates (CI/CD)

**Pre-Merge Checks:**
```yaml
# .github/workflows/test.yml
- name: Unit Tests
  run: pytest tests/services/ tests/agents/ tests/workers/ tests/utils/ -v

- name: Coverage Check
  run: |
    pytest --cov=src/services/processing_service \
           --cov=src/services/pdf_converter \
           --cov=src/services/ai_enhancement_service \
           --cov=src/agents/accessibility_agent \
           --cov=src/workers/processing_worker \
           --cov-report=term-missing \
           --cov-fail-under=90

- name: Integration Tests
  run: pytest tests/integration/ -v --integration

- name: E2E Tests (on main branch only)
  if: github.ref == 'refs/heads/main'
  run: pytest tests/e2e/ -v --e2e
```

**Blocking Criteria:**
- Any test failure blocks merge
- Coverage below 90% blocks merge
- Integration test failure blocks deploy
- E2E test failure blocks production deploy

### Performance Benchmarks

**Test Execution Time:**
- Unit tests: < 30 seconds total
- Integration tests: < 2 minutes total
- E2E tests: < 5 minutes total
- Full test suite: < 8 minutes total

**Monitoring Commands:**
```bash
# Run tests with timing
pytest -v --durations=10

# Run with coverage
pytest --cov=src --cov-report=html --cov-report=term

# Run specific test suites
pytest -m unit -v          # Unit tests only
pytest -m integration -v   # Integration tests only
pytest -m e2e -v           # E2E tests only

# Run performance benchmarks
pytest --benchmark-only
```

---

## 10. Definition of Done

### Task Completion Checklist

**Development:**
- [ ] All 87 unit tests written and passing
- [ ] All 19 integration tests written and passing
- [ ] All 3 E2E tests written and passing
- [ ] Coverage reports show >90% for core modules
- [ ] No flaky tests (100% pass rate on 10 consecutive runs)

**Infrastructure:**
- [ ] pytest markers configured (`unit`, `integration`, `e2e`)
- [ ] Coverage reporting configured (pytest-cov)
- [ ] CI/CD pipeline updated with test stages
- [ ] Test data fixtures created (sample PDFs, YAML files)
- [ ] Docker test environment validated

**Integration:**
- [ ] Integration tests use real Redis (from docker-compose)
- [ ] Integration tests use real LocalStack S3
- [ ] E2E tests validate complete workflow
- [ ] Mock strategies documented

**Documentation:**
- [ ] Test execution guide created
- [ ] Mock fixture documentation written
- [ ] Test architecture diagram updated
- [ ] Coverage reports published

**Code Quality:**
- [ ] All tests follow pytest best practices
- [ ] Fixtures properly scoped (function/module/session)
- [ ] No duplicate test logic (DRY principle)
- [ ] Clear test names (test_<what>_<condition>_<expected>)
- [ ] Assertions include helpful error messages

**Review:**
- [ ] Code review completed by 2+ developers
- [ ] Test coverage validated by QA team
- [ ] Performance benchmarks met
- [ ] No regressions in existing tests

---

## 11. Risk Assessment

### High-Risk Scenarios (Mitigated by Tests)

| Scenario | Current Risk | Test Mitigation | Residual Risk |
|----------|-------------|-----------------|---------------|
| Page images not generated | CRITICAL | Unit test validates `has_page_images=True` | LOW |
| AI retry logic fails | CRITICAL | Unit tests verify exponential backoff | LOW |
| Semaphore misconfigured | HIGH | Integration test tracks concurrent calls | LOW |
| Worker shutdown loses jobs | HIGH | Unit test verifies job requeue | LOW |
| Confidence scoring bug | MEDIUM | Unit tests verify thresholds/averages | VERY LOW |
| S3 upload failure | MEDIUM | Unit test verifies retry logic | LOW |

### Testing Risks

**Risk: Flaky Tests**
- Mitigation: Use deterministic mocks, avoid real time delays
- Strategy: Run tests 10 times before merge

**Risk: Slow Integration Tests**
- Mitigation: Optimize Redis/S3 cleanup, use fixtures
- Strategy: Parallel test execution (pytest-xdist)

**Risk: E2E Tests Too Brittle**
- Mitigation: Mock only Claude API, use real infrastructure
- Strategy: Retry logic for transient failures

**Risk: Mock Divergence**
- Mitigation: Integration tests validate real behavior
- Strategy: Regular mock validation against real services

---

## 12. Appendix

### A. Test Execution Examples

**Run all tests:**
```bash
make test-docker
```

**Run specific suites:**
```bash
# Unit tests only
pytest -m unit -v

# Integration tests only
pytest -m integration -v

# E2E tests only
pytest -m e2e -v
```

**Run with coverage:**
```bash
pytest --cov=src --cov-report=html --cov-report=term-missing
```

**Run specific component:**
```bash
pytest tests/services/test_processing_service.py -v
```

**Run with debugging:**
```bash
pytest tests/services/test_processing_service.py::test_process_document_success -vv -s
```

### B. Mock Fixture Reference

**Core Service Mocks:**
```python
# Use these fixtures in test files
mock_storage_service
mock_queue_service
mock_job_service
mock_pdf_converter
mock_ai_enhancement
mock_pydantic_agent
```

**Real Infrastructure Fixtures:**
```python
# Use these for integration tests
real_redis_client
real_s3_client
real_storage_service
real_queue_service
```

### C. Coverage Reporting

**Generate HTML report:**
```bash
pytest --cov=src --cov-report=html
open htmlcov/index.html
```

**Terminal report:**
```bash
pytest --cov=src --cov-report=term-missing
```

**Fail if coverage below threshold:**
```bash
pytest --cov=src --cov-fail-under=90
```

### D. Related Documents

- [Research Document: Core Pipeline Test Analysis](/Users/dylanisaac/Projects/equalify-pdf-converter/ai-docs/research/core-pipeline-test-analysis.md)
- [STORY-007: CI/CD Test Automation](/Users/dylanisaac/Projects/equalify-pdf-converter/ai-docs/PRDs/phase-2-services/STORY-007-ci-cd-test-automation.md)
- [STORY-008: Test Coverage Reporting](/Users/dylanisaac/Projects/equalify-pdf-converter/ai-docs/PRDs/phase-2-services/STORY-008-test-coverage-reporting.md)
- [BUG-006: Test Suite Failures](/Users/dylanisaac/Projects/equalify-pdf-converter/ai-docs/PRDs/phase-2-services/BUG-006-test-suite-failures.md)

---

**End of PRD**
