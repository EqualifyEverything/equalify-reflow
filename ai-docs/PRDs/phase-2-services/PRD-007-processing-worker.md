# PRD-007: Background Processing Worker

## Overview
**Epic**: MVP PDF Converter AI Processing Worker
**Phase**: 2 - Core Modules
**Estimated Effort**: 4 days
**Dependencies**:
- PRD-001 (Infrastructure)
- PRD-002 (Data Models)
- **PRD-003 (Shared Services) - REQUIRED** - Must be complete before starting this worker

## Problem Statement
The monolith application requires a **background worker thread** that processes approved documents through an AI pipeline. This worker monitors the `eq-pdf:queue:processing` Redis queue, handles Docling PDF→Markdown conversion, performs AI accessibility enhancement, stores results in S3, and updates job status.

**Architecture Note:** This is a **background worker thread** running within the same Python process as the FastAPI API, not a separate microservice. It shares storage_service, queue_service, and job_service from PRD-003 with the API and other workers.

## Success Criteria
- [ ] Docling PDF→Markdown conversion working
- [ ] Single Anthropic Claude agent implemented for page-by-page processing
- [ ] Uses shared services from PRD-003 (storage_service, queue_service, job_service)
- [ ] Semantic markdown from Claude agent with visual comparison produced
- [ ] S3 results storage for versioned static resources
- [ ] Processing time: 2-8 minutes for typical documents
- [ ] Confidence scoring: >85% High, 60-85% Medium, <60% Low
- [ ] Structure accuracy: ≥90% proper heading hierarchy preservation

## Shared Service Dependencies
This worker imports and uses the following shared services built in PRD-003:

- **storage_service.download_from_s3()** - Downloads PDF files from S3 temp bucket
- **storage_service.upload_to_s3()** - Uploads processed HTML/MDX to S3 results bucket
- **queue_service.get_processing_job()** - Pops jobs from processing queue
- **queue_service.monitor_processing_queue()** - Blocking queue operations
- **job_service.update_job_status()** - Updates job status through processing pipeline
- **job_service.mark_job_failed()** - Handles failed processing jobs

These services MUST be implemented in PRD-003 before this worker can be developed.

## Technical Requirements

### PDF to Markdown Conversion

#### Docling Integration with Page-by-Page Processing
```python
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import PdfFormatOption
import base64
from io import BytesIO

async def convert_pdf_with_page_images(pdf_content: bytes) -> DoclingConversionResult:
    """Convert PDF to structured document with page images using Docling"""
    try:
        # Initialize converter with page image generation enabled
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=PdfPipelineOptions(
                        enable_ocr=True,
                        ocr_engine="tesseract",
                        extract_images=True,
                        preserve_layout=True,
                        table_structure_detection=True,
                        generate_page_images=True,  # Enable page image generation
                        images_scale=2.0,  # High resolution for AI analysis
                        generate_picture_images=True
                    )
                )
            }
        )

        # Convert PDF to Docling document
        conversion_result = converter.convert(pdf_content)

        if conversion_result.status == ConversionStatus.SUCCESS:
            doc = conversion_result.document

            # Extract page-by-page content with images
            pages_data = []
            for page_idx, page in enumerate(doc.pages):
                # Get page text content
                page_text = ""
                for element in page.elements:
                    page_text += element.text + "\n"

                # Get page image as base64 for Claude API
                page_image_base64 = None
                if hasattr(page, 'image') and page.image:
                    pil_image = page.image.pil_image
                    buffer = BytesIO()
                    pil_image.save(buffer, format='PNG')
                    page_image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

                # Get page-specific markdown
                page_markdown = ""
                for element in page.elements:
                    if hasattr(element, 'export_to_markdown'):
                        page_markdown += element.export_to_markdown() + "\n"

                pages_data.append({
                    "page_number": page_idx + 1,
                    "extracted_text": page_text.strip(),
                    "page_markdown": page_markdown.strip(),
                    "page_image_base64": page_image_base64,
                    "tables": [table for table in doc.tables if table.page_no == page_idx + 1],
                    "images": [img for img in doc.images if img.page_no == page_idx + 1]
                })

            return DoclingConversionResult(
                document=doc,
                full_markdown_content=doc.export_to_markdown(),
                pages_data=pages_data,
                total_pages=len(doc.pages),
                confidence_score=getattr(conversion_result, 'confidence_score', 0.8)
            )
        else:
            raise ConversionError(f"PDF conversion failed: {conversion_result.error}")

    except Exception as e:
        logger.error(f"Docling conversion error: {e}")
        raise ConversionError(f"PDF to markdown conversion failed: {e}")

class DoclingConversionResult(BaseModel):
    document: Any  # Docling Document object
    full_markdown_content: str
    pages_data: List[Dict[str, Any]]
    total_pages: int
    confidence_score: float
```

### Single-Agent AI Processing Pipeline

#### Anthropic Claude Agent Architecture
```python
from anthropic import Anthropic
from pathlib import Path
from PIL import Image
import base64
import io

# Anthropic Claude configuration
CLAUDE_MODEL = "claude-3-5-sonnet-20241022"
CLAUDE_MAX_TOKENS = 8192
CLAUDE_TEMPERATURE = 0.2

class PageProcessingContext(BaseModel):
    job_id: str
    original_filename: str
    page_number: int
    extracted_text: str
    page_image_base64: str
    page_markdown: str
    confidence_score: float

class ProcessingContext(BaseModel):
    job_id: str
    original_filename: str
    total_pages: int
    docling_document: Any  # Full Docling document object
    overall_confidence_score: float

# Single Claude agent for page-by-page processing
class DocumentAccessibilityAgent:
    def __init__(self):
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.system_prompt = """
You are an expert accessibility specialist who transforms PDF documents into accessible HTML.
You will be given both the extracted text content from a PDF page AND a visual image of that same page.

Your job is to:
1. Compare the extracted text to the visual presentation to identify discrepancies
2. Fix heading hierarchies to match the visual structure (h1, h2, h3, etc.)
3. Generate comprehensive alt text for images, charts, and diagrams
4. Convert mathematical expressions to proper MathML format
5. Ensure proper semantic markup for tables, lists, and other structures

Provide your response as semantic markdown.
Include a confidence score (0-1) indicating how well you could process this page.
"""

    async def process_page(self, context: PageProcessingContext) -> Dict[str, Any]:
        """Process a single page with visual comparison"""

        user_message = f"""
        Page {context.page_number} of document: {context.original_filename}

        EXTRACTED TEXT:
        {context.extracted_text}

        CURRENT MARKDOWN:
        {context.page_markdown}

        Please analyze the visual image and improve the markdown to be more accessible and semantically correct.
        Focus on:
        - Heading hierarchy that matches visual structure
        - Alt text for any images or visual elements
        - Proper table markup if tables are present
        - Mathematical notation converted to MathML
        - Accessibility improvements

        Return improved markdown and a confidence score.
        """

        message = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": user_message
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": context.page_image_base64
                    }
                }
            ]
        }

        response = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            temperature=CLAUDE_TEMPERATURE,
            system=self.system_prompt,
            messages=[message]
        )

        # Parse response for improved markdown and confidence score
        response_text = response.content[0].text

        # Extract confidence score from response (expect format like "Confidence: 0.85")
        confidence = 0.8  # Default fallback
        if "Confidence:" in response_text:
            try:
                confidence_line = [line for line in response_text.split('\n') if 'Confidence:' in line][0]
                confidence = float(confidence_line.split(':')[1].strip())
            except (IndexError, ValueError):
                pass

        return {
            "improved_markdown": response_text,
            "confidence_score": confidence,
            "page_number": context.page_number
        }
```

#### Processing Pipeline Implementation
```python
async def process_document(job: ProcessingQueuePayload) -> ProcessingResult:
    """Main document processing pipeline with page-by-page Claude processing"""
    start_time = time.time()
    try:
        # 1. Download PDF from S3
        pdf_content = await download_from_s3(job.s3_key)

        # 2. Convert PDF to Docling document with page images
        conversion_result = await convert_pdf_with_page_images(pdf_content)

        # 3. Create main processing context
        context = ProcessingContext(
            job_id=job.job_id,
            original_filename=job.original_filename,
            total_pages=conversion_result.total_pages,
            docling_document=conversion_result.document,
            overall_confidence_score=conversion_result.confidence_score
        )

        # 4. Initialize Claude agent
        claude_agent = DocumentAccessibilityAgent()

        # 5. Process each page with visual comparison
        processed_pages = []
        page_confidence_scores = []

        for page_data in conversion_result.pages_data:
            logger.info(f"Job {job.job_id}: Processing page {page_data['page_number']}/{context.total_pages}")

            page_context = PageProcessingContext(
                job_id=job.job_id,
                original_filename=job.original_filename,
                page_number=page_data['page_number'],
                extracted_text=page_data['extracted_text'],
                page_image_base64=page_data['page_image_base64'],
                page_markdown=page_data['page_markdown'],
                confidence_score=0.8  # Initial confidence
            )

            # Process page with Claude agent
            page_result = await claude_agent.process_page(page_context)
            processed_pages.append(page_result)
            page_confidence_scores.append(page_result['confidence_score'])

        # 6. Combine processed pages into final document
        combined_markdown = combine_processed_pages(processed_pages)
        final_confidence = sum(page_confidence_scores) / len(page_confidence_scores) if page_confidence_scores else 0.0

        # 7. Generate final HTML and MDX
        html_content = await render_to_html(combined_markdown, context)
        mdx_content = await render_to_mdx(combined_markdown, context)

        # 8. Store results in S3
        urls = await store_results_in_s3(job.job_id, html_content, mdx_content)

        # 9. Update job status
        result = ProcessingResult(
            job_id=job.job_id,
            html_url=urls.html_url,
            mdx_url=urls.mdx_url,
            confidence_score=final_confidence,
            processing_time_seconds=int(time.time() - start_time)
        )

        await update_job_status(job.job_id, "completed", result=result)
        return result

    except Exception as e:
        logger.error(f"Processing failed for job {job.job_id}: {e}")
        await mark_job_failed(job.job_id, str(e))
        raise ProcessingError(f"Document processing failed: {e}")

def combine_processed_pages(processed_pages: List[Dict[str, Any]]) -> str:
    """Combine page-level processed markdown into final document"""
    combined_content = []

    for page_result in processed_pages:
        page_num = page_result['page_number']
        improved_markdown = page_result['improved_markdown']

        # Add page separator and content
        combined_content.append(f"<!-- Page {page_num} -->\n")
        combined_content.append(improved_markdown)
        combined_content.append("\n\n")

    return "\n".join(combined_content)

# Note: The multi-agent pipeline has been replaced with the single Claude agent
# processing approach implemented in the process_document function above.
# Each page is processed individually with visual comparison capabilities.

class ProcessedDocument(BaseModel):
    content: str
    final_confidence_score: float
    page_count: int
    processing_notes: List[str]
```

### Queue Processing Logic

#### Worker Main Loop (Background Thread)
```python
# workers/processing_worker.py
import asyncio
from redis import Redis
from ..services import ProcessingService, JobService
from ..shared.constants import PROCESSING_QUEUE

async def processing_worker_main():
    """
    Background worker thread that monitors Redis queue for processing jobs.

    Runs in the same Python process as FastAPI API.
    Started by src/main.py at application startup.
    """
    logger.info("Processing worker started")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    while True:
        try:
            # Blocking pop from processing queue (60 second timeout)
            job_data = redis.blpop(PROCESSING_QUEUE, timeout=60)

            if job_data:
                job = ProcessingQueuePayload.parse_raw(job_data[1])
                logger.info(f"Processing worker picked up job {job.job_id}")

                await update_job_status(job.job_id, "processing")
                await process_document(job)

        except Exception as e:
            logger.error(f"Processing worker error: {e}")
            await asyncio.sleep(10)

# Note: This worker runs as a background thread started by main.py,
# not as a separate microservice or container.

class ProcessingResult(BaseModel):
    job_id: str
    html_url: Optional[str]
    mdx_url: Optional[str]
    confidence_score: float
    processing_time_seconds: int
    error_message: Optional[str] = None
```

### S3 Results Storage

#### Storage Service
```python
async def store_results_in_s3(job_id: str, html_content: str, mdx_content: str) -> ResultURLs:
    """Store processed results in S3 with versioning"""
    try:
        # Generate versioned S3 keys
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        html_key = f"results/{job_id}/v{timestamp}/index.html"
        mdx_key = f"results/{job_id}/v{timestamp}/document.mdx"

        # Upload HTML result
        await upload_to_s3(
            bucket=RESULTS_BUCKET,
            key=html_key,
            content=html_content,
            content_type="text/html",
            metadata={
                "job-id": job_id,
                "content-type": "processed-html",
                "created-at": datetime.utcnow().isoformat()
            }
        )

        # Upload MDX source
        await upload_to_s3(
            bucket=RESULTS_BUCKET,
            key=mdx_key,
            content=mdx_content,
            content_type="text/markdown",
            metadata={
                "job-id": job_id,
                "content-type": "processed-mdx",
                "created-at": datetime.utcnow().isoformat()
            }
        )

        # Generate public URLs
        html_url = f"{S3_PUBLIC_BASE_URL}/{html_key}"
        mdx_url = f"{S3_PUBLIC_BASE_URL}/{mdx_key}"

        return ResultURLs(html_url=html_url, mdx_url=mdx_url)

    except Exception as e:
        logger.error(f"S3 storage failed for job {job_id}: {e}")
        raise StorageError(f"Failed to store results: {e}")
```

## Acceptance Criteria

### 1. PDF Conversion
- [ ] Docling integration working with OCR support
- [ ] PDF text extraction with high accuracy
- [ ] Image and table extraction preserved
- [ ] Layout and structure maintained
- [ ] Handles various PDF formats and sizes

### 2. Single-Agent Processing
- [ ] Single Anthropic Claude agent operational with visual comparison
- [ ] Page-by-page processing pipeline working
- [ ] Visual analysis comparing extracted text to page images
- [ ] Error handling for AI model failures
- [ ] Page-level confidence scoring and aggregation

### 3. Content Generation
- [ ] Semantic HTML output with proper WCAG markup
- [ ] MDX format with standardized JSX components
- [ ] MathML conversion for mathematical content
- [ ] Comprehensive alt text for all images
- [ ] Proper heading hierarchy structure

### 4. Quality Scoring
- [ ] Confidence scoring: High (>85%), Medium (60-85%), Low (<60%)
- [ ] Structure accuracy measurement
- [ ] Accessibility compliance validation
- [ ] Processing time tracking
- [ ] Error categorization and reporting

### 5. S3 Integration
- [ ] Versioned result storage
- [ ] Public URL generation for HTML access
- [ ] Metadata tagging for job tracking
- [ ] Cleanup of temporary processing files
- [ ] Static hosting compatibility

### 6. Performance
- [ ] Processing time: 2-8 minutes for typical documents
- [ ] Memory usage optimization for large PDFs
- [ ] Concurrent job processing support
- [ ] Graceful handling of processing timeouts
- [ ] Resource cleanup after processing

## Deliverables

### Files to Create
```
/src/services/
├── processing_service.py               # Main processing service module
├── pdf_converter.py                    # Docling integration
├── ai_pipeline.py                      # AI processing pipeline
├── html_renderer.py                    # HTML generation
├── mdx_renderer.py                     # MDX generation
├── processing_worker.py                # Worker main loop

/src/agents/
├── document_accessibility_agent.py     # Main Claude agent for page processing
├── quality_agent.py                    # Quality assessment

/src/utils/
├── markdown_utils.py                   # Markdown processing utilities
├── html_utils.py                       # HTML generation utilities
└── confidence_scoring.py               # Confidence calculation

/tests/services/
├── test_pdf_conversion.py              # Docling integration tests
├── test_ai_pipeline.py                 # AI pipeline tests
├── test_html_generation.py             # Rendering tests
└── test_storage.py                     # S3 operations tests

/config/
├── ai_prompts.yaml                     # Agent system prompts
└── docling_config.yaml                 # PDF conversion settings
```

### Worker Execution (Part of Monolith)
The processing worker runs as a **background asyncio task** started by src/main.py:

```python
# src/main.py
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from .workers.processing_worker import processing_worker_main

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start processing worker as background task
    asyncio.create_task(processing_worker_main())
    asyncio.create_task(pii_worker_main())
    asyncio.create_task(timeout_worker_main())
    yield

app = FastAPI(lifespan=lifespan)
```

```bash
# Start infrastructure services (Redis, LocalStack)
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Run the monolith application (starts API + all workers)
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8080

# src/main.py automatically starts:
# - FastAPI server (main thread)
# - PII worker (background task)
# - Processing worker (background task) ← This worker
# - Timeout scheduler (background task)
```

**System Dependencies:**
- poppler-utils, tesseract-ocr (for Docling PDF processing)
- Install on dev machine or in production Docker container

## Technical Notes

### PydanticAI Configuration
```python
# AI agent configuration using PydanticAI with Anthropic Claude
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.anthropic import AnthropicModel

class AIConfig:
    MODEL = AnthropicModel("claude-4-haiku-latest")
    MAX_TOKENS = 8192
    TEMPERATURE = 0.2  # Lower temperature for consistency
    TIMEOUT_SECONDS = 120

    # Rate limiting for Anthropic API
    REQUESTS_PER_MINUTE = 50  # Anthropic's default limit
    MAX_CONCURRENT_PAGES = 3  # Process up to 3 pages simultaneously

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0

# Processing result models
class PageProcessingResult(BaseModel):
    page_number: int
    improved_markdown: str
    confidence_score: float
    accessibility_improvements: List[str]
    identified_issues: List[str]

class DocumentProcessingResult(BaseModel):
    final_confidence_score: float
    total_pages_processed: int
    average_page_confidence: float
    pages_requiring_review: List[int]  # Pages with confidence < 0.6
    processing_notes: List[str]
```

### Docling Configuration
```python
# PDF conversion settings optimized for course materials
DOCLING_CONFIG = {
    "pdf_backend": "pypdfium2",  # Best for academic documents
    "ocr_engine": "tesseract",
    "ocr_languages": ["eng"],
    "extract_tables": True,
    "extract_images": True,
    "preserve_layout": True,
    "detect_reading_order": True,
    "table_structure_detection": True,
    "image_resolution_dpi": 150,
    "max_image_size_mb": 10,
    "timeout_seconds": 300
}

# Quality thresholds
CONFIDENCE_THRESHOLDS = {
    "high": 0.85,      # Documents ready for publication
    "medium": 0.60,    # Documents need review
    "low": 0.0         # Documents need significant work
}
```

### Environment Configuration
```python
# Environment variables required
REDIS_URL=redis://redis:6379
AWS_ENDPOINT_URL=http://localstack:4566
S3_TEMP_BUCKET=equalify-temp
S3_RESULTS_BUCKET=equalify-results
S3_PUBLIC_BASE_URL=https://results.equalify.app

# Queue names
PROCESSING_QUEUE_NAME=eq-pdf:queue:processing

# AI Configuration
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL_NAME=claude-3-5-sonnet-20241022
CLAUDE_MAX_TOKENS=8192
CLAUDE_TEMPERATURE=0.2
CLAUDE_MAX_CONCURRENT_PAGES=3

# Processing settings
MAX_PROCESSING_TIME_MINUTES=15
CONCURRENT_JOBS=2
MEMORY_LIMIT_MB=4096
TEMP_DIR=/tmp/processing

# Quality thresholds
HIGH_CONFIDENCE_THRESHOLD=0.85
MEDIUM_CONFIDENCE_THRESHOLD=0.60
STRUCTURE_ACCURACY_THRESHOLD=0.90
```

## Definition of Done
- [ ] Docling PDF conversion working reliably
- [ ] Single Claude agent operational with visual comparison tested
- [ ] Page-by-page pipeline processes documents end-to-end with visual analysis
- [ ] HTML and MDX output generated correctly
- [ ] S3 storage with versioning implemented
- [ ] Confidence scoring and quality metrics working
- [ ] Module integrates with main application
- [ ] Integration tests with Redis and S3 pass
- [ ] Performance meets 2-8 minute processing target
- [ ] Documentation complete and accurate
- [ ] Module ready for production deployment