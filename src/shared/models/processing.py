"""Processing result models for completed conversions."""

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


class LLMUsage(BaseModel):
    """Token usage and cost for a single LLM call.

    Tracks input/output/total tokens and calculates estimated cost based on model pricing.

    Attributes:
        input_tokens: Number of input tokens consumed
        output_tokens: Number of output tokens generated
        total_tokens: Total tokens (input + output)
        estimated_cost_cents: Estimated cost in cents based on model pricing

    Example:
        >>> usage = LLMUsage(
        ...     input_tokens=1500,
        ...     output_tokens=200,
        ...     total_tokens=1700,
        ...     estimated_cost_cents=0.25
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
    total_tokens: int = Field(
        ...,
        ge=0,
        description="Total tokens (input + output)"
    )
    estimated_cost_cents: float = Field(
        ...,
        ge=0.0,
        description="Estimated cost in cents based on model pricing"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "input_tokens": 1500,
                "output_tokens": 200,
                "total_tokens": 1700,
                "estimated_cost_cents": 0.25
            }
        }
    )
