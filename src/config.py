"""Configuration management for API Gateway Service."""

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


settings = Settings()