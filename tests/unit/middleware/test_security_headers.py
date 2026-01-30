"""Unit tests for SecurityHeadersMiddleware.

Tests cover:
- LTI routes get CSP frame-ancestors allowing Canvas origin
- LTI routes do not get X-Frame-Options: DENY
- Non-LTI routes get X-Frame-Options: DENY
- Non-LTI routes get CSP frame-ancestors 'none'
- Canvas origin normalization from issuer URL
- Static canvas assets are treated as LTI routes
- LTI login/launch endpoints allow iframe embedding
- Empty canvas_origin falls back to 'self' only
"""

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from src.middleware.security_headers import SecurityHeadersMiddleware

pytestmark = pytest.mark.unit


def _make_app(canvas_origin: str = "") -> FastAPI:
    """Create a minimal FastAPI app with SecurityHeadersMiddleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, canvas_origin=canvas_origin)

    @app.get("/lti/dashboard/{course_id}")
    async def dashboard(course_id: str) -> PlainTextResponse:
        return PlainTextResponse("dashboard")

    @app.get("/lti/dashboard/{course_id}/settings")
    async def dashboard_settings(course_id: str) -> PlainTextResponse:
        return PlainTextResponse("settings")

    @app.get("/static/canvas/dashboard.css")
    async def dashboard_css() -> PlainTextResponse:
        return PlainTextResponse("css", media_type="text/css")

    @app.post("/lti/login")
    async def lti_login() -> PlainTextResponse:
        return PlainTextResponse("login")

    @app.post("/lti/launch")
    async def lti_launch() -> PlainTextResponse:
        return PlainTextResponse("launch")

    @app.get("/api/v1/health")
    async def health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/docs")
    async def docs() -> PlainTextResponse:
        return PlainTextResponse("docs")

    return app


# ===========================================================================
# Dashboard routes: frame-ancestors allows Canvas
# ===========================================================================


class TestDashboardFrameHeaders:
    """Dashboard routes allow Canvas iframe embedding."""

    def test_dashboard_has_csp_frame_ancestors_with_canvas_origin(self):
        """Dashboard sets CSP frame-ancestors to 'self' + Canvas origin."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/lti/dashboard/123")
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self' https://canvas.instructure.com" == csp

    def test_dashboard_no_x_frame_options_deny(self):
        """Dashboard does not have X-Frame-Options: DENY."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/lti/dashboard/123")
        xfo = response.headers.get("x-frame-options", "")
        assert xfo.upper() != "DENY"

    def test_dashboard_no_x_frame_options_header(self):
        """Dashboard does not set X-Frame-Options at all."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/lti/dashboard/123")
        assert "x-frame-options" not in response.headers

    def test_dashboard_settings_has_frame_ancestors(self):
        """Dashboard settings sub-route also gets frame-ancestors."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/lti/dashboard/123/settings")
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self' https://canvas.instructure.com" == csp

    def test_dashboard_static_assets_have_frame_ancestors(self):
        """Static canvas assets also allow Canvas iframe."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/static/canvas/dashboard.css")
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self' https://canvas.instructure.com" == csp

    def test_dashboard_with_empty_canvas_origin_uses_self_only(self):
        """When no Canvas origin configured, dashboard allows only 'self'."""
        client = TestClient(_make_app(canvas_origin=""))
        response = client.get("/lti/dashboard/123")
        csp = response.headers.get("content-security-policy", "")
        assert csp == "frame-ancestors 'self'"

    def test_lti_login_has_frame_ancestors(self):
        """LTI login endpoint allows Canvas iframe (OIDC initiation)."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.post("/lti/login")
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self' https://canvas.instructure.com" == csp
        assert "x-frame-options" not in response.headers

    def test_lti_launch_has_frame_ancestors(self):
        """LTI launch endpoint allows Canvas iframe (JWT post)."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.post("/lti/launch")
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self' https://canvas.instructure.com" == csp
        assert "x-frame-options" not in response.headers


# ===========================================================================
# Non-LTI routes: strict framing denied
# ===========================================================================


class TestNonDashboardFrameHeaders:
    """Non-dashboard routes block iframe embedding."""

    def test_api_routes_have_x_frame_options_deny(self):
        """API routes get X-Frame-Options: DENY."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/api/v1/health")
        assert response.headers.get("x-frame-options") == "DENY"

    def test_api_routes_have_csp_frame_ancestors_none(self):
        """API routes get CSP frame-ancestors 'none'."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/api/v1/health")
        csp = response.headers.get("content-security-policy", "")
        assert csp == "frame-ancestors 'none'"

    def test_docs_route_has_x_frame_options_deny(self):
        """Docs route gets X-Frame-Options: DENY."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com"))
        response = client.get("/docs")
        assert response.headers.get("x-frame-options") == "DENY"


# ===========================================================================
# Canvas origin normalization
# ===========================================================================


class TestCanvasOriginNormalization:
    """Origin extraction from various issuer URL formats."""

    def test_normalizes_full_url_to_origin(self):
        """Strips path from issuer URL, keeping scheme + host."""
        client = TestClient(_make_app(canvas_origin="https://canvas.instructure.com/api/lti/authorize"))
        response = client.get("/lti/dashboard/123")
        csp = response.headers.get("content-security-policy", "")
        assert "https://canvas.instructure.com" in csp
        assert "/api/lti/authorize" not in csp

    def test_handles_origin_with_trailing_slash(self):
        """Handles trailing slash in issuer URL."""
        client = TestClient(_make_app(canvas_origin="https://canvas.example.com/"))
        response = client.get("/lti/dashboard/123")
        csp = response.headers.get("content-security-policy", "")
        assert "https://canvas.example.com" in csp

    def test_handles_plain_origin(self):
        """Handles already-normalized origin."""
        client = TestClient(_make_app(canvas_origin="https://canvas.example.com"))
        response = client.get("/lti/dashboard/123")
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self' https://canvas.example.com" == csp

    def test_handles_origin_with_port(self):
        """Handles origin with a non-standard port (e.g., local Canvas dev)."""
        client = TestClient(_make_app(canvas_origin="http://canvas.docker:3000"))
        response = client.get("/lti/dashboard/123")
        csp = response.headers.get("content-security-policy", "")
        assert "http://canvas.docker:3000" in csp
