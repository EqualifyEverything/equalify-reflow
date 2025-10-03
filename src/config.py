"""Configuration management for API Gateway Service."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS Configuration
    aws_endpoint_url: str = "http://localstack:4566"
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"

    # S3 Buckets
    s3_temp_bucket: str = "equalify-temp"
    s3_results_bucket: str = "equalify-results"

    # Redis Configuration
    redis_url: str = "redis://redis:6379"
    redis_max_connections: int = Field(ge=1, le=1000, default=10)

    # Queue Configuration (align with shared/constants/queues.py)
    pii_queue_name: str = "eq-pdf:queue:pii"
    processing_queue_name: str = "eq-pdf:queue:processing"
    approval_queue_name: str = "eq-pdf:queue:approval"
    timeout_queue_name: str = "eq-pdf:timeouts:approval"

    # Job Status Configuration
    job_status_prefix: str = "eq-pdf:job:"

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = Field(ge=1, le=65535, default=8000)
    log_level: str = "INFO"
    environment: str = "production"  # "dev" or "production"

    # Metrics Configuration
    enable_metrics: bool = True
    metrics_port: int = Field(ge=1, le=65535, default=8001)

    # Application Settings
    max_upload_size: int = Field(gt=0, le=1024*1024*1024, default=100*1024*1024)  # 100MB, max 1GB
    max_file_size_mb: int = Field(gt=0, le=1000, default=100)
    job_ttl_days: int = Field(ge=1, le=365, default=30)

    # Processing Configuration
    estimated_processing_minutes: int = Field(ge=1, le=60, default=5)

    # Claude AI Configuration
    anthropic_api_key: SecretStr
    claude_model: str = "claude-3-5-haiku-20241022"
    claude_max_tokens: int = Field(ge=1, le=100000, default=4096)
    claude_temperature: float = Field(ge=0.0, le=2.0, default=0.2)

    # AI Processing Configuration
    max_concurrent_pages: int = Field(ge=1, le=50, default=5)  # Process up to 5 pages concurrently
    page_retry_attempts: int = Field(ge=0, le=10, default=3)  # Retry failed pages up to 3 times
    confidence_threshold_high: float = Field(ge=0.0, le=1.0, default=0.85)
    confidence_threshold_medium: float = Field(ge=0.0, le=1.0, default=0.60)

    # Timeout Worker Configuration
    approval_timeout_hours: int = Field(ge=1, le=168, default=4)  # Approval deadline (hours), max 1 week
    approval_check_interval_seconds: int = Field(ge=10, le=3600, default=30)  # Check for expired approvals every 30s
    temp_cleanup_interval_hours: int = Field(ge=1, le=168, default=1)  # Clean temp files every hour
    orphan_cleanup_interval_hours: int = Field(ge=1, le=168, default=4)  # Check for orphaned jobs every 4 hours
    metrics_cleanup_interval_hours: int = Field(ge=1, le=168, default=24)  # Clean old metrics daily

    # Retention Policies
    temp_file_retention_hours: int = Field(ge=1, le=720, default=24)  # Delete temp files after 24 hours, max 30 days
    job_retention_days: int = Field(ge=1, le=365, default=30)  # Keep completed/failed jobs for 30 days
    metrics_retention_days: int = Field(ge=1, le=730, default=90)  # Keep metrics for 90 days, max 2 years
    max_processing_hours: int = Field(ge=1, le=24, default=2)  # Mark jobs as stuck after 2 hours in processing

    # PII Detection Configuration
    pii_confidence_threshold: float = Field(ge=0.0, le=1.0, default=0.85)  # Minimum confidence score for PII detection

    # Redis TTL Configuration (in seconds)
    # TTL ensures job hashes auto-expire to prevent Redis memory exhaustion
    job_ttl_active: int = Field(ge=3600, le=30*24*3600, default=7*24*3600)       # 7 days for active jobs, min 1 hour, max 30 days
    job_ttl_completed: int = Field(ge=3600, le=365*24*3600, default=30*24*3600)   # 30 days for completed jobs, min 1 hour, max 1 year
    job_ttl_failed: int = Field(ge=3600, le=365*24*3600, default=30*24*3600)      # 30 days for failed jobs, min 1 hour, max 1 year
    job_ttl_denied: int = Field(ge=3600, le=30*24*3600, default=7*24*3600)       # 7 days for denied jobs, min 1 hour, max 30 days


settings = Settings()