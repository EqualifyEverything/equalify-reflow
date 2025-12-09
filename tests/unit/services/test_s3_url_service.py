"""Unit tests for S3URLService."""

import pytest
from src.config import settings
from src.services.s3_url_service import S3URLService


@pytest.fixture
def url_service(mock_s3_client):
    """Create URL service with mock client."""
    return S3URLService(
        s3_client=mock_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


class TestGetPresignedUrl:
    """Tests for get_presigned_url method."""

    @pytest.mark.asyncio
    async def test_generate_presigned_url(self, url_service, mock_s3_client):
        """Test presigned URL generation."""
        expected_url = "https://s3.amazonaws.com/bucket/key?signature=..."
        mock_s3_client.generate_presigned_url.return_value = expected_url

        url = await url_service.get_presigned_url("bucket", "key", expiration=3600)

        assert url == expected_url
        mock_s3_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "bucket", "Key": "key"},
            ExpiresIn=3600
        )

    @pytest.mark.asyncio
    async def test_presigned_url_default_expiration(self, url_service, mock_s3_client):
        """Test default expiration time."""
        mock_s3_client.generate_presigned_url.return_value = "https://url"

        await url_service.get_presigned_url("bucket", "key")

        call_kwargs = mock_s3_client.generate_presigned_url.call_args.kwargs
        assert call_kwargs["ExpiresIn"] == 3600  # Default 1 hour

    @pytest.mark.asyncio
    async def test_presigned_url_failure(self, url_service, mock_s3_client):
        """Test handling of URL generation failure."""
        from fastapi import HTTPException

        mock_s3_client.generate_presigned_url.side_effect = Exception("Error")

        with pytest.raises(HTTPException) as exc:
            await url_service.get_presigned_url("bucket", "key")

        assert exc.value.status_code == 500
        assert "Failed to generate presigned URL" in exc.value.detail


class TestGetResultUrl:
    """Tests for get_result_url method."""

    @pytest.mark.asyncio
    async def test_get_result_url(self, url_service):
        """Test result URL generation."""
        url = url_service.get_result_url("job123", "md")

        assert "job123.md" in url
        assert settings.s3_results_bucket in url

    @pytest.mark.asyncio
    async def test_get_result_url_with_extension(self, url_service):
        """Test result URL generation with custom extension."""
        url = url_service.get_result_url("job456", "txt")

        assert "job456.txt" in url


class TestBuildPublicUrl:
    """Tests for build_public_url method."""

    @pytest.mark.asyncio
    async def test_build_public_url_localstack(self, url_service, monkeypatch):
        """Test URL generation for LocalStack environment."""
        # Mock LocalStack environment
        monkeypatch.setattr(settings, "s3_public_url", "http://localhost:4566")

        url = url_service.build_public_url("results-bucket", "results/abc-123.md")

        assert url == "http://localhost:4566/results-bucket/results/abc-123.md"
        assert "localhost" in url
        assert "results/abc-123.md" in url

    @pytest.mark.asyncio
    async def test_build_public_url_aws_production(self, url_service, monkeypatch):
        """Test URL generation for AWS production environment."""
        # Mock AWS production environment (no public URL)
        monkeypatch.setattr(settings, "s3_public_url", None)
        monkeypatch.setattr(settings, "aws_region", "us-east-1")

        url = url_service.build_public_url("results-bucket", "results/xyz-789.md")

        expected_url = "https://results-bucket.s3.us-east-1.amazonaws.com/results/xyz-789.md"
        assert url == expected_url
        assert url.startswith("https://")
        assert ".s3.us-east-1.amazonaws.com" in url
        assert "results/xyz-789.md" in url

    @pytest.mark.asyncio
    async def test_build_public_url_aws_with_custom_region(self, url_service, monkeypatch):
        """Test URL generation for AWS with custom region."""
        monkeypatch.setattr(settings, "s3_public_url", None)
        monkeypatch.setattr(settings, "aws_region", "eu-west-1")

        url = url_service.build_public_url("results-bucket", "results/test.md")

        assert ".s3.eu-west-1.amazonaws.com" in url
        assert url.startswith("https://")

    @pytest.mark.asyncio
    async def test_build_public_url_aws_fallback_region(self, url_service, monkeypatch):
        """Test URL generation falls back to us-east-1 if region not set."""
        monkeypatch.setattr(settings, "s3_public_url", None)
        monkeypatch.setattr(settings, "aws_region", None)

        url = url_service.build_public_url("results-bucket", "results/test.md")

        assert ".s3.us-east-1.amazonaws.com" in url
        assert url.startswith("https://")


class TestGenerateUrl:
    """Tests for generate_url method (API Refactoring Phase 1)."""

    @pytest.mark.asyncio
    async def test_generate_url_localstack(self, url_service, monkeypatch):
        """Test URL generation for LocalStack environment."""
        # Mock LocalStack environment
        monkeypatch.setattr(settings, "s3_public_url", "http://localhost:4566")

        url = await url_service.generate_url("results/abc-123.md")

        assert url == f"http://localhost:4566/{settings.s3_results_bucket}/results/abc-123.md"
        assert "localhost" in url
        assert "results/abc-123.md" in url

    @pytest.mark.asyncio
    async def test_generate_url_localstack_with_custom_bucket(self, url_service, monkeypatch):
        """Test URL generation for LocalStack with custom bucket."""
        monkeypatch.setattr(settings, "s3_public_url", "http://localhost:4566")

        url = await url_service.generate_url(
            "temp/job-456/pages/page-1.png",
            bucket="custom-bucket"
        )

        assert url == "http://localhost:4566/custom-bucket/temp/job-456/pages/page-1.png"
        assert "custom-bucket" in url

    @pytest.mark.asyncio
    async def test_generate_url_aws_production(self, url_service, monkeypatch):
        """Test URL generation for AWS production environment."""
        # Mock AWS production environment (no endpoint URL)
        monkeypatch.setattr(settings, "s3_public_url", None)
        monkeypatch.setattr(settings, "aws_region", "us-east-1")

        url = await url_service.generate_url("results/xyz-789.md")

        expected_url = f"https://{settings.s3_results_bucket}.s3.us-east-1.amazonaws.com/results/xyz-789.md"
        assert url == expected_url
        assert url.startswith("https://")
        assert ".s3.us-east-1.amazonaws.com" in url
        assert "results/xyz-789.md" in url

    @pytest.mark.asyncio
    async def test_generate_url_aws_with_custom_region(self, url_service, monkeypatch):
        """Test URL generation for AWS with custom region."""
        monkeypatch.setattr(settings, "s3_public_url", None)
        monkeypatch.setattr(settings, "aws_region", "eu-west-1")

        url = await url_service.generate_url("results/test.md")

        assert ".s3.eu-west-1.amazonaws.com" in url
        assert url.startswith("https://")

    @pytest.mark.asyncio
    async def test_generate_url_aws_fallback_region(self, url_service, monkeypatch):
        """Test URL generation falls back to us-east-1 if region not set."""
        monkeypatch.setattr(settings, "s3_public_url", None)
        monkeypatch.setattr(settings, "aws_region", None)

        url = await url_service.generate_url("results/test.md")

        assert ".s3.us-east-1.amazonaws.com" in url
        assert url.startswith("https://")

    @pytest.mark.asyncio
    async def test_generate_url_defaults_to_results_bucket(self, url_service, monkeypatch):
        """Test URL generation defaults to results_bucket when bucket not specified."""
        monkeypatch.setattr(settings, "s3_public_url", "http://localhost:4566")

        url = await url_service.generate_url("some-key.md")

        assert settings.s3_results_bucket in url

    @pytest.mark.asyncio
    async def test_generate_url_with_temp_bucket(self, url_service, monkeypatch):
        """Test URL generation with temp bucket explicitly specified."""
        monkeypatch.setattr(settings, "s3_public_url", "http://localhost:4566")

        url = await url_service.generate_url(
            "job-123/pages/page-1.png",
            bucket=url_service.temp_bucket
        )

        assert url_service.temp_bucket in url
        assert "job-123/pages/page-1.png" in url
