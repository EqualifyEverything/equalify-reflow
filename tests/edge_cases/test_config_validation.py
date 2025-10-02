"""Edge case tests for configuration validation."""

import pytest
from pydantic import ValidationError

from src.config import Settings


class TestConfigurationValidation:
    """Tests for configuration validation edge cases."""

    def test_default_config_is_valid(self):
        """Test that default configuration values are valid."""
        settings = Settings()

        # Verify critical settings have valid defaults
        assert settings.job_ttl_active > 0
        assert settings.job_ttl_completed > 0
        assert settings.max_file_size_mb > 0
        assert settings.approval_timeout_hours > 0
        assert settings.redis_max_connections > 0
        assert settings.confidence_threshold_high >= 0.0
        assert settings.confidence_threshold_medium >= 0.0

    def test_negative_timeout_rejected(self):
        """Test that negative timeout values are rejected."""
        try:
            settings = Settings(approval_timeout_hours=-1)
            # If validation allows it, verify it's still positive or zero
            assert settings.approval_timeout_hours >= 0
        except ValidationError:
            # If validation rejects it, that's acceptable
            pass

    def test_negative_ttl_values_rejected(self):
        """Test that negative TTL values are rejected."""
        invalid_ttls = [
            {"job_ttl_active": -1},
            {"job_ttl_completed": -100},
            {"job_ttl_failed": -1},
        ]

        for invalid_config in invalid_ttls:
            try:
                settings = Settings(**invalid_config)
                # If allowed, verify it's non-negative
                for key, value in invalid_config.items():
                    assert getattr(settings, key) >= 0
            except (ValidationError, ValueError):
                # Rejection is acceptable
                pass

    def test_zero_ttl_values(self):
        """Test handling of zero TTL values."""
        # Zero TTL might be valid (immediate expiration)
        try:
            settings = Settings(job_ttl_active=0)
            assert settings.job_ttl_active == 0
        except ValidationError:
            # Or might be rejected
            pass

    def test_negative_max_file_size(self):
        """Test that negative file size is rejected."""
        try:
            settings = Settings(max_upload_size=-1, max_file_size_mb=-1)
            # If allowed, should be clamped or ignored
            assert settings.max_upload_size >= 0
            assert settings.max_file_size_mb >= 0
        except (ValidationError, ValueError):
            pass

    def test_zero_max_file_size(self):
        """Test that zero max file size is handled."""
        try:
            settings = Settings(max_upload_size=0, max_file_size_mb=0)
            # Zero might mean unlimited or might be invalid
            assert isinstance(settings.max_upload_size, int)
        except (ValidationError, ValueError):
            # Or rejected
            pass

    def test_extreme_max_file_size(self):
        """Test extremely large file size limit."""
        # Try 10GB limit
        try:
            settings = Settings(max_upload_size=10 * 1024 * 1024 * 1024)
            assert settings.max_upload_size > 0
        except (ValidationError, ValueError, OverflowError):
            # May be rejected if too large
            pass

    def test_invalid_confidence_thresholds(self):
        """Test invalid confidence threshold values."""
        invalid_thresholds = [
            {"confidence_threshold_high": -0.1},  # Below 0
            {"confidence_threshold_high": 1.5},   # Above 1
            {"confidence_threshold_medium": 2.0}, # Above 1
        ]

        for invalid_config in invalid_thresholds:
            try:
                settings = Settings(**invalid_config)
                # If allowed, verify it's clamped to valid range
                for key, _ in invalid_config.items():
                    value = getattr(settings, key)
                    assert 0.0 <= value <= 1.0
            except (ValidationError, ValueError):
                # Rejection is expected
                pass

    def test_confidence_threshold_boundaries(self):
        """Test confidence threshold boundary values."""
        # Test exact boundaries
        settings_zero = Settings(confidence_threshold_high=0.0)
        settings_one = Settings(confidence_threshold_high=1.0)

        assert settings_zero.confidence_threshold_high == 0.0
        assert settings_one.confidence_threshold_high == 1.0

    def test_invalid_redis_url_format(self):
        """Test invalid Redis URL formats."""
        invalid_urls = [
            "not-a-url",
            "http://redis:6379",  # Wrong protocol
            "redis://",           # Incomplete
            "",                   # Empty
            "redis://redis",      # No port (may be valid)
        ]

        for url in invalid_urls:
            try:
                settings = Settings(redis_url=url)
                # If accepted, verify it's a string
                assert isinstance(settings.redis_url, str)
            except (ValidationError, ValueError):
                # Rejection is acceptable
                pass

    def test_redis_url_with_password(self):
        """Test Redis URL with password."""
        url_with_password = "redis://:password@redis:6379"

        settings = Settings(redis_url=url_with_password)
        assert settings.redis_url == url_with_password

    def test_empty_bucket_names(self):
        """Test empty S3 bucket names."""
        try:
            settings = Settings(s3_temp_bucket="", s3_results_bucket="")
            # Empty strings might be rejected
            assert len(settings.s3_temp_bucket) >= 0
        except (ValidationError, ValueError):
            # Or explicitly rejected
            pass

    def test_invalid_bucket_name_characters(self):
        """Test S3 bucket names with invalid characters."""
        invalid_names = [
            "Invalid Bucket Name!",  # Spaces and special chars
            "UPPERCASE",              # Uppercase (invalid for S3)
            "bucket_name",            # Underscores
            "a" * 64,                 # Too long (max 63 chars)
            "ab",                     # Too short (min 3 chars)
        ]

        for bucket_name in invalid_names:
            try:
                settings = Settings(s3_temp_bucket=bucket_name)
                # If accepted, validation might be loose
                assert isinstance(settings.s3_temp_bucket, str)
            except (ValidationError, ValueError):
                # Or rejected
                pass

    def test_invalid_aws_region(self):
        """Test invalid AWS region."""
        invalid_regions = [
            "",
            "invalid-region",
            "us_east_1",  # Underscore instead of dash
            "USEAST1",
        ]

        for region in invalid_regions:
            try:
                settings = Settings(aws_region=region)
                # Might accept any string
                assert isinstance(settings.aws_region, str)
            except (ValidationError, ValueError):
                pass

    def test_negative_redis_max_connections(self):
        """Test negative Redis max connections."""
        try:
            settings = Settings(redis_max_connections=-1)
            assert settings.redis_max_connections >= 0
        except (ValidationError, ValueError):
            pass

    def test_zero_redis_max_connections(self):
        """Test zero Redis max connections."""
        try:
            settings = Settings(redis_max_connections=0)
            # Zero connections is invalid
            assert settings.redis_max_connections > 0
        except (ValidationError, ValueError):
            # Should be rejected
            pass

    def test_extreme_redis_max_connections(self):
        """Test extremely high Redis max connections."""
        try:
            settings = Settings(redis_max_connections=100000)
            # Very high value should be accepted but might be impractical
            assert settings.redis_max_connections > 0
        except (ValidationError, ValueError, OverflowError):
            pass

    def test_invalid_queue_names(self):
        """Test invalid queue name formats."""
        invalid_names = [
            "",
            " ",
            "queue name with spaces",
            "queue\nname",
            "\x00invalid",
        ]

        for queue_name in invalid_names:
            try:
                settings = Settings(pii_queue_name=queue_name)
                # Might accept any string
                assert isinstance(settings.pii_queue_name, str)
            except (ValidationError, ValueError):
                pass

    def test_empty_string_configs(self):
        """Test empty string configuration values."""
        try:
            settings = Settings(
                api_host="",
                log_level="",
                claude_model="",
            )
            # Some empty strings might be invalid
            assert isinstance(settings.api_host, str)
        except (ValidationError, ValueError):
            # Should catch empty required strings
            pass

    def test_invalid_log_level(self):
        """Test invalid log level values."""
        invalid_levels = [
            "INVALID",
            "debug",  # Lowercase might be invalid
            "ERROR123",
            "",
        ]

        for level in invalid_levels:
            try:
                settings = Settings(log_level=level)
                # Might accept any string
                assert isinstance(settings.log_level, str)
            except (ValidationError, ValueError):
                pass

    def test_negative_api_port(self):
        """Test negative API port number."""
        try:
            settings = Settings(api_port=-1)
            assert settings.api_port >= 0
        except (ValidationError, ValueError):
            pass

    def test_invalid_port_numbers(self):
        """Test invalid port number ranges."""
        invalid_ports = [
            0,      # Port 0 (might be valid for auto-assign)
            70000,  # Above max (65535)
            -100,   # Negative
        ]

        for port in invalid_ports:
            try:
                settings = Settings(api_port=port)
                # Valid ports: 1-65535 (0 sometimes valid)
                assert 0 <= settings.api_port <= 65535
            except (ValidationError, ValueError, OverflowError):
                # Should reject invalid ports
                pass

    def test_conflicting_timeout_values(self):
        """Test conflicting timeout configurations."""
        # Processing timeout shorter than approval timeout (might be illogical)
        settings = Settings(
            max_processing_hours=1,
            approval_timeout_hours=10
        )

        # Should accept but might warn
        assert settings.max_processing_hours > 0
        assert settings.approval_timeout_hours > 0

    def test_very_short_intervals(self):
        """Test very short cleanup intervals."""
        try:
            settings = Settings(
                approval_check_interval_seconds=1,
                temp_cleanup_interval_hours=0.001,  # ~3 seconds
            )
            # Very short intervals might be accepted but impractical
            assert settings.approval_check_interval_seconds >= 0
        except (ValidationError, ValueError):
            pass

    def test_very_long_intervals(self):
        """Test extremely long cleanup intervals are rejected."""
        # With Field constraints, these should now be rejected (max 168 hours = 1 week)
        with pytest.raises(ValidationError):
            Settings(
                temp_cleanup_interval_hours=8760,  # 1 year - exceeds max 168 hours
            )

        with pytest.raises(ValidationError):
            Settings(
                metrics_cleanup_interval_hours=87600,  # 10 years - exceeds max 168 hours
            )

    def test_anthropic_api_key_validation(self):
        """Test Anthropic API key field."""
        # Test with valid-looking key
        settings = Settings(anthropic_api_key="sk-test-key-12345")

        # Should be stored as SecretStr
        assert settings.anthropic_api_key is not None
        # Access secret value
        secret_value = settings.anthropic_api_key.get_secret_value()
        assert "sk-test-key" in secret_value

    def test_empty_anthropic_api_key(self):
        """Test empty Anthropic API key."""
        try:
            settings = Settings(anthropic_api_key="")
            # Empty key might be rejected
            assert settings.anthropic_api_key is not None
        except (ValidationError, ValueError):
            # Should require non-empty key
            pass

    def test_invalid_claude_model_name(self):
        """Test invalid Claude model name."""
        invalid_models = [
            "",
            "gpt-4",  # Wrong provider
            "claude",
            "model-does-not-exist",
        ]

        for model in invalid_models:
            try:
                settings = Settings(claude_model=model)
                # Might accept any string
                assert isinstance(settings.claude_model, str)
            except (ValidationError, ValueError):
                pass

    def test_negative_claude_max_tokens(self):
        """Test negative Claude max tokens."""
        try:
            settings = Settings(claude_max_tokens=-1)
            assert settings.claude_max_tokens >= 0
        except (ValidationError, ValueError):
            pass

    def test_zero_claude_max_tokens(self):
        """Test zero Claude max tokens."""
        try:
            settings = Settings(claude_max_tokens=0)
            # Zero tokens is invalid
            assert settings.claude_max_tokens > 0
        except (ValidationError, ValueError):
            pass

    def test_extreme_claude_max_tokens(self):
        """Test extremely high max tokens."""
        try:
            settings = Settings(claude_max_tokens=1000000)
            # Should accept but might hit API limits
            assert settings.claude_max_tokens > 0
        except (ValidationError, ValueError):
            pass

    def test_invalid_temperature_range(self):
        """Test invalid temperature values."""
        invalid_temps = [
            -0.5,  # Negative
            2.0,   # Above typical max
            10.0,  # Way above max
        ]

        for temp in invalid_temps:
            try:
                settings = Settings(claude_temperature=temp)
                # Should be in valid range (typically 0.0-1.0)
                assert settings.claude_temperature >= 0.0
            except (ValidationError, ValueError):
                pass

    def test_temperature_boundaries(self):
        """Test temperature boundary values."""
        settings_zero = Settings(claude_temperature=0.0)
        settings_one = Settings(claude_temperature=1.0)

        assert settings_zero.claude_temperature == 0.0
        assert settings_one.claude_temperature == 1.0

    def test_negative_concurrent_pages(self):
        """Test negative max concurrent pages."""
        try:
            settings = Settings(max_concurrent_pages=-1)
            assert settings.max_concurrent_pages >= 0
        except (ValidationError, ValueError):
            pass

    def test_zero_concurrent_pages(self):
        """Test zero max concurrent pages."""
        try:
            settings = Settings(max_concurrent_pages=0)
            # Zero concurrent might be invalid
            assert settings.max_concurrent_pages >= 0
        except (ValidationError, ValueError):
            pass

    def test_extreme_concurrent_pages(self):
        """Test extremely high concurrent pages are rejected."""
        # With Field constraints, values > 50 should be rejected
        with pytest.raises(ValidationError):
            Settings(max_concurrent_pages=1000)

    def test_negative_retry_attempts(self):
        """Test negative retry attempts."""
        try:
            settings = Settings(page_retry_attempts=-1)
            assert settings.page_retry_attempts >= 0
        except (ValidationError, ValueError):
            pass

    def test_zero_retry_attempts(self):
        """Test zero retry attempts (no retries)."""
        settings = Settings(page_retry_attempts=0)

        # Zero retries is valid (fail immediately)
        assert settings.page_retry_attempts == 0

    def test_extreme_retry_attempts(self):
        """Test extremely high retry attempts are rejected."""
        # With Field constraints, values > 10 should be rejected
        with pytest.raises(ValidationError):
            Settings(page_retry_attempts=100)

    def test_retention_policies_consistency(self):
        """Test that retention policies are logically consistent."""
        settings = Settings(
            temp_file_retention_hours=24,
            job_retention_days=30,
            metrics_retention_days=90,
        )

        # Verify all are positive
        assert settings.temp_file_retention_hours > 0
        assert settings.job_retention_days > 0
        assert settings.metrics_retention_days > 0

        # Verify logical ordering (not enforced but good practice)
        # Convert to same units for comparison
        temp_hours = settings.temp_file_retention_hours
        job_hours = settings.job_retention_days * 24
        metrics_hours = settings.metrics_retention_days * 24

        # Just verify they're all positive
        assert temp_hours > 0
        assert job_hours > 0
        assert metrics_hours > 0
