"""Unit tests for main.py lifespan canvas worker startup.

Tests cover:
- Canvas file worker is started when canvas_autopublish_enabled=True
- Canvas file worker is NOT started when canvas_autopublish_enabled=False
- Canvas file worker is NOT started when workers are disabled
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
    async def test_canvas_worker_not_started_when_autopublish_disabled(self, mock_lifespan_dependencies):
        """Canvas file worker task is NOT created when canvas_autopublish_enabled=False."""
        from src.main import app

        with patch("src.main.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            mock_settings.disable_workers = False
            mock_settings.canvas_autopublish_enabled = False
            mock_settings.log_level = "INFO"

            async with app.router.lifespan_context(app):
                pass

        mock_lifespan_dependencies["canvas_worker"].assert_not_called()

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
    async def test_pii_and_timeout_workers_always_started(self, mock_lifespan_dependencies):
        """PII and timeout workers are always started when workers are not disabled."""
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
