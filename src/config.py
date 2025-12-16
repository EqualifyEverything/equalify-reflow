"""Configuration management for API Gateway Service."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS Configuration (boto3 reads AWS_ENDPOINT_URL_S3 from environment automatically)
    aws_region: str = "us-east-1"
    # Public S3 URL for client-facing links (localhost:4566 in dev, real S3 URL in prod)
    s3_public_url: str | None = None  # e.g., "http://localhost:4566" for dev
    aws_access_key_id: str | None = None  # Uses IAM role in production, "test" for local dev
    aws_secret_access_key: str | None = None  # Uses IAM role in production, "test" for local dev

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
    api_port: int = Field(ge=1, le=65535, default=8080)
    log_level: str = "INFO"
    environment: str = "production"  # "dev" or "production"

    # API Key Authentication Configuration
    enable_api_key_auth: bool = Field(default=True, description="Enable API key authentication for API endpoints")
    api_key_header_name: str = Field(default="X-API-Key", description="Header name for API key authentication")
    api_keys: SecretStr | None = Field(default=None, description="Comma-separated list of valid API keys")

    # Swagger/OpenAPI Documentation Authentication (HTTP Basic)
    enable_docs_auth: bool = Field(default=False, description="Enable HTTP Basic auth for /docs and /openapi.json")
    docs_username: str = Field(default="admin", description="Username for Swagger UI authentication")
    docs_password: SecretStr | None = Field(default=None, description="Password for Swagger UI authentication")

    # Metrics Configuration
    enable_metrics: bool = True
    metrics_port: int = Field(ge=1, le=65535, default=8001)

    # Application Settings
    max_upload_size: int = Field(gt=0, le=1024*1024*1024, default=100*1024*1024)  # 100MB, max 1GB
    max_file_size_mb: int = Field(gt=0, le=1000, default=100)
    job_ttl_days: int = Field(ge=1, le=365, default=30)

    # Processing Configuration
    estimated_processing_minutes: int = Field(ge=1, le=60, default=5)

    # AI Provider Configuration
    ai_provider: str = "bedrock"  # Only "bedrock" is supported

    # Bedrock Configuration
    # Note: Model selection is handled by src/agents/model_tiers.py (not configurable via env)
    bedrock_region: str = "us-east-1"

    # Claude Model Settings
    claude_max_tokens: int = Field(ge=1, le=100000, default=4096)
    claude_temperature: float = Field(ge=0.0, le=2.0, default=0.2)

    # AI Processing Configuration
    max_pages_full_context: int = Field(
        ge=1, le=50, default=15,
        description="Maximum pages for full-document extraction before chunking is needed"
    )
    confidence_threshold_high: float = Field(ge=0.0, le=1.0, default=0.85)
    confidence_threshold_medium: float = Field(ge=0.0, le=1.0, default=0.60)

    # PDF Processing Configuration
    pdf_images_scale: float = Field(
        ge=1.0, le=3.0, default=1.5,
        description="Scale factor for PDF page image generation. "
                    "1.5x (108 DPI) is optimal for Claude vision API. "
                    "2.0x (144 DPI) may be needed for complex diagrams."
    )

    # Multi-Agent Configuration
    agent_prompts_dir: str = Field(
        default="config/agents",
        description="Directory containing agent YAML prompt files"
    )
    agent_max_retries: int = Field(
        ge=0, le=10, default=2,
        description="Maximum retries for agent output validation failures"
    )
    agent_default_temperature: float = Field(
        ge=0.0, le=2.0, default=0.2,
        description="Default temperature for agent LLM calls"
    )
    max_concurrent_agents: int = Field(
        ge=1, le=4, default=4,
        description="Maximum specialized agents to run concurrently"
    )

    # Deterministic Pre-Analysis Configuration
    # VeraPDF settings for PDF/UA-1 accessibility validation
    verapdf_url: str = Field(
        default="http://verapdf:8080",
        description="VeraPDF REST API URL"
    )
    verapdf_timeout: int = Field(
        ge=30, le=600, default=120,
        description="VeraPDF request timeout in seconds"
    )
    verapdf_enabled: bool = Field(
        default=True,
        description="Enable VeraPDF PDF/UA-1 validation"
    )

    # PyMarkdown settings for markdown structure validation
    pymarkdown_enabled: bool = Field(
        default=True,
        description="Enable PyMarkdown structure linting"
    )
    pymarkdown_rules: str = Field(
        default="MD001,MD025,MD036,MD045",
        description="Comma-separated list of PyMarkdown rules to enable"
    )

    # Document Context Extraction Configuration
    context_extraction_enabled: bool = Field(
        default=True,
        description="Enable document context extraction for agent grounding"
    )
    context_max_headings: int = Field(
        ge=10, le=500, default=100,
        description="Maximum headings to track per document"
    )
    context_recurring_threshold: float = Field(
        ge=0.1, le=1.0, default=0.5,
        description="Minimum page occurrence rate to consider element recurring (0.5 = 50%)"
    )

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

    # Confidence threshold for auto vs manual routing
    # Observations/proposals with confidence >= this go to auto queue, < this go to manual
    min_confidence_for_auto_approval: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for auto vs manual routing. "
                    "Observations/proposals >= this go to auto queue (batch approval), "
                    "< this go to manual queue (individual review). "
                    "Set to 0.0 to auto-approve all, 1.0 to require manual review for all."
    )

    # Correction review workflow
    always_require_correction_review: bool = Field(
        default=True,
        description="Always route to correction review (True for demo/testing)"
    )

    correction_approval_timeout_hours: int = Field(
        ge=1, le=168, default=4,
        description="Hours until correction approval expires"
    )

    @property
    def auto_approval_threshold(self) -> float:
        """Deprecated: Use min_confidence_for_auto_approval instead."""
        return self.min_confidence_for_auto_approval

    # Redis TTL Configuration (in seconds)
    # TTL ensures job hashes auto-expire to prevent Redis memory exhaustion
    # Active jobs: 7 days (min 1 hour, max 30 days)
    job_ttl_active: int = Field(ge=3600, le=30 * 24 * 3600, default=7 * 24 * 3600)
    # Completed jobs: 30 days (min 1 hour, max 1 year)
    job_ttl_completed: int = Field(ge=3600, le=365 * 24 * 3600, default=30 * 24 * 3600)
    # Failed jobs: 30 days (min 1 hour, max 1 year)
    job_ttl_failed: int = Field(ge=3600, le=365 * 24 * 3600, default=30 * 24 * 3600)
    # Denied jobs: 7 days (min 1 hour, max 30 days)
    job_ttl_denied: int = Field(ge=3600, le=30 * 24 * 3600, default=7 * 24 * 3600)

    # Worker Queue Configuration
    pii_worker_queue_timeout_seconds: int = Field(
        ge=1, le=300, default=30,
        description="PII worker queue blocking timeout in seconds"
    )
    processing_worker_queue_timeout_seconds: int = Field(
        ge=1, le=300, default=60,
        description="Processing worker queue blocking timeout in seconds"
    )
    worker_error_sleep_seconds: int = Field(
        ge=1, le=300, default=5,
        description="Sleep duration after worker error to avoid tight error loops"
    )
    timeout_worker_check_interval_seconds: int = Field(
        ge=1, le=300, default=10,
        description="Timeout worker loop check interval in seconds"
    )
    timeout_worker_error_sleep_seconds: int = Field(
        ge=1, le=300, default=60,
        description="Timeout worker sleep duration on error"
    )

    # Testing Configuration
    disable_workers: bool = False  # Set to True to disable background workers (for testing)

    # Telemetry Configuration (OpenTelemetry)
    telemetry_enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry tracing and metrics"
    )
    telemetry_console_export: bool = Field(
        default=False,
        description="Export spans to console (for development)"
    )
    telemetry_otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP endpoint for trace export (e.g., 'localhost:4317' for Jaeger)"
    )


settings = Settings()
