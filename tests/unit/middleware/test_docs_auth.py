"""Unit tests for documentation HTTP Basic authentication middleware."""

import pytest
import base64
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from src.middleware.docs_auth import DocsAuthMiddleware


@pytest.fixture
def mock_app():
    """Create mock FastAPI app."""
    return MagicMock()


@pytest.fixture
def mock_settings_with_credentials():
    """Mock settings with docs credentials configured."""
    with patch("src.middleware.docs_auth.settings") as mock_settings:
        mock_settings.docs_username = "testuser"
        mock_settings.docs_password = MagicMock()
        mock_settings.docs_password.get_secret_value.return_value = "testpass"
        yield mock_settings


@pytest.fixture
def mock_settings_no_password():
    """Mock settings without docs password."""
    with patch("src.middleware.docs_auth.settings") as mock_settings:
        mock_settings.docs_username = "testuser"
        mock_settings.docs_password = None
        yield mock_settings


@pytest.fixture
def middleware_with_creds(mock_app, mock_settings_with_credentials):
    """Create middleware instance with credentials."""
    return DocsAuthMiddleware(mock_app)


@pytest.fixture
def middleware_no_creds(mock_app, mock_settings_no_password):
    """Create middleware instance without credentials."""
    return DocsAuthMiddleware(mock_app)


def create_mock_request(path: str, headers: dict = None, client_host: str = "127.0.0.1"):
    """Create mock request."""
    request = MagicMock(spec=Request)
    request.url.path = path
    request.method = "GET"
    request.client = MagicMock()
    request.client.host = client_host

    # Mock headers object with get method
    mock_headers = MagicMock()
    headers_dict = headers or {}
    mock_headers.get = MagicMock(side_effect=lambda key, default=None: headers_dict.get(key, default))
    request.headers = mock_headers

    return request


def create_basic_auth_header(username: str, password: str) -> str:
    """Create HTTP Basic auth header value."""
    credentials = f"{username}:{password}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_valid_credentials_allow_access(middleware_with_creds):
    """Test that valid credentials allow access to docs."""
    # Setup
    auth_header = create_basic_auth_header("testuser", "testpass")
    request = create_mock_request("/docs", {"Authorization": auth_header})
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert call_next.called
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_password_rejects_request(middleware_with_creds):
    """Test that invalid password rejects request."""
    # Setup
    auth_header = create_basic_auth_header("testuser", "wrongpass")
    request = create_mock_request("/docs", {"Authorization": auth_header})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert isinstance(response, JSONResponse)
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_username_rejects_request(middleware_with_creds):
    """Test that invalid username rejects request."""
    # Setup
    auth_header = create_basic_auth_header("wronguser", "testpass")
    request = create_mock_request("/docs", {"Authorization": auth_header})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_authorization_header_rejects(middleware_with_creds):
    """Test that missing Authorization header rejects request."""
    # Setup
    request = create_mock_request("/docs", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_auth_scheme_rejects(middleware_with_creds):
    """Test that non-Basic auth scheme rejects request."""
    # Setup
    request = create_mock_request("/docs", {"Authorization": "Bearer some-token"})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_malformed_basic_auth_rejects(middleware_with_creds):
    """Test that malformed Basic auth rejects request."""
    # Setup - invalid base64
    request = create_mock_request("/docs", {"Authorization": "Basic not-valid-base64!!!"})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_docs_endpoint_requires_auth(middleware_with_creds):
    """Test that /docs endpoint requires auth."""
    # Setup
    request = create_mock_request("/docs", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openapi_endpoint_requires_auth(middleware_with_creds):
    """Test that /openapi.json endpoint requires auth."""
    # Setup
    request = create_mock_request("/openapi.json", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redoc_endpoint_requires_auth(middleware_with_creds):
    """Test that /redoc endpoint requires auth."""
    # Setup
    request = create_mock_request("/redoc", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_docs_endpoint_bypasses_auth(middleware_with_creds):
    """Test that non-docs endpoints bypass auth."""
    # Setup
    request = create_mock_request("/api/documents/submit", {})
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert call_next.called
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_endpoint_bypasses_auth(middleware_with_creds):
    """Test that /health endpoint bypasses auth."""
    # Setup
    request = create_mock_request("/health", {})
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert call_next.called
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_password_configured_rejects_all(middleware_no_creds):
    """Test that middleware without password rejects all docs requests."""
    # Setup
    auth_header = create_basic_auth_header("testuser", "anypass")
    request = create_mock_request("/docs", {"Authorization": auth_header})
    call_next = AsyncMock()

    # Execute
    response = await middleware_no_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_password_with_colon_works(middleware_with_creds):
    """Test that password containing colon works correctly."""
    # Setup - update mock password to contain colon
    with patch("src.middleware.docs_auth.settings") as mock_settings:
        mock_settings.docs_username = "user"
        mock_settings.docs_password = MagicMock()
        mock_settings.docs_password.get_secret_value.return_value = "pass:word:123"

        middleware = DocsAuthMiddleware(MagicMock())
        auth_header = create_basic_auth_header("user", "pass:word:123")
        request = create_mock_request("/docs", {"Authorization": auth_header})
        call_next = AsyncMock(return_value=Response(status_code=200))

        # Execute
        response = await middleware.dispatch(request, call_next)

        # Assert
        assert call_next.called
        assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_www_authenticate_header_in_response(middleware_with_creds):
    """Test that 401 response includes WWW-Authenticate header."""
    # Setup
    request = create_mock_request("/docs", {})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    assert "Basic" in response.headers["WWW-Authenticate"]
    assert "realm=" in response.headers["WWW-Authenticate"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_case_sensitive_username(middleware_with_creds):
    """Test that username validation is case-sensitive."""
    # Setup - try uppercase version of valid username
    auth_header = create_basic_auth_header("TESTUSER", "testpass")
    request = create_mock_request("/docs", {"Authorization": auth_header})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_credentials_without_colon_rejects(middleware_with_creds):
    """Test that credentials without colon separator are rejected."""
    # Setup - manually create malformed auth header
    malformed = base64.b64encode(b"userpassword").decode("utf-8")
    request = create_mock_request("/docs", {"Authorization": f"Basic {malformed}"})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_ip_extraction_from_x_forwarded_for(middleware_with_creds):
    """Test client IP extraction from X-Forwarded-For header."""
    # Setup
    auth_header = create_basic_auth_header("wronguser", "wrongpass")
    request = create_mock_request(
        "/docs",
        {
            "X-Forwarded-For": "1.2.3.4, 5.6.7.8",
            "Authorization": auth_header
        }
    )
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert - just verify it doesn't crash with forwarded header
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_username_rejects(middleware_with_creds):
    """Test that empty username is rejected."""
    # Setup
    auth_header = create_basic_auth_header("", "testpass")
    request = create_mock_request("/docs", {"Authorization": auth_header})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_password_rejects(middleware_with_creds):
    """Test that empty password is rejected."""
    # Setup
    auth_header = create_basic_auth_header("testuser", "")
    request = create_mock_request("/docs", {"Authorization": auth_header})
    call_next = AsyncMock()

    # Execute
    response = await middleware_with_creds.dispatch(request, call_next)

    # Assert
    assert not call_next.called
    assert response.status_code == 401
