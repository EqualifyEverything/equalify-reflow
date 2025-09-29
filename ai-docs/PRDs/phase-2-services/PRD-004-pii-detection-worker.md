# PRD-004: PII Detection Worker

## Overview
**Epic**: MVP PDF Converter PII Security Layer
**Phase**: 2 - Core Services
**Estimated Effort**: 2 days
**Dependencies**: PRD-001 (Infrastructure), PRD-002 (Data Models)
**Parallel**: ✅ Independent service

## Problem Statement
The system requires a dedicated worker service that processes uploaded PDFs for Personally Identifiable Information (PII) using Microsoft Presidio. Documents with no PII proceed directly to processing, while documents with detected PII are queued for human approval with detailed findings.

## Success Criteria
- [ ] Microsoft Presidio integration for PII detection
- [ ] Queue processing from PII queue to approval/processing queues
- [ ] Configurable PII detection rules and thresholds
- [ ] Detailed PII findings with location and confidence
- [ ] Proper error handling and retry logic
- [ ] Performance metrics and monitoring

## Technical Requirements

### PII Detection Integration

#### Presidio Configuration
```python
# Supported PII Entity Types
ENTITY_TYPES = [
    "PERSON",           # Names
    "EMAIL_ADDRESS",    # Email addresses
    "PHONE_NUMBER",     # Phone numbers
    "SSN",              # Social Security Numbers
    "CREDIT_CARD",      # Credit card numbers
    "IBAN_CODE",        # Bank account numbers
    "US_DRIVER_LICENSE", # Driver's license numbers
    "DATE_TIME",        # Specific dates (optional)
    "LOCATION",         # Addresses (optional)
]

# Confidence Thresholds
PII_CONFIDENCE_THRESHOLD = 0.7  # Minimum confidence to flag as PII
```

#### Document Processing Pipeline
```python
async def process_pii_job(job: PIIQueuePayload):
    """Main PII processing pipeline"""
    try:
        # 1. Download PDF from S3
        pdf_content = await download_from_s3(job.s3_key)

        # 2. Extract text content (Presidio requires text, not PDF)
        # integrate with existing Docling dependency
        text_content = extract_pdf_text(pdf_content)

        # 3. Run Presidio analysis on extracted text
        pii_findings = await analyze_pii_with_presidio(text_content)

        # 4. Route based on findings
        if pii_findings:
            await queue_for_approval(job, pii_findings)
        else:
            await queue_for_processing(job)

    except Exception as e:
        await mark_job_failed(job.job_id, str(e))
```

### Queue Processing Logic

#### Worker Main Loop
```python
async def pii_worker_main():
    """Main worker loop processing PII queue"""
    while True:
        try:
            # Blocking pop from PII queue (30 second timeout)
            job_data = await redis.blpop(PII_QUEUE, timeout=30)

            if job_data:
                job = PIIQueuePayload.parse_raw(job_data[1])
                await process_pii_job(job)

        except Exception as e:
            logger.error(f"Worker error: {e}")
            await asyncio.sleep(5)  # Brief pause before retry
```

#### Routing Logic
```python
async def queue_for_approval(job: PIIQueuePayload, pii_findings: List[PIIFinding]):
    """Queue job for human approval with PII details"""

    # Generate secure approval token
    approval_token = generate_secure_token()
    expires_at = datetime.utcnow() + timedelta(hours=4)

    # Create approval queue payload
    approval_payload = ApprovalQueuePayload(
        job_id=job.job_id,
        s3_key=job.s3_key,
        pii_findings=pii_findings,
        approval_token=approval_token,
        expires_at=expires_at
    )

    # Queue for approval
    await redis.lpush(APPROVAL_QUEUE, approval_payload.json())

    # Add to timeout tracking
    await redis.zadd(APPROVAL_TIMEOUTS, {job.job_id: expires_at.timestamp()})

    # Update job status
    await update_job_status(
        job.job_id,
        "awaiting_approval",
        pii_findings=pii_findings,
        approval_token=approval_token,
        expires_at=expires_at
    )

async def queue_for_processing(job: PIIQueuePayload):
    """Queue clean job directly for processing"""
    processing_payload = ProcessingQueuePayload(
        job_id=job.job_id,
        s3_key=job.s3_key,
        approved_at=None  # No approval needed
    )

    await redis.lpush(PROCESSING_QUEUE, processing_payload.json())
    await update_job_status(job.job_id, "processing")
```

## Acceptance Criteria

### 1. PII Detection
- [ ] Presidio analyzer configured with required entity types
- [ ] Text extraction from PDF files working
- [ ] PII findings include entity type, location, and confidence
- [ ] Configurable confidence thresholds
- [ ] Custom rules for false positive reduction

### 2. Queue Processing
- [ ] Processes jobs from PII queue continuously
- [ ] Routes clean documents to processing queue
- [ ] Routes PII documents to approval queue
- [ ] Handles queue errors and empty queue gracefully
- [ ] Worker restarts automatically on failure

### 3. Job Status Management
- [ ] Updates Redis job status appropriately
- [ ] Stores PII findings in job record
- [ ] Creates approval tokens for PII documents
- [ ] Sets proper expiration times
- [ ] Handles concurrent job processing

### 4. Error Handling
- [ ] Retries failed PII scans once
- [ ] Marks jobs as failed after retry attempts
- [ ] Logs detailed error information
- [ ] Continues processing other jobs on individual failures
- [ ] Handles PDF parsing errors gracefully

### 5. Performance
- [ ] Processes typical documents in <30 seconds
- [ ] Handles concurrent job processing
- [ ] Memory usage stays reasonable for large PDFs
- [ ] Scales with multiple worker instances

## Deliverables

### Files to Create
```
/services/pii-worker/
├── Dockerfile                     # Container definition
├── pyproject.toml                 # UV project configuration
├── uv.lock                        # UV lock file

├── app/
│   ├── main.py                   # Worker main loop
│   ├── config.py                 # Configuration
│   ├── services/
│   │   ├── pii_analyzer.py       # Presidio integration
│   │   ├── pdf_extractor.py      # PDF text extraction
│   │   ├── queue_service.py      # Redis operations
│   │   └── storage_service.py    # S3 operations
│   ├── models/
│   │   └── pii_models.py         # PII-specific data models
│   └── utils/
│       ├── token_generator.py    # Secure token generation
│       └── text_processing.py    # Text preprocessing
├── tests/
│   ├── test_pii_detection.py     # PII detection tests
│   ├── test_queue_processing.py  # Queue logic tests
│   └── test_pdf_extraction.py    # PDF parsing tests
├── config/
│   └── presidio_config.yaml      # Presidio configuration
└── docs/
    └── pii_detection.md          # Documentation
```
### Container Configuration
```dockerfile
FROM python:3.12-slim

# Install system dependencies for PDF processing
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# Download Presidio models
RUN uv run python -m spacy download en_core_web_sm

COPY app/ ./app/
COPY config/ ./config/

CMD ["uv", "run", "python", "app/main.py"]
```

## Technical Notes

### Presidio Configuration
```python
# Presidio analyzer setup
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

# Configure NLP engine
nlp_configuration = {
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]
}

nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()

# Create analyzer
analyzer = AnalyzerEngine(nlp_engine=nlp_engine)

def analyze_pii_with_presidio(text: str) -> List[PIIFinding]:
    """Analyze text for PII using Presidio"""
    analyzer_results = analyzer.analyze(text=text, language='en')

    # Convert Presidio results to our PIIFinding model
    return [
        PIIFinding(
            entity_type=result.entity_type,
            start=result.start,
            end=result.end,
            score=result.score,
            text=text[result.start:result.end]
        )
        for result in analyzer_results
        if result.score > PII_CONFIDENCE_THRESHOLD
    ]
```

### Text Extraction
```python
# PDF text extraction with error handling
async def extract_pdf_text(pdf_content: bytes) -> str:
    """Extract text from PDF with fallback strategies"""
    try:
        # Primary: PyPDF2 extraction
        text = extract_with_pypdf2(pdf_content)

        if len(text.strip()) < 100:  # Likely OCR-only document
            # Fallback: OCR with Tesseract
            text = await extract_with_ocr(pdf_content)

        return text

    except Exception as e:
        logger.error(f"PDF text extraction failed: {e}")
        raise PDFExtractionError(f"Unable to extract text from PDF: {e}")
```

### Security Token Generation
```python
# Secure token generation for approval URLs
def generate_secure_token() -> str:
    """Generate cryptographically secure approval token"""
    return secrets.token_urlsafe(32)  # 256-bit security

def create_approval_url(token: str) -> str:
    """Create approval URL for frontend"""
    base_url = os.getenv("APPROVAL_BASE_URL", "http://localhost:3000")
    return f"{base_url}/approve/{token}"
```

### Environment Configuration
```python
# Environment variables required
REDIS_URL=redis://redis:6379
AWS_ENDPOINT_URL=http://localstack:4566
S3_TEMP_BUCKET=equalify-temp

# Queue names
PII_QUEUE_NAME=eq-pdf:queue:pii
APPROVAL_QUEUE_NAME=eq-pdf:queue:approval
PROCESSING_QUEUE_NAME=eq-pdf:queue:processing

# PII Detection settings
PII_CONFIDENCE_THRESHOLD=0.7
HIGH_RISK_THRESHOLD=0.9
APPROVAL_TIMEOUT_HOURS=4

# Worker settings
WORKER_CONCURRENCY=2
QUEUE_TIMEOUT_SECONDS=30
```

## Definition of Done
- [ ] PII detection working with Presidio
- [ ] Worker processes queue continuously
- [ ] Proper routing based on PII findings
- [ ] Container builds and runs successfully
- [ ] Integration tests with Redis and S3 pass
- [ ] Error handling covers all edge cases
- [ ] Documentation complete and accurate
- [ ] Service ready for approval service integration