"""Processing result models for completed conversions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProcessingResult(BaseModel):
    """Final processing results for a converted document.

    Captures all outputs from the AI processing pipeline including
    accessibility improvements and quality metrics.

    Attributes:
        job_id: Unique job identifier (UUID)
        markdown_url: S3 URL for accessible markdown output
        confidence_score: AI confidence in conversion quality (0.0-1.0)
        processing_time_seconds: Total processing duration
        error_message: Optional error details if processing failed

    Example:
        >>> result = ProcessingResult(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     markdown_url="s3://equalify-results/550e8400.../v20250101_120000/output.md",
        ...     confidence_score=0.92,
        ...     processing_time_seconds=180,
        ...     error_message=None
        ... )
    """
    job_id: str = Field(
        ...,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="UUID format job identifier"
    )
    markdown_url: str | None = Field(
        default=None,
        description="S3 URL for accessible markdown output"
    )
    confidence_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="AI confidence score"
    )
    processing_time_seconds: int = Field(
        ...,
        ge=0,
        description="Total processing duration"
    )
    error_message: str | None = Field(
        default=None,
        max_length=2000,
        description="Error details if failed"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "markdown_url": "s3://equalify-results/550e8400.../v20250101_120000/output.md",
                "confidence_score": 0.87,
                "processing_time_seconds": 245,
                "error_message": None
            }
        }
    )


class ProcessingJob(BaseModel):
    """Active processing job metadata.

    Tracks processing pipeline state including current stage,
    partial results, and intermediate artifacts.

    Attributes:
        job_id: Unique job identifier (UUID)
        s3_key: S3 key for source PDF
        current_stage: Current processing pipeline stage
        markdown_s3_key: Optional S3 key for Docling markdown output
        result: Optional final processing result
    """
    job_id: str = Field(
        ...,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="UUID format job identifier"
    )
    s3_key: str = Field(
        ...,
        min_length=1,
        description="S3 key for source PDF"
    )
    current_stage: str = Field(
        ...,
        description="Current pipeline stage"
    )
    markdown_s3_key: str | None = Field(
        default=None,
        description="S3 key for markdown output"
    )
    result: ProcessingResult | None = Field(
        default=None,
        description="Final processing result"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "s3_key": "temp/550e8400.../input.pdf",
                "current_stage": "docling_conversion",
                "markdown_s3_key": None,
                "result": None
            }
        }
    )


class TextCorrection(BaseModel):
    """Individual text correction identified by comparing page image to extracted text.

    Represents a specific layout or structure error that should be corrected
    in the markdown output to match the visual appearance of the page.

    Attributes:
        correction_type: Category of correction (heading level, list format, table, etc.)
        original_text: Text as extracted by Docling
        corrected_text: Text as it should appear based on visual layout
        location_context: Surrounding text to help locate the correction point
        line_number: Approximate line number in markdown (if determinable)
        confidence: AI confidence in this correction (0.0-1.0)
        explanation: Human-readable explanation of why this correction is needed

    Example:
        >>> correction = TextCorrection(
        ...     correction_type="heading_level",
        ...     original_text="Course Schedule",
        ...     corrected_text="## Course Schedule",
        ...     location_context="...previous heading...\\n\\nCourse Schedule\\n\\nWeek 1...",
        ...     line_number=15,
        ...     confidence=0.95,
        ...     explanation="Visual layout shows this as a level 2 heading (larger font, bold)"
        ... )
    """
    correction_type: Literal[
        "heading_level",
        "list_structure",
        "table_format",
        "paragraph_break",
        "spelling",
        "other"
    ] = Field(
        ...,
        description="Category of layout/structure correction"
    )
    original_text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Text as extracted by Docling"
    )
    corrected_text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Corrected text matching visual layout"
    )
    location_context: str = Field(
        default="",
        max_length=10000,
        description="Surrounding text for locating the error (optional)"
    )
    line_number: int | None = Field(
        default=None,
        ge=1,
        description="Approximate line number in markdown"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="AI confidence in this correction"
    )
    explanation: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Why this correction is needed"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "correction_type": "heading_level",
                "original_text": "Course Schedule",
                "corrected_text": "## Course Schedule",
                "location_context": "...Introduction\\n\\nCourse Schedule\\n\\nWeek 1: Overview...",
                "line_number": 15,
                "confidence": 0.95,
                "explanation": "Visual layout shows this as a level 2 heading with larger font and bold formatting"
            }
        }
    )


class LLMUsage(BaseModel):
    """Token usage and cost for a single LLM call.

    Tracks input/output tokens and calculates cost based on model pricing.

    Attributes:
        input_tokens: Number of input tokens consumed
        output_tokens: Number of output tokens generated
        cost_cents: Calculated cost in cents based on model pricing

    Example:
        >>> usage = LLMUsage(
        ...     input_tokens=1500,
        ...     output_tokens=200,
        ...     cost_cents=0.0625
        ... )
    """
    input_tokens: int = Field(
        ...,
        ge=0,
        description="Number of input tokens consumed"
    )
    output_tokens: int = Field(
        ...,
        ge=0,
        description="Number of output tokens generated"
    )
    cost_cents: float = Field(
        ...,
        ge=0.0,
        description="Calculated cost in cents"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "input_tokens": 1500,
                "output_tokens": 200,
                "cost_cents": 0.0625
            }
        }
    )


class PageCorrectionResult(BaseModel):
    """Text correction results for a single page.

    Contains visual observations, identified corrections, and metadata
    about the correction process.

    Attributes:
        visual_observations: Observations about the visual layout with landmark names
        corrections: List of individual corrections identified for this page
        overall_confidence: Overall confidence in correction quality (0.0-1.0)
        processing_notes: Summary of what was checked and corrected
        page_number: Page number (1-indexed)
        usage: Token usage and cost for this page's LLM call

    Example:
        >>> result = PageCorrectionResult(
        ...     visual_observations=[
        ...         "Title: 'Introduction to Python' - large bold text at top",
        ...         "Heading: 'Chapter 1' - medium bold, appears to be H2 level"
        ...     ],
        ...     corrections=[
        ...         TextCorrection(
        ...             correction_type="heading_level",
        ...             original_text="Introduction",
        ...             corrected_text="# Introduction",
        ...             location_context="...\\n\\nIntroduction\\n\\nThis course...",
        ...             confidence=0.95,
        ...             explanation="Visual layout shows level 1 heading"
        ...         )
        ...     ],
        ...     overall_confidence=0.92,
        ...     processing_notes="Checked 1 heading, 0 lists, 0 tables. Found 1 correction.",
        ...     page_number=1,
        ...     usage=LLMUsage(input_tokens=1500, output_tokens=200, cost_cents=0.0625)
        ... )
    """
    visual_observations: list[str] = Field(
        default_factory=list,
        description=(
            "Observations about the visual layout, using landmark names and explicit text. "
            "Examples: 'Title: \"Introduction to Python\" - large bold text at top', "
            "'Heading: \"Chapter 1\" - medium bold, appears to be H2 level', "
            "'Bulleted list with 3 items starting with \"First, ...\"', "
            "'Table with 4 columns: Name, Date, Amount, Status'"
        )
    )
    corrections: list[TextCorrection] = Field(
        default_factory=list,
        description="List of corrections for this page"
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence in page corrections"
    )
    processing_notes: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Summary of correction process"
    )
    page_number: int = Field(
        default=0,
        ge=0,
        description="Page number (1-indexed, 0 if not set)"
    )
    usage: LLMUsage | None = Field(
        default=None,
        description="Token usage and cost for this page's LLM call"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "visual_observations": [
                    "Title: 'Introduction' - large bold text at top",
                    "Section heading: 'Schedule' - medium bold, appears H2"
                ],
                "corrections": [
                    {
                        "correction_type": "heading_level",
                        "original_text": "Introduction",
                        "corrected_text": "# Introduction",
                        "location_context": "...\\n\\nIntroduction\\n\\nThis course...",
                        "line_number": 5,
                        "confidence": 0.95,
                        "explanation": "Visual layout shows level 1 heading with largest font"
                    },
                    {
                        "correction_type": "list_structure",
                        "original_text": "Week 1\\nWeek 2\\nWeek 3",
                        "corrected_text": "1. Week 1\\n2. Week 2\\n3. Week 3",
                        "location_context": "Schedule:\\n\\nWeek 1\\nWeek 2\\nWeek 3\\n\\nGrading",
                        "line_number": 12,
                        "confidence": 0.88,
                        "explanation": "Visual layout shows numbered list, not plain paragraphs"
                    }
                ],
                "overall_confidence": 0.91,
                "processing_notes": "Checked 2 headings, 1 list, 0 tables. Found 2 corrections.",
                "page_number": 1,
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": 200,
                    "cost_cents": 0.0625
                }
            }
        }
    )
