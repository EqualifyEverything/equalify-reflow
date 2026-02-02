"""Unit tests for main.py lifespan and conditional router registration.

Tests cover:
- Canvas file worker is always started when workers are enabled
- Canvas file worker is NOT started when workers are disabled
- Canvas config router is registered when canvas_autopublish_enabled=True
- Dashboard static files are mounted at /static/canvas when LTI is enabled
"""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_redis_client():
    """Mock Redis client for lifespan tests."""
    return AsyncMock()


@pytest.fixture
def mock_lifespan_dependencies(mock_redis_client):
    """Patch all lifespan dependencies so it can run without real services."""

    async def mock_redis_gen():
        yield mock_redis_client

    patches = {
        "redis": patch("src.main.get_redis_client", return_value=mock_redis_gen()),
        "rate_limit": patch("src.main.RateLimitService"),
        "pii_worker": patch("src.main.start_pii_worker", new_callable=AsyncMock),
        "timeout_worker": patch("src.main.start_timeout_worker", new_callable=AsyncMock),
        "canvas_worker": patch("src.main.start_canvas_file_worker", new_callable=AsyncMock),
    }

    started = {}
    for name, p in patches.items():
        started[name] = p.start()

    yield started

    for p in patches.values():
        p.stop()


class TestLifespanCanvasWorkerStartup:
    """Tests for canvas file worker startup in the lifespan function."""

    @pytest.mark.asyncio
    async def test_canvas_worker_started_when_autopublish_enabled(self, mock_lifespan_dependencies):
        """Canvas file worker task is created when canvas_autopublish_enabled=True."""
        from src.main import app

        with patch("src.main.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            mock_settings.disable_workers = False
            mock_settings.canvas_autopublish_enabled = True
            mock_settings.log_level = "INFO"

            async with app.router.lifespan_context(app):
                pass

        mock_lifespan_dependencies["canvas_worker"].assert_called_once()

    @pytest.mark.asyncio
    async def test_canvas_worker_started_when_autopublish_disabled(self, mock_lifespan_dependencies):
        """Canvas file worker task is created even when canvas_autopublish_enabled=False."""
        from src.main import app

        with patch("src.main.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            mock_settings.disable_workers = False
            mock_settings.canvas_autopublish_enabled = False
            mock_settings.log_level = "INFO"

            async with app.router.lifespan_context(app):
                pass

        mock_lifespan_dependencies["canvas_worker"].assert_called_once()

    @pytest.mark.asyncio
    async def test_canvas_worker_not_started_when_workers_disabled(self, mock_lifespan_dependencies):
        """Canvas file worker is NOT started when disable_workers=True, even if autopublish is on."""
        from src.main import app

        with patch("src.main.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            mock_settings.disable_workers = True
            mock_settings.canvas_autopublish_enabled = True
            mock_settings.log_level = "INFO"

            async with app.router.lifespan_context(app):
                pass

        mock_lifespan_dependencies["canvas_worker"].assert_not_called()
        mock_lifespan_dependencies["pii_worker"].assert_not_called()
        mock_lifespan_dependencies["timeout_worker"].assert_not_called()

    @pytest.mark.asyncio
    async def test_all_workers_always_started(self, mock_lifespan_dependencies):
        """All workers are always started when workers are not disabled."""
        from src.main import app

        with patch("src.main.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            mock_settings.disable_workers = False
            mock_settings.canvas_autopublish_enabled = False
            mock_settings.log_level = "INFO"

            async with app.router.lifespan_context(app):
                pass

        mock_lifespan_dependencies["pii_worker"].assert_called_once()
        mock_lifespan_dependencies["timeout_worker"].assert_called_once()
        mock_lifespan_dependencies["canvas_worker"].assert_called_once()


class TestCanvasConfigRouterRegistration:
    """Tests for conditional canvas config router registration in main.py."""

    def test_canvas_routes_present_on_app(self):
        """Canvas config routes are present on the app (registered at import time or by test helper)."""
        from src.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        canvas_routes = [p for p in route_paths if "/canvas/courses" in p]
        assert len(canvas_routes) > 0, "Canvas config routes should be registered on the app"

    def test_canvas_config_router_has_expected_endpoints(self):
        """Canvas config router defines all expected endpoint paths."""
        from src.api.canvas_config import router

        route_paths = [r.path for r in router.routes]
        prefix = router.prefix
        assert f"{prefix}/{{course_id}}/config" in route_paths
        assert f"{prefix}/{{course_id}}/documents" in route_paths
        assert f"{prefix}/{{course_id}}/documents/{{file_id}}" in route_paths
        assert f"{prefix}/{{course_id}}/documents/{{file_id}}/process" in route_paths
        assert f"{prefix}/{{course_id}}/documents/{{file_id}}/retry" in route_paths
        assert f"{prefix}/{{course_id}}/documents/{{file_id}}/publish" in route_paths

    def test_canvas_config_registration_is_gated_by_feature_flag(self):
        """Registration code is conditional on canvas_autopublish_enabled setting."""
        import ast
        from pathlib import Path

        main_source = Path("src/main.py").read_text()
        tree = ast.parse(main_source)

        # Find the `if settings.canvas_autopublish_enabled:` block
        found_gate = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                if (
                    isinstance(test, ast.Attribute)
                    and isinstance(test.value, ast.Name)
                    and test.value.id == "settings"
                    and test.attr == "canvas_autopublish_enabled"
                ):
                    # Verify include_router is called inside this block
                    body_source = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                    assert "include_router" in body_source
                    found_gate = True

        assert found_gate, (
            "src/main.py must gate canvas config router registration behind `if settings.canvas_autopublish_enabled:`"
        )


class TestDashboardStaticFilesMount:
    """Tests that main.py mounts the compiled Tailwind CSS static files directory."""

    def test_static_canvas_mount_present_on_app(self):
        """The /static/canvas mount is present on the app when LTI is enabled."""
        from src.main import app

        mount_paths = [getattr(r, "path", "") for r in app.routes]
        assert "/static/canvas" in mount_paths, "Static files mount at /static/canvas should be present on the app"

    def test_static_canvas_mount_named_correctly(self):
        """The /static/canvas mount has the expected name 'canvas-static'."""
        from src.main import app

        for route in app.routes:
            if getattr(route, "path", "") == "/static/canvas":
                assert route.name == "canvas-static"
                return
        pytest.fail("No mount found at /static/canvas")

    def test_static_canvas_mount_is_starlette_mount(self):
        """The /static/canvas route is a Starlette Mount with a StaticFiles app."""
        from src.main import app
        from starlette.routing import Mount

        for route in app.routes:
            if getattr(route, "path", "") == "/static/canvas":
                assert isinstance(route, Mount), "Route should be a Starlette Mount"
                return
        pytest.fail("No mount found at /static/canvas")

    def test_dist_directory_exists(self):
        """The canvas/static/dist directory exists with CSS files."""
        from pathlib import Path

        dist_dir = Path(__file__).resolve().parents[2] / "src" / "canvas" / "static" / "dist"
        assert dist_dir.exists(), f"Expected dist directory at {dist_dir}"
        css_files = list(dist_dir.glob("*.css"))
        assert len(css_files) > 0, "dist directory should contain at least one CSS file"

    def test_static_mount_gated_by_lti_enabled(self):
        """Static files mount is inside the `if settings.lti_enabled:` block."""
        import ast
        from pathlib import Path

        main_source = Path("src/main.py").read_text()
        tree = ast.parse(main_source)

        found_mount_in_lti_block = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                if (
                    isinstance(test, ast.Attribute)
                    and isinstance(test.value, ast.Name)
                    and test.value.id == "settings"
                    and test.attr == "lti_enabled"
                ):
                    # Check for app.mount call with "/static/canvas" inside this block
                    block_source = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                    if "static/canvas" in block_source or "StaticFiles" in block_source:
                        found_mount_in_lti_block = True

        assert found_mount_in_lti_block, "src/main.py must mount /static/canvas inside `if settings.lti_enabled:` block"
