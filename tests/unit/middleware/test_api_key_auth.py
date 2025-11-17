"""Unit tests for API key authentication middleware."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from src.middleware.api_key_auth import APIKeyAuthMiddleware


@pytest.fixture
def mock_app():
    """Create mock FastAPI app."""
    return MagicMock()


@pytest.fixture
def mock_settings_with_keys():
    """Mock settings with API keys configured."""
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "X-API-Key"
        mock_settings.environment = "production"
        mock_settings.api_keys = MagicMock()
        mock_settings.api_keys.get_secret_value.return_value = "test-key-1,test-key-2"
        yield mock_settings


@pytest.fixture
def mock_settings_no_keys():
    """Mock settings with no API keys."""
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "X-API-Key"
        mock_settings.environment = "production"
        mock_settings.api_keys = None
        yield mock_settings


@pytest.fixture
def middleware_with_keys(mock_app, mock_settings_with_keys):
    """Create middleware instance with API keys."""
    return APIKeyAuthMiddleware(mock_app)


@pytest.fixture
def middleware_no_keys(mock_app, mock_settings_no_keys):
    """Create middleware instance without API keys."""
    return APIKeyAuthMiddleware(mock_app)


def create_mock_request(path: str, headers: dict = None, client_host: str = "127.0.0.1"):
    """Create mock request."""
    request = MagicMock(spec=Request)
    request.url.path = path
    request.method = "GET"
    request.client = MagicMock()
    request.client.host = client_host
    request.state = MagicMock()

    # Mock headers object with get method
    mock_headers = MagicMock()
    headers_dict = headers or {}
    mock_headers.get = MagicMock(side_effect=lambda key, default=None: headers_dict.get(key, default))
    request.headers = mock_headers

    return request


@pytest.mark.unit
@pytest.mark.asyncio
async def test_valid_api_key_allows_request(middleware_with_keys):
    """Test that valid API key allows request through."""
    # Setup
    request = create_mock_request("/api/documents/submit", {"X-API-Key": "test-key-1"})
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert call_next.called
    assert response.status_code == 200
    assert request.state.api_key == "test-key-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_second_valid_api_key_allows_request(middleware_with_keys):
    """Test that second valid API key also works."""
    # Setup
    request = create_mock_request("/api/documents/submit", {"X-API-Key": "test-key-2"})
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert call_next.called
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_api_key_rejects_request(middleware_with_keys):
    """Test that invalid API key rejects request."""
    # Setup
    request = create_mock_request("/api/documents/submit", {"X-API-Key": "invalid-key"})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert isinstance(response, JSONResponse)
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_api_key_rejects_request(middleware_with_keys):
    """Test that missing API key rejects request."""
    # Setup
    request = create_mock_request("/api/documents/submit", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    assert "Missing API key" in str(response.body)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_configured_keys_rejects_all_requests(middleware_no_keys):
    """Test that middleware without configured keys rejects all requests."""
    # Setup
    request = create_mock_request("/api/documents/submit", {"X-API-Key": "any-key"})
    call_next = AsyncMock()

    # Execute
    response = await middleware_no_keys.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert isinstance(response, JSONResponse)
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_endpoint_bypasses_auth(middleware_with_keys):
    """Test that /health endpoint bypasses authentication."""
    # Setup
    request = create_mock_request("/health", {})
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert call_next.called
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_metrics_endpoint_bypasses_auth(middleware_with_keys):
    """Test that /metrics endpoint bypasses authentication."""
    # Setup
    request = create_mock_request("/metrics", {})
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert call_next.called
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dev_monitoring_bypasses_auth_in_dev(mock_app):
    """Test that dev monitoring endpoints bypass auth in dev environment."""
    # Setup - dev environment
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "X-API-Key"
        mock_settings.environment = "dev"
        mock_settings.api_keys = MagicMock()
        mock_settings.api_keys.get_secret_value.return_value = "test-key-1"

        middleware = APIKeyAuthMiddleware(mock_app)
        request = create_mock_request("/api/dev/monitoring/queues", {})
        call_next = AsyncMock(return_value=Response(status_code=200))

        # Execute
        response = await middleware.dispatch(request, call_next)

        # Assert
        assert call_next.called
        assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dev_monitoring_requires_auth_in_production(middleware_with_keys):
    """Test that dev monitoring endpoints require auth in production."""
    # Setup - production environment (middleware_with_keys is production)
    request = create_mock_request("/api/dev/monitoring/queues", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_case_sensitive_api_key(middleware_with_keys):
    """Test that API key validation is case-sensitive."""
    # Setup - try uppercase version of valid key
    request = create_mock_request("/api/documents/submit", {"X-API-Key": "TEST-KEY-1"})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_whitespace_stripped_from_keys():
    """Test that whitespace is stripped from configured keys."""
    # Setup - keys with whitespace in configuration
    mock_app = MagicMock()
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "X-API-Key"
        mock_settings.environment = "production"
        mock_settings.api_keys = MagicMock()
        mock_settings.api_keys.get_secret_value.return_value = " key-1 , key-2 , key-3 "

        middleware = APIKeyAuthMiddleware(mock_app)
        request = create_mock_request("/api/documents/submit", {"X-API-Key": "key-1"})
        call_next = AsyncMock(return_value=Response(status_code=200))

        # Execute
        response = await middleware.dispatch(request, call_next)

        # Assert
        assert call_next.called
        assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_custom_header_name():
    """Test that custom header name works."""
    # Setup - custom header name
    mock_app = MagicMock()
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "Authorization"
        mock_settings.environment = "production"
        mock_settings.api_keys = MagicMock()
        mock_settings.api_keys.get_secret_value.return_value = "custom-key"

        middleware = APIKeyAuthMiddleware(mock_app)
        request = create_mock_request("/api/documents/submit", {"Authorization": "custom-key"})
        call_next = AsyncMock(return_value=Response(status_code=200))

        # Execute
        response = await middleware.dispatch(request, call_next)

        # Assert
        assert call_next.called
        assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_ip_extraction_from_x_forwarded_for(middleware_with_keys):
    """Test client IP extraction from X-Forwarded-For header."""
    # Setup
    request = create_mock_request(
        "/api/documents/submit",
        {
            "X-Forwarded-For": "1.2.3.4, 5.6.7.8",
            "X-API-Key": "invalid-key"
        }
    )
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert - just verify it doesn't crash with forwarded header
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_www_authenticate_header_in_response(middleware_with_keys):
    """Test that 401 response includes WWW-Authenticate header."""
    # Setup
    request = create_mock_request("/api/documents/submit", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_keys.dispatch(request, call_next)

    # Assert
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    assert "ApiKey" in response.headers["WWW-Authenticate"]


@pytest.mark.unit
def test_load_api_keys_empty_string():
    """Test loading API keys from empty string."""
    # Setup
    mock_app = MagicMock()
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "X-API-Key"
        mock_settings.environment = "production"
        mock_settings.api_keys = MagicMock()
        mock_settings.api_keys.get_secret_value.return_value = "   ,  ,  "

        # Execute
        middleware = APIKeyAuthMiddleware(mock_app)
        loaded_keys = middleware._load_api_keys()

        # Assert - empty strings and whitespace should result in no keys
        assert len(loaded_keys) == 0


@pytest.mark.unit
def test_multiple_keys_loaded_correctly():
    """Test that multiple keys are loaded correctly."""
    # Setup
    mock_app = MagicMock()
    with patch("src.middleware.api_key_auth.settings") as mock_settings:
        mock_settings.api_key_header_name = "X-API-Key"
        mock_settings.environment = "production"
        mock_settings.api_keys = MagicMock()
        mock_settings.api_keys.get_secret_value.return_value = "key1,key2,key3"

        # Execute
        middleware = APIKeyAuthMiddleware(mock_app)
        loaded_keys = middleware._load_api_keys()

        # Assert - keys loaded dynamically
        assert len(loaded_keys) == 3
        assert "key1" in loaded_keys
        assert "key2" in loaded_keys
        assert "key3" in loaded_keys
