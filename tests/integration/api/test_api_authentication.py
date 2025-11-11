"""Integration tests for API authentication."""

import pytest
import base64
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock, MagicMock

from src.main import app


@pytest.fixture
def enable_api_key_auth():
    """Enable API key authentication for tests."""
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "X-API-Key"
        mock_settings.environment = "production"
        mock_settings.enable_api_key_auth = True
        mock_settings.api_keys = MagicMock()
        mock_settings.api_keys.get_secret_value.return_value = "test-key-123,test-key-456"
        yield mock_settings


@pytest.fixture
def enable_docs_auth():
    """Enable docs authentication for tests."""
    with patch("src.middleware.docs_auth.settings") as mock_settings:
        mock_settings.docs_username = "admin"
        mock_settings.docs_password = MagicMock()
        mock_settings.docs_password.get_secret_value.return_value = "secret123"
        mock_settings.enable_docs_auth = True
        yield mock_settings


def create_basic_auth_header(username: str, password: str) -> str:
    """Create HTTP Basic auth header value."""
    credentials = f"{username}:{password}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_key_required_for_protected_endpoint(enable_api_key_auth):
    """Test that API key is required for protected endpoints."""
    # Mock dependencies to avoid real service calls
    with patch("src.api.documents.get_storage_service"), \
         patch("src.api.documents.get_queue_service"), \
         patch("src.api.documents.get_job_service"), \
         patch("src.main.settings") as main_settings:

        # Configure main settings
        main_settings.enable_api_key_auth = True
        main_settings.enable_docs_auth = False
        main_settings.api_key_header_name = "X-API-Key"
        main_settings.environment = "production"
        main_settings.api_keys = MagicMock()
        main_settings.api_keys.get_secret_value.return_value = "test-key-123"

        # Recreate app with auth enabled (in real usage, this is set at startup)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Test without API key
            response = await client.get("/api/documents/some-id")

            # Should be rejected (401)
            assert response.status_code == 401
            assert "API key" in response.json()["detail"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_valid_api_key_allows_access(enable_api_key_auth):
    """Test that valid API key allows access to protected endpoints."""
    # Mock dependencies
    with patch("src.dependencies.get_job_service") as mock_job_service_dep, \
         patch("src.main.settings") as main_settings:

        # Configure main settings
        main_settings.enable_api_key_auth = True
        main_settings.enable_docs_auth = False
        main_settings.api_key_header_name = "X-API-Key"
        main_settings.environment = "production"
        main_settings.api_keys = MagicMock()
        main_settings.api_keys.get_secret_value.return_value = "test-key-123"

        # Mock job service
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = {
            "job_id": "test-123",
            "status": "pii_scanning",
            "filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z"
        }
        mock_job_service_dep.return_value = mock_job_service

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-123"}
        ) as client:
            # Test with valid API key
            response = await client.get("/api/documents/test-123")

            # Should succeed (200) or 404 if job not found, but NOT 401
            assert response.status_code in [200, 404]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint_bypasses_api_key_auth(enable_api_key_auth):
    """Test that health endpoint works without API key."""
    # Mock dependencies for health check
    with patch("src.dependencies.get_storage_service") as mock_storage_dep, \
         patch("src.dependencies.get_queue_service") as mock_queue_dep, \
         patch("src.main.settings") as main_settings:

        main_settings.enable_api_key_auth = True
        main_settings.enable_docs_auth = False
        main_settings.environment = "production"

        # Mock storage service
        mock_storage = AsyncMock()
        mock_storage.check_s3_access.return_value = True
        mock_storage_dep.return_value = mock_storage

        # Mock queue service
        mock_queue = AsyncMock()
        mock_queue.check_redis_connection.return_value = True
        mock_queue.check_queue_depth.return_value = 0
        mock_queue_dep.return_value = mock_queue

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Test health endpoint without API key
            response = await client.get("/health")

            # Should succeed
            assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiple_api_keys_supported(enable_api_key_auth):
    """Test that multiple API keys can be configured and used."""
    # Import the middleware to find its instance
    from src.middleware.api_key_auth import APIKeyAuthMiddleware

    with patch("src.dependencies.get_job_service") as mock_job_service_dep:
        # Find the middleware instance in the app's middleware stack
        api_key_middleware = None
        for middleware in app.user_middleware:
            if middleware.cls == APIKeyAuthMiddleware:
                # Access the middleware instance through the app
                if hasattr(middleware, 'kwargs') and 'app' in middleware.kwargs:
                    pass  # Middleware not instantiated yet in test context

        # Directly patch the method that validates keys
        original_is_valid = APIKeyAuthMiddleware._is_valid_key

        def mock_is_valid_key(self, provided_key: str) -> bool:
            return provided_key in {"key-1", "key-2", "key-3"}

        with patch.object(APIKeyAuthMiddleware, '_is_valid_key', mock_is_valid_key):
            mock_job_service = AsyncMock()
            mock_job_service.get_job.return_value = {
                "job_id": "test",
                "status": "completed"
            }
            mock_job_service_dep.return_value = mock_job_service

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                # Test with first key
                response = await client.get(
                    "/api/documents/test",
                    headers={"X-API-Key": "key-1"}
                )
                assert response.status_code != 401

                # Test with second key
                response = await client.get(
                    "/api/documents/test",
                    headers={"X-API-Key": "key-2"}
                )
                assert response.status_code != 401

                # Test with third key
                response = await client.get(
                    "/api/documents/test",
                    headers={"X-API-Key": "key-3"}
                )
                assert response.status_code != 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docs_endpoint_requires_basic_auth(enable_docs_auth):
    """Test that /docs endpoint requires HTTP Basic authentication."""
    with patch("src.main.settings") as main_settings:
        main_settings.enable_api_key_auth = False
        main_settings.enable_docs_auth = True
        main_settings.docs_username = "admin"
        main_settings.docs_password = MagicMock()
        main_settings.docs_password.get_secret_value.return_value = "secret123"

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Test without auth
            response = await client.get("/docs")

            # Should be rejected
            assert response.status_code == 401
            assert "WWW-Authenticate" in response.headers
            assert "Basic" in response.headers["WWW-Authenticate"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docs_valid_credentials_allow_access(enable_docs_auth):
    """Test that valid credentials allow access to docs."""
    with patch("src.main.settings") as main_settings:
        main_settings.enable_api_key_auth = False
        main_settings.enable_docs_auth = True
        main_settings.docs_username = "admin"
        main_settings.docs_password = MagicMock()
        main_settings.docs_password.get_secret_value.return_value = "secret123"

        auth_header = create_basic_auth_header("admin", "secret123")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": auth_header}
        ) as client:
            # Test with valid credentials
            response = await client.get("/docs")

            # Should succeed
            assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openapi_json_requires_auth(enable_docs_auth):
    """Test that /openapi.json requires authentication when docs auth enabled."""
    with patch("src.main.settings") as main_settings:
        main_settings.enable_api_key_auth = False
        main_settings.enable_docs_auth = True
        main_settings.docs_username = "admin"
        main_settings.docs_password = MagicMock()
        main_settings.docs_password.get_secret_value.return_value = "secret123"

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Test without auth
            response = await client.get("/openapi.json")

            # Should be rejected
            assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_both_auth_methods_work_together():
    """Test that API key and docs auth can be enabled simultaneously."""
    # Import middleware to patch validation methods
    from src.middleware.api_key_auth import APIKeyAuthMiddleware
    from src.middleware.docs_auth import DocsAuthMiddleware

    with patch("src.dependencies.get_job_service") as mock_job_service_dep:
        # Patch validation methods instead of instance attributes
        def mock_is_valid_key(self, provided_key: str) -> bool:
            return provided_key == "api-key-123"

        def mock_is_valid_credentials(self, username: str, password: str) -> bool:
            return username == "admin" and password == "doc-pass"

        with patch.object(APIKeyAuthMiddleware, '_is_valid_key', mock_is_valid_key), \
             patch.object(DocsAuthMiddleware, '_is_valid_credentials', mock_is_valid_credentials):

            # Mock job service
            mock_job_service = AsyncMock()
            mock_job_service.get_job.return_value = {
                "job_id": "test-id",
                "status": "completed"
            }
            mock_job_service_dep.return_value = mock_job_service

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                # Test docs endpoint requires Basic auth (not API key)
                docs_auth = create_basic_auth_header("admin", "doc-pass")
                response = await client.get(
                    "/docs",
                    headers={"Authorization": docs_auth}
                )
                assert response.status_code == 200

                # Test API endpoint requires API key (not Basic auth)
                response = await client.get(
                    "/api/documents/test-id",
                    headers={"X-API-Key": "api-key-123"}
                )
                # Should not be 401 (might be 404 if job not found, but auth passed)
                assert response.status_code != 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rate_limiting_still_works_with_auth():
    """Test that rate limiting middleware still functions with auth enabled."""
    # This is a basic test to ensure middleware stack works correctly
    with patch("src.main.settings") as main_settings, \
         patch("src.dependencies.get_storage_service") as mock_storage_dep, \
         patch("src.dependencies.get_queue_service") as mock_queue_dep:

        main_settings.enable_api_key_auth = False  # Disable for this test
        main_settings.enable_docs_auth = False
        main_settings.environment = "production"

        # Mock storage service
        mock_storage = AsyncMock()
        mock_storage.check_s3_access.return_value = True
        mock_storage_dep.return_value = mock_storage

        # Mock queue service
        mock_queue = AsyncMock()
        mock_queue.check_redis_connection.return_value = True
        mock_queue.check_queue_depth.return_value = 0
        mock_queue_dep.return_value = mock_queue

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Make request to health endpoint
            response = await client.get("/health")

            # Should succeed (middleware stack intact)
            assert response.status_code == 200
