"""Configuration management for API Gateway Service."""

from pydantic import SecretStr
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
    redis_max_connections: int = 10

    # Queue Configuration (align with shared/constants/queues.py)
    pii_queue_name: str = "eq-pdf:queue:pii"
    processing_queue_name: str = "eq-pdf:queue:processing"
    approval_queue_name: str = "eq-pdf:queue:approval"
    timeout_queue_name: str = "eq-pdf:timeouts:approval"

    # Job Status Configuration
    job_status_prefix: str = "eq-pdf:job:"

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # Application Settings
    max_upload_size: int = 100 * 1024 * 1024  # 100MB
    max_file_size_mb: int = 100
    job_ttl_days: int = 30

    # Processing Configuration
    estimated_processing_minutes: int = 5

    # Claude AI Configuration
    anthropic_api_key: SecretStr
    claude_model: str = "claude-3-5-haiku-20241022"
    claude_max_tokens: int = 4096
    claude_temperature: float = 0.2

    # AI Processing Configuration
    max_concurrent_pages: int = 5  # Process up to 5 pages concurrently
    page_retry_attempts: int = 3  # Retry failed pages up to 3 times
    confidence_threshold_high: float = 0.85
    confidence_threshold_medium: float = 0.60

    # Timeout Worker Configuration
    approval_timeout_hours: int = 4  # Approval deadline (hours)
    approval_check_interval_seconds: int = 30  # Check for expired approvals every 30s
    temp_cleanup_interval_hours: int = 1  # Clean temp files every hour
    orphan_cleanup_interval_hours: int = 4  # Check for orphaned jobs every 4 hours
    metrics_cleanup_interval_hours: int = 24  # Clean old metrics daily

    # Retention Policies
    temp_file_retention_hours: int = 24  # Delete temp files after 24 hours
    job_retention_days: int = 30  # Keep completed/failed jobs for 30 days
    metrics_retention_days: int = 90  # Keep metrics for 90 days
    max_processing_hours: int = 2  # Mark jobs as stuck after 2 hours in processing


settings = Settings()