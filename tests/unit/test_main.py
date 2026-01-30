"""Unit tests for main.py lifespan and conditional router registration.

Tests cover:
- Canvas file worker is always started when workers are enabled
- Canvas file worker is NOT started when workers are disabled
- Canvas config router is registered when canvas_autopublish_enabled=True
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
