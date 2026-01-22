"""Integration tests for LTI 1.3 Canvas integration.

Tests the full LTI launch flow:
1. JWKS endpoint serves valid keys
2. Config endpoint provides Canvas configuration
3. OIDC login initiates auth flow
4. Launch endpoint processes JWT and creates jobs
5. End-to-end flow from Canvas to viewer redirect

Uses testcontainers for Redis/S3 with mocked Canvas responses.

Note: LTI environment variables and fixtures (temp_keys, lti_app, lti_client)
are defined in conftest.py to ensure they're set before any app import.
"""

import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


class TestJWKSEndpoint:
    """Tests for GET /lti/jwks endpoint."""

    def test_jwks_returns_valid_json(self, lti_client, temp_keys):
        """JWKS endpoint returns valid JSON Web Key Set."""
        response = lti_client.get("/lti/jwks")

        assert response.status_code == 200
        data = response.json()

        # Verify JWKS structure
        assert "keys" in data
        assert len(data["keys"]) >= 1

        # Verify key properties
        key = data["keys"][0]
        assert key.get("kty") == "RSA"
        assert key.get("use") == "sig"
        assert key.get("alg") == "RS256"
        assert "kid" in key
        assert "n" in key  # RSA modulus
        assert "e" in key  # RSA exponent

    def test_jwks_is_public(self, lti_client, temp_keys):
        """JWKS endpoint doesn't require authentication."""
        # No API key header
        response = lti_client.get("/lti/jwks")
        assert response.status_code == 200


class TestConfigEndpoint:
    """Tests for GET /lti/config endpoint."""

    def test_config_returns_canvas_json(self, lti_client, temp_keys):
        """Config endpoint returns Canvas Developer Key configuration."""
        response = lti_client.get("/lti/config")

        assert response.status_code == 200
        data = response.json()

        # Verify required fields
        assert "title" in data
        assert data["title"] == "Equalify PDF Reflow"
        assert "oidc_initiation_url" in data
        assert "target_link_uri" in data
        assert "public_jwk_url" in data
        assert "extensions" in data

        # Verify endpoints are properly formed
        assert "/lti/login" in data["oidc_initiation_url"]
        assert "/lti/launch" in data["target_link_uri"]
        assert "/lti/jwks" in data["public_jwk_url"]

    def test_config_includes_file_menu_placement(self, lti_client, temp_keys):
        """Config includes file_menu placement for Canvas."""
        response = lti_client.get("/lti/config")
        data = response.json()

        # Find Canvas extension
        canvas_ext = next(
            (e for e in data.get("extensions", []) if e.get("platform") == "canvas.instructure.com"),
            None,
        )
        assert canvas_ext is not None

        # Verify file_menu placement
        placements = canvas_ext.get("settings", {}).get("placements", [])
        file_menu = next((p for p in placements if p.get("placement") == "file_menu"), None)
        assert file_menu is not None
        assert file_menu.get("accept_media_types") == "application/pdf"
        assert "file_id" in file_menu.get("custom_fields", {})


class TestOIDCLoginEndpoint:
    """Tests for POST /lti/login endpoint."""

    def test_login_redirects_to_canvas(self, lti_client, temp_keys):
        """Login endpoint redirects to Canvas auth URL."""
        # Simulate Canvas OIDC initiation
        form_data = {
            "iss": "https://canvas.test.instructure.com",
            "login_hint": "user123",
            "target_link_uri": "http://testserver/lti/launch",
            "lti_message_hint": "message123",
            "client_id": "10000000000001",
        }

        response = lti_client.post(
            "/lti/login",
            data=form_data,
            follow_redirects=False,
        )

        # Should redirect to Canvas auth
        assert response.status_code == 302
        location = response.headers.get("location", "")

        # Verify redirect URL contains expected parameters
        assert "canvas.test.instructure.com" in location
        assert "client_id=10000000000001" in location
        assert "redirect_uri=" in location
        assert "state=" in location
        assert "nonce=" in location
        assert "login_hint=user123" in location
        assert "response_type=id_token" in location
        assert "response_mode=form_post" in location

    def test_login_rejects_unknown_issuer(self, lti_client, temp_keys):
        """Login endpoint rejects requests from unknown issuers."""
        form_data = {
            "iss": "https://evil.example.com",
            "login_hint": "user123",
            "target_link_uri": "http://testserver/lti/launch",
        }

        response = lti_client.post("/lti/login", data=form_data)

        assert response.status_code == 400
        assert "Unknown issuer" in response.json()["detail"]

    def test_login_requires_login_hint(self, lti_client, temp_keys):
        """Login endpoint requires login_hint parameter."""
        form_data = {
            "iss": "https://canvas.test.instructure.com",
            # Missing login_hint
            "target_link_uri": "http://testserver/lti/launch",
        }

        response = lti_client.post("/lti/login", data=form_data)

        assert response.status_code == 400
        assert "login_hint" in response.json()["detail"]


class TestLaunchEndpoint:
    """Tests for POST /lti/launch endpoint."""

    @pytest.fixture
    def mock_canvas_jwt(self):
        """Create mock Canvas JWT claims."""
        return {
            "iss": "https://canvas.test.instructure.com",
            "sub": "user123",
            "aud": "10000000000001",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "nonce": "test-nonce-123",
            "name": "Test User",
            "email": "test@example.com",
            "https://purl.imsglobal.org/spec/lti/claim/deployment_id": "10000000000001",
            "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiResourceLinkRequest",
            "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": "http://testserver/lti/launch",
            "https://purl.imsglobal.org/spec/lti/claim/resource_link": {
                "id": "resource123",
                "title": "Test PDF",
            },
            "https://purl.imsglobal.org/spec/lti/claim/context": {
                "id": "course123",
                "label": "TEST101",
                "title": "Test Course",
            },
            "https://purl.imsglobal.org/spec/lti/claim/custom": {
                "file_id": "file456",
                "file_name": "test_document.pdf",
                "file_content_type": "application/pdf",
                "file_download_url": "https://canvas.test.instructure.com/files/456/download",
                "course_id": "course123",
            },
        }

    def test_launch_rejects_missing_state(self, lti_client, temp_keys):
        """Launch endpoint rejects requests without state."""
        response = lti_client.post(
            "/lti/launch",
            data={"id_token": "fake.jwt.token"},
        )

        assert response.status_code == 200  # Returns HTML error page
        assert "Missing state" in response.text

    def test_launch_rejects_invalid_state(self, lti_client, temp_keys):
        """Launch endpoint rejects invalid/expired state."""
        response = lti_client.post(
            "/lti/launch",
            data={
                "id_token": "fake.jwt.token",
                "state": "invalid-state-token",
            },
        )

        assert response.status_code == 200  # Returns HTML error page
        assert "Invalid or expired state" in response.text


@pytest.mark.integration
@pytest.mark.requires_redis
class TestLTIEndToEnd:
    """End-to-end tests with real Redis."""

    @pytest_asyncio.fixture
    async def setup_lti_state(self, real_redis_client, temp_keys):
        """Set up LTI state in Redis for testing."""
        from src.lti.adapters import RedisLaunchDataStorage

        storage = RedisLaunchDataStorage(real_redis_client)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        await storage.set_value_async(
            state,
            {
                "nonce": nonce,
                "target_link_uri": "http://testserver/lti/launch",
                "client_id": "10000000000001",
                "deployment_id": "10000000000001",
            },
        )

        return {"state": state, "nonce": nonce, "storage": storage}

    @pytest.mark.asyncio
    async def test_redis_state_storage(self, real_redis_client, temp_keys):
        """Test Redis state storage for LTI OIDC flow."""
        from src.lti.adapters import RedisLaunchDataStorage

        storage = RedisLaunchDataStorage(real_redis_client)

        # Store state
        state_key = "test-state-123"
        state_data = {
            "nonce": "test-nonce",
            "target_link_uri": "http://example.com/launch",
            "client_id": "12345",
        }

        await storage.set_value_async(state_key, state_data)

        # Retrieve state
        retrieved = await storage.get_value_async(state_key)
        assert retrieved == state_data

        # Check nonce uniqueness
        nonce_valid = await storage.check_nonce_async("unique-nonce", "https://canvas.example.com")
        assert nonce_valid is True

        # Same nonce should be rejected (replay protection)
        nonce_replay = await storage.check_nonce_async("unique-nonce", "https://canvas.example.com")
        assert nonce_replay is False

        # Cleanup state
        await storage.cleanup_state_async(state_key)
        retrieved_after = await storage.get_value_async(state_key)
        assert retrieved_after is None

    @pytest.mark.asyncio
    async def test_full_lti_flow_with_mocked_jwt(
        self, real_redis_client, storage_service, job_service, temp_keys
    ):
        """Test full LTI flow with mocked JWT validation and Canvas file download."""
        from src.lti.adapters import RedisLaunchDataStorage
        from src.lti.models import FileMenuContent, LTIContext, LTILaunchData, LTIResourceLink, LTIUser
        from src.lti.service import LTIService

        # Set up state
        storage = RedisLaunchDataStorage(real_redis_client)
        nonce = secrets.token_urlsafe(32)

        # Create launch data (simulating parsed JWT)
        launch_data = LTILaunchData(
            deployment_id="10000000000001",
            target_link_uri="http://testserver/lti/launch",
            message_type="LtiResourceLinkRequest",
            user=LTIUser(
                sub="user123",
                name="Test User",
                email="test@example.com",
            ),
            context=LTIContext(
                id="course123",
                label="TEST101",
                title="Test Course",
            ),
            resource_link=LTIResourceLink(
                id="resource123",
                title="Test PDF",
            ),
            file_menu=FileMenuContent(
                file_id="file456",
                file_name="test_document.pdf",
                file_content_type="application/pdf",
                file_download_url="https://canvas.test.instructure.com/files/456/download",
                course_id="course123",
            ),
            canvas_user_id="canvas_user_123",
            canvas_course_id="canvas_course_123",
        )

        # Mock Canvas file download
        sample_pdf = (
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 "
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
            b"/MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n"
            b"4 0 obj\n<< /Length 44 >>\nstream\nBT\n/F1 12 Tf\n100 700 Td\n"
            b"(Test PDF) Tj\nET\nendstream\nendobj\nxref\n0 5\n"
            b"0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n"
            b"0000000115 00000 n\n0000000317 00000 n\ntrailer\n"
            b"<< /Size 5 /Root 1 0 R >>\nstartxref\n410\n%%EOF"
        )

        # Create LTI service
        lti_service = LTIService(storage_service, job_service)

        # Mock the Canvas file download
        with patch("src.lti.service.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = sample_pdf
            mock_response.headers = {"content-type": "application/pdf"}
            mock_response.raise_for_status = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # Process launch
            response, s3_key = await lti_service.process_file_menu_launch(launch_data)

        # Verify response
        assert response.job_id is not None
        assert response.viewer_url == f"/viewer/{response.job_id}"
        assert response.file_name == "test_document.pdf"

        # Verify job was created in Redis
        job = await job_service.get_job(response.job_id)
        assert job is not None
        assert job["status"] == "processing"
        assert job["pii_skipped"] == "true"
        assert job["pii_skip_reason"] == "LTI launch from authenticated Canvas user"
        assert job["source"] == "lti"
        assert job["lti_canvas_file_id"] == "file456"
        # Note: file_menu.course_id takes precedence over launch_data.canvas_course_id
        assert job["lti_canvas_course_id"] == "course123"

        # Verify file was stored in S3
        file_exists = await storage_service.file_exists(
            storage_service.temp_bucket,
            s3_key,
        )
        assert file_exists is True


class TestLTIHealthEndpoint:
    """Tests for GET /lti/health endpoint."""

    def test_health_returns_ok(self, lti_client, temp_keys):
        """Health endpoint returns OK status."""
        response = lti_client.get("/lti/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["lti_enabled"] == "True"


class TestLTIServiceErrors:
    """Tests for LTI service error handling."""

    @pytest.mark.asyncio
    async def test_missing_file_menu_data(self, storage_service, job_service, temp_keys):
        """Service raises error when file menu data is missing."""
        from src.lti.models import LTILaunchData, LTIUser
        from src.lti.service import LTIService, LTIServiceError

        launch_data = LTILaunchData(
            deployment_id="10000000000001",
            target_link_uri="http://testserver/lti/launch",
            message_type="LtiResourceLinkRequest",
            user=LTIUser(sub="user123"),
            file_menu=None,  # Missing file menu
        )

        lti_service = LTIService(storage_service, job_service)

        with pytest.raises(LTIServiceError, match="file menu data"):
            await lti_service.process_file_menu_launch(launch_data)

    @pytest.mark.asyncio
    async def test_missing_download_url(self, storage_service, job_service, temp_keys):
        """Service raises error when download URL is missing."""
        from src.lti.models import FileMenuContent, LTILaunchData, LTIUser
        from src.lti.service import LTIService, LTIServiceError

        launch_data = LTILaunchData(
            deployment_id="10000000000001",
            target_link_uri="http://testserver/lti/launch",
            message_type="LtiResourceLinkRequest",
            user=LTIUser(sub="user123"),
            file_menu=FileMenuContent(
                file_id="123",
                file_name="test.pdf",
                file_download_url=None,  # Missing URL
            ),
        )

        lti_service = LTIService(storage_service, job_service)

        with pytest.raises(LTIServiceError, match="download URL"):
            await lti_service.process_file_menu_launch(launch_data)

    @pytest.mark.asyncio
    async def test_canvas_download_failure(self, storage_service, job_service, temp_keys):
        """Service handles Canvas download failures gracefully."""
        import httpx

        from src.lti.models import FileMenuContent, LTILaunchData, LTIUser
        from src.lti.service import LTIService, LTIServiceError

        launch_data = LTILaunchData(
            deployment_id="10000000000001",
            target_link_uri="http://testserver/lti/launch",
            message_type="LtiResourceLinkRequest",
            user=LTIUser(sub="user123"),
            file_menu=FileMenuContent(
                file_id="123",
                file_name="test.pdf",
                file_download_url="https://canvas.test.instructure.com/files/123/download",
            ),
        )

        lti_service = LTIService(storage_service, job_service)

        # Mock httpx to raise an error
        with patch("src.lti.service.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(LTIServiceError, match="Timeout"):
                await lti_service.process_file_menu_launch(launch_data)
