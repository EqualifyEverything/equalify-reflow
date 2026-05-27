"""Configuration management for API Gateway Service."""

import json
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS Configuration (boto3 reads AWS_ENDPOINT_URL_S3 from environment automatically)
    aws_region: str = Field(default="us-east-1", description="AWS region for S3 and Bedrock clients")
    # Public S3 URL for client-facing links (localhost:4566 in dev, real S3 URL in prod)
    s3_public_url: str | None = Field(
        default=None,
        description="Public S3 base URL for client-facing links (e.g., 'http://localhost:4566' in dev, real S3 URL in prod)",
    )
    aws_access_key_id: str | None = Field(
        default=None,
        description="AWS access key ID (secret); leave unset to use IAM role in production, 'test' for local dev",
    )
    aws_secret_access_key: str | None = Field(
        default=None,
        description="AWS secret access key (secret); leave unset to use IAM role in production, 'test' for local dev",
    )

    # AI Provider Configuration
    # Selects which backend serves AI agent calls in the versioned pipeline.
    # Leave ai_provider unset to auto-detect: if anthropic_api_key is set, use Anthropic
    # direct; otherwise fall back to AWS Bedrock with ambient AWS credentials.
    ai_provider: Literal["anthropic", "bedrock"] | None = Field(
        default=None,
        description="AI model backend for pipeline agents ('anthropic' or 'bedrock'); auto-detected from ANTHROPIC_API_KEY presence when unset",
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Anthropic API key (secret); required when ai_provider=anthropic and enables auto-detect into the Anthropic path when ai_provider is unset",
    )

    # S3 Buckets
    s3_temp_bucket: str = Field(default="equalify-temp", description="S3 bucket name for temporary PDF uploads")
    s3_results_bucket: str = Field(default="equalify-results", description="S3 bucket name for processed results and figures")

    # Redis Configuration
    redis_url: str = Field(default="redis://redis:6379", description="Redis URL for job state, queues, and rate limiting")
    redis_max_connections: int = Field(ge=1, le=1000, default=10, description="Maximum connections in the Redis client pool")

    # Queue Configuration (align with shared/constants/queues.py)
    pii_queue_name: str = Field(default="eq-pdf:queue:pii", description="Redis list key for the PII scan worker queue")
    timeout_queue_name: str = Field(default="eq-pdf:timeouts:approval", description="Redis sorted set key for approval timeout deadlines")

    # Job Status Configuration
    job_status_prefix: str = Field(default="eq-pdf:job:", description="Redis key prefix for per-job status hashes")

    # API Configuration
    api_host: str = Field(default="0.0.0.0", description="Host interface the FastAPI server binds to")
    api_port: int = Field(ge=1, le=65535, default=8080, description="Port the FastAPI server listens on")
    log_level: str = Field(default="INFO", description="Application log level (DEBUG, INFO, WARNING, ERROR)")
    environment: str = Field(default="production", description="Runtime environment label ('dev' or 'production')")

    # API Key Authentication Configuration
    enable_api_key_auth: bool = Field(default=True, description="Enable API key authentication for API endpoints")
    api_key_header_name: str = Field(default="X-API-Key", description="Header name for API key authentication")
    api_keys: SecretStr | None = Field(
        default=None,
        description=(
            "Comma-separated list of valid API keys. Each entry is either a "
            "bare key or a 'label:key' pair (split on the first colon, like "
            "AUTH_BASIC_USERS). The label is recorded on every authenticated "
            "request log so per-key usage is attributable; the key itself is "
            "never logged. Bare keys get a derived 'key-<fingerprint>' label. "
            "Keys that themselves contain a colon must be given an explicit "
            "label to avoid the leading segment being parsed as one."
        ),
    )

    # Viewer Authentication (optional, layered on top of API keys)
    # AUTH_MODE=none keeps today's behaviour: no login, no cookies, no identity.
    # AUTH_MODE=basic enables operator-provisioned username/password against
    #   AUTH_BASIC_USERS (CSV of "username:argon2hash"). No signup endpoint.
    # AUTH_MODE=oidc (PR2) enables generic OIDC; Entra is just a config preset.
    # API keys remain valid as a parallel auth path in all modes.
    auth_mode: Literal["none", "basic", "oidc"] = Field(
        default="none",
        description=(
            "Auth mode for the viewer. 'none' preserves today's behaviour; "
            "'basic' enables HTTP basic; 'oidc' enables SSO (PR2)."
        ),
    )
    auth_secret_key: SecretStr | None = Field(
        default=None,
        description="HMAC key for signing session and CSRF cookies. Required when auth_mode != 'none'. >= 32 chars.",
    )
    auth_session_ttl_seconds: int = Field(
        ge=300,
        le=30 * 24 * 3600,
        default=8 * 3600,
        description="Session lifetime in seconds (default 8h). Sliding re-issue at half-life.",
    )
    auth_session_cookie_name: str = Field(
        default="reflow_session",
        description="Name of the session cookie. The CSRF companion cookie is named '<this>_csrf'.",
    )
    auth_cookie_secure: bool = Field(
        default=True,
        description="Set Secure flag on session cookies. Disable only for local HTTP dev.",
    )
    auth_basic_users: SecretStr | None = Field(
        default=None,
        description=(
            "Semicolon-separated 'username:argon2hash' pairs. Required when "
            "auth_mode='basic'. Generate hashes with `make auth-hash-password`. "
            "Comma-separated would collide with argon2 parameter blocks."
        ),
    )
    auth_oidc_providers: SecretStr | None = Field(
        default=None,
        description="JSON array of OIDC provider configs. Required when auth_mode='oidc' (PR2).",
    )
    auth_post_login_redirect: str = Field(
        default="/",
        description="Where to send the browser after a successful login when no ?next= is provided.",
    )

    # Metrics Configuration
    enable_metrics: bool = Field(default=True, description="Enable Prometheus metrics collection and /metrics endpoint")
    metrics_port: int = Field(ge=1, le=65535, default=8001, description="Port for the Prometheus metrics server")

    # Application Settings
    max_upload_size: int = Field(
        gt=0,
        le=1024 * 1024 * 1024,
        default=100 * 1024 * 1024,
        description="Maximum upload size in bytes (default 100MB, hard cap 1GB)",
    )  # 100MB, max 1GB
    max_file_size_mb: int = Field(
        gt=0,
        le=1000,
        default=100,
        description="Maximum PDF file size in megabytes (default 100MB, hard cap 1000MB)",
    )

    # Processing Configuration
    estimated_processing_minutes: int = Field(
        ge=1,
        le=60,
        default=5,
        description="User-facing estimate of processing duration in minutes (shown in API responses)",
    )
    pipeline_timeout_seconds: int = Field(
        ge=60, le=7200, default=1800, description="Global timeout for pipeline processing (seconds)"
    )

    # PDF Classification Configuration
    pdf_max_pages: int = Field(
        ge=1,
        le=500,
        default=50,
        description="Maximum pages allowed per PDF. Documents exceeding this are rejected.",
    )

    # Docling-serve sidecar configuration
    docling_serve_url: str = Field(
        default="http://docling-serve:5001",
        description=(
            "Base URL of the docling-serve service. **Required** — every "
            "document submission calls this; there is no in-process docling "
            "fallback. The default 'http://docling-serve:5001' resolves only "
            "inside the docker-compose dev stack, where 'docling-serve' is "
            "the compose service name. For any other deployment set this to "
            "the actual address: 'http://localhost:5001' when docling-serve "
            "runs as a sidecar in the same task/pod, or the full "
            "DNS/hostname of a separate docling-serve service otherwise."
        ),
    )
    docling_serve_timeout: float = Field(
        ge=10.0,
        le=600.0,
        default=120.0,
        description="HTTP timeout in seconds for docling-serve requests",
    )

    # OCR Configuration
    ocr_default_languages: list[str] = Field(
        default=["eng"],
        description="Default OCR language codes (Tesseract format, mapped to EasyOCR for docling-serve)",
    )

    # PDF Processing Configuration
    pdf_images_scale: float = Field(
        ge=1.0,
        le=3.0,
        default=1.5,
        description="Scale factor for PDF page image generation. "
        "1.5x (108 DPI) is optimal for Claude vision API. "
        "2.0x (144 DPI) may be needed for complex diagrams.",
    )


    # Timeout Worker Configuration
    approval_timeout_hours: int = Field(
        ge=1, le=168, default=4, description="PII approval deadline in hours before a job is auto-denied (max 1 week)"
    )  # Approval deadline (hours), max 1 week
    approval_check_interval_seconds: int = Field(
        ge=10, le=3600, default=30, description="How often the timeout worker checks for expired approvals (seconds)"
    )  # Check for expired approvals every 30s
    temp_cleanup_interval_hours: int = Field(
        ge=1, le=168, default=1, description="Interval in hours between temp-file cleanup sweeps"
    )  # Clean temp files every hour
    orphan_cleanup_interval_hours: int = Field(
        ge=1, le=168, default=4, description="Interval in hours between orphaned-job cleanup sweeps"
    )  # Check for orphaned jobs every 4 hours
    metrics_cleanup_interval_hours: int = Field(
        ge=1, le=168, default=24, description="Interval in hours between old-metrics cleanup sweeps"
    )  # Clean old metrics daily

    # Retention Policies
    temp_file_retention_hours: int = Field(
        ge=1, le=720, default=24, description="Hours to retain temporary files in S3 before cleanup (max 30 days)"
    )  # Delete temp files after 24 hours, max 30 days
    debug_artifact_retention_hours: int = Field(
        ge=1, le=168, default=24, description="Hours to retain debug artifacts before cleanup (max 7 days)"
    )  # Delete debug artifacts after 24 hours, max 7 days
    job_retention_days: int = Field(
        ge=1, le=365, default=30, description="Days to retain completed and failed jobs before cleanup"
    )  # Keep completed/failed jobs for 30 days
    metrics_retention_days: int = Field(
        ge=1, le=730, default=90, description="Days to retain metrics history (max 2 years)"
    )  # Keep metrics for 90 days, max 2 years
    max_processing_hours: int = Field(
        ge=1, le=24, default=2, description="Hours after which an in-flight job is marked stuck"
    )  # Mark jobs as stuck after 2 hours in processing

    # PII Detection Configuration
    pii_confidence_threshold: float = Field(
        ge=0.0, le=1.0, default=0.85, description="Minimum Presidio confidence score required to flag a PII match"
    )  # Minimum confidence score for PII detection


    # Redis TTL Configuration (in seconds)
    # TTL ensures job hashes auto-expire to prevent Redis memory exhaustion
    # Active jobs: 7 days (min 1 hour, max 30 days)
    job_ttl_active: int = Field(
        ge=3600,
        le=30 * 24 * 3600,
        default=7 * 24 * 3600,
        description="Redis TTL in seconds for active job hashes (default 7 days, max 30 days)",
    )
    # Completed jobs: 30 days (min 1 hour, max 1 year)
    job_ttl_completed: int = Field(
        ge=3600,
        le=365 * 24 * 3600,
        default=30 * 24 * 3600,
        description="Redis TTL in seconds for completed job hashes (default 30 days, max 1 year)",
    )
    # Failed jobs: 30 days (min 1 hour, max 1 year)
    job_ttl_failed: int = Field(
        ge=3600,
        le=365 * 24 * 3600,
        default=30 * 24 * 3600,
        description="Redis TTL in seconds for failed job hashes (default 30 days, max 1 year)",
    )
    # Denied jobs: 7 days (min 1 hour, max 30 days)
    job_ttl_denied: int = Field(
        ge=3600,
        le=30 * 24 * 3600,
        default=7 * 24 * 3600,
        description="Redis TTL in seconds for denied job hashes (default 7 days, max 30 days)",
    )

    # Worker Queue Configuration
    pii_worker_queue_timeout_seconds: int = Field(
        ge=1, le=300, default=30, description="PII worker queue blocking timeout in seconds"
    )
    worker_error_sleep_seconds: int = Field(
        ge=1, le=300, default=5, description="Sleep duration after worker error to avoid tight error loops"
    )
    timeout_worker_check_interval_seconds: int = Field(
        ge=1, le=300, default=10, description="Timeout worker loop check interval in seconds"
    )
    timeout_worker_error_sleep_seconds: int = Field(
        ge=1, le=300, default=60, description="Timeout worker sleep duration on error"
    )

    # ------------------------------------------------------------------
    # Canvas LMS / LTI 1.3 integration
    # ------------------------------------------------------------------
    # All optional. When ``lti_enabled`` is False the routers self-disable
    # and the watcher/bridge idle, so leaving the rest empty is safe.
    lti_enabled: bool = Field(default=False, description="Enable LTI 1.3 endpoints and Canvas workers")
    lti_issuer: str | None = Field(default=None, description="Canvas issuer URL (e.g. https://canvas.instructure.com)")
    lti_client_id: str | None = Field(default=None, description="Canvas Developer Key client_id")
    lti_deployment_id: str | None = Field(default=None, description="Canvas deployment id")
    lti_auth_login_url: str | None = Field(default=None, description="Canvas OIDC authorize_redirect endpoint")
    lti_auth_token_url: str | None = Field(default=None, description="Canvas OAuth2 token endpoint")
    lti_jwks_url: str | None = Field(default=None, description="Canvas JWKS endpoint")
    lti_private_key_path: str = Field(default="/app/keys/lti_private.pem", description="Path to LTI private key")
    lti_public_key_path: str = Field(default="/app/keys/lti_public.pem", description="Path to LTI public key")
    lti_state_ttl_seconds: int = Field(ge=60, le=3600, default=600, description="OIDC state nonce TTL")
    lti_public_url: str | None = Field(default=None, description="Public origin of this tool, used in notification deep links")

    canvas_tenant: str = Field(
        default="default",
        description=(
            "Tenant id for Redis key namespacing. 'default' preserves the "
            "legacy unprefixed key shape (single-campus install); any other "
            "value namespaces every key as eq-pdf:t:{tenant}:... so multiple "
            "campuses can share one Redis without cross-contamination."
        ),
    )
    canvas_api_url: str | None = Field(default=None, description="Canvas instance base URL (https://yourschool.instructure.com)")
    canvas_api_token: SecretStr | None = Field(default=None, description="Canvas API token used by the watcher/bridge workers")
    canvas_watched_courses: str = Field(default="", description="Comma-separated Canvas course ids the watcher polls")
    multi_tenant_watcher: bool = Field(default=False, description="Watcher iterates LTI-registered platforms instead of canvas_watched_courses")
    canvas_oauth_client_id: str = Field(default="", description="Canvas API Key (non-LTI Developer Key) client_id for the user-OAuth2 flow; required because LTI 1.3 keys are not accepted by Canvas's /login/oauth2/auth endpoint")
    canvas_oauth_client_secret: SecretStr | None = Field(default=None, description="Secret for the user-OAuth API Key (optional if the key uses a public JWK URL and JWT-bearer client auth)")
    canvas_allowed_origins: str = Field(default="", description="Comma-separated Canvas origins allowed to call panorama endpoints (e.g. 'https://canvas.example.edu,https://example.instructure.com')")
    canvas_allowed_origin_regex: str = Field(default="", description="Regex of Canvas origins allowed to call panorama endpoints; takes precedence over canvas_allowed_origins when set")
    canvas_poll_seconds: int = Field(ge=15, le=3600, default=60, description="Canvas file poll interval")
    reflow_poll_seconds: int = Field(ge=5, le=600, default=30, description="Reflow job poll interval for the bridge")
    reflow_api_url: str = Field(default="http://api-gateway:8080", description="Reflow API base URL the bridge calls")

    # Data retention controls. Both apply to Canvas-side state stored in Redis.
    # Set to 0 (or negative) to disable purging for that bucket. Defaults are
    # conservative for a research-data steward: jobs roll off after 90 days
    # (alt formats can always be regenerated from the original file), audit
    # events survive a full year so ISO reviews have history to walk.
    # Per-course Claude API spend cap. See ``canvas.spend_cap`` for the
    # full contract. Default of 0 disables the cap entirely - operators
    # opt in by setting a per-course budget.
    canvas_monthly_spend_cap_usd_default: int = Field(
        ge=0, le=100_000, default=0,
        description="Default monthly Claude-API spend cap in USD per Canvas course (0=unlimited)",
    )
    canvas_monthly_spend_cap_overrides: str = Field(
        default="",
        description='JSON map of course_id->cap_usd overrides, e.g. \'{"50594": 250}\'',
    )
    canvas_estimated_cost_per_doc_cents: int = Field(
        ge=0, le=10000, default=10,
        description="Pre-flight per-document cost estimate in cents (used by the spend cap)",
    )
    canvas_job_retention_days: int = Field(
        ge=0, le=3650, default=90,
        description="Days to retain Canvas job records in Redis (0=keep forever)",
    )
    canvas_audit_retention_days: int = Field(
        ge=0, le=3650, default=365,
        description="Days to retain approval-audit events in Redis (0=keep forever)",
    )
    canvas_retention_sweep_every_ticks: int = Field(
        ge=1, le=10080, default=240,
        description="Run the retention sweep every N watcher ticks (default 240 ~= every 4 hours at 60s poll)",
    )

    # Testing Configuration
    disable_workers: bool = Field(
        default=False, description="Disable background workers (PII, timeout) for testing scenarios"
    )  # Set to True to disable background workers (for testing)

    # Telemetry Configuration (OpenTelemetry)
    telemetry_enabled: bool = Field(default=False, description="Enable OpenTelemetry tracing and metrics")
    telemetry_console_export: bool = Field(default=False, description="Export spans to console (for development)")
    telemetry_otlp_endpoint: str | None = Field(
        default=None, description="OTLP endpoint for trace export (e.g., 'localhost:4317' for Jaeger)"
    )
    telemetry_log_prompts: bool = Field(
        default=False, description="Log full LLM prompts and outputs in traces (WARNING: may contain document content)"
    )

    # Logfire Configuration (PydanticAI agent tracing)
    logfire_enabled: bool = Field(default=False, description="Enable Logfire tracing for PydanticAI agents")
    logfire_token: str = Field(default="", description="Logfire API token")

    # Feedback service (optional)
    feedback_enabled: bool = Field(
        default=False, description="Enable forwarding of user feedback to the external feedback service"
    )
    feedback_service_url: str | None = Field(
        default=None, description="Base URL of the external feedback aggregation service"
    )
    feedback_service_api_key: SecretStr | None = Field(
        default=None, description="API key for the external feedback service (secret)"
    )

    @model_validator(mode="after")
    def _validate_auth(self) -> "Settings":
        """Enforce per-mode requirements so misconfiguration fails fast at startup.

        We deliberately raise inside the validator (not log-and-continue) so an
        operator who flips ``AUTH_MODE=basic`` without populating users gets a
        loud failure rather than a silent auth bypass.
        """
        if self.auth_mode == "none":
            return self

        if self.auth_secret_key is None:
            raise ValueError("AUTH_SECRET_KEY is required when AUTH_MODE != 'none'")
        if len(self.auth_secret_key.get_secret_value()) < 32:
            raise ValueError("AUTH_SECRET_KEY must be at least 32 characters")

        if self.auth_mode == "basic":
            if self.auth_basic_users is None:
                raise ValueError("AUTH_BASIC_USERS is required when AUTH_MODE='basic'")
            # Semicolon-separated to avoid colliding with argon2 parameter
            # blocks, which always contain commas (m=…,t=…,p=…).
            entries = [e.strip() for e in self.auth_basic_users.get_secret_value().split(";")]
            valid = [
                e for e in entries if ":" in e and e.partition(":")[2].strip().startswith("$argon2")
            ]
            if not valid:
                raise ValueError(
                    "AUTH_BASIC_USERS must contain at least one 'username:$argon2…' entry"
                )

        if self.auth_mode == "oidc":
            if self.auth_oidc_providers is None:
                raise ValueError("AUTH_OIDC_PROVIDERS is required when AUTH_MODE='oidc'")
            try:
                providers = json.loads(self.auth_oidc_providers.get_secret_value())
            except json.JSONDecodeError as e:
                raise ValueError(f"AUTH_OIDC_PROVIDERS must be valid JSON: {e}") from e
            if not isinstance(providers, list) or not providers:
                raise ValueError("AUTH_OIDC_PROVIDERS must be a non-empty JSON array")
            required_keys = {"id", "display_name", "discovery_url", "client_id", "client_secret"}
            for entry in providers:
                if not isinstance(entry, dict):
                    raise ValueError("AUTH_OIDC_PROVIDERS entries must be objects")
                missing = required_keys - entry.keys()
                if missing:
                    raise ValueError(f"AUTH_OIDC_PROVIDERS entry missing keys: {sorted(missing)}")

        return self


settings = Settings()
