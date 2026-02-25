from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestFeedbackClient:
    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset module-level singleton for each test."""
        # Import fresh each time with mocked settings
        pass

    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        """When feedback_enabled=False, no HTTP calls are made."""
        with patch("src.services.feedback_client.settings") as mock_settings:
            mock_settings.feedback_enabled = False
            mock_settings.feedback_service_url = None
            
            from src.services.feedback_client import FeedbackClient
            client = FeedbackClient()
            
            with patch("httpx.AsyncClient") as mock_httpx:
                await client.track_edit(original_text="a", corrected_text="b")
                mock_httpx.assert_not_called()

    @pytest.mark.asyncio
    async def test_track_edit_sends_post(self):
        """When enabled, track_edit sends a POST request."""
        with patch("src.services.feedback_client.settings") as mock_settings:
            mock_settings.feedback_enabled = True
            mock_settings.feedback_service_url = "http://feedback:8090"
            mock_settings.feedback_service_api_key = type("S", (), {"get_secret_value": lambda self: "test-key"})()
            mock_settings.api_key_header_name = "X-API-Key"
            
            from src.services.feedback_client import FeedbackClient
            client = FeedbackClient()

            mock_response = AsyncMock()
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            
            with patch("httpx.AsyncClient", return_value=mock_client_instance):
                await client.track_edit(
                    document_id="doc-1",
                    original_text="old",
                    corrected_text="new",
                    page=3,
                )
                mock_client_instance.post.assert_called_once()
                call_args = mock_client_instance.post.call_args
                assert call_args[0][0] == "http://feedback:8090/api/v1/feedback"
                assert call_args[1]["json"]["feedback_type"] == "user_edit"

    @pytest.mark.asyncio
    async def test_report_issue_sends_post(self):
        """When enabled, report_issue sends a POST request."""
        with patch("src.services.feedback_client.settings") as mock_settings:
            mock_settings.feedback_enabled = True
            mock_settings.feedback_service_url = "http://feedback:8090"
            mock_settings.feedback_service_api_key = type("S", (), {"get_secret_value": lambda self: "test-key"})()
            mock_settings.api_key_header_name = "X-API-Key"
            
            from src.services.feedback_client import FeedbackClient
            client = FeedbackClient()

            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=AsyncMock())
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            
            with patch("httpx.AsyncClient", return_value=mock_client_instance):
                await client.report_issue(
                    document_id="doc-1",
                    category="accessibility",
                    description="Alt text missing",
                )
                call_args = mock_client_instance.post.call_args
                assert call_args[1]["json"]["feedback_type"] == "issue_report"
                assert call_args[1]["json"]["category"] == "accessibility"

    @pytest.mark.asyncio
    async def test_error_swallowed(self):
        """HTTP errors should be logged but not raised."""
        with patch("src.services.feedback_client.settings") as mock_settings:
            mock_settings.feedback_enabled = True
            mock_settings.feedback_service_url = "http://feedback:8090"
            mock_settings.feedback_service_api_key = type("S", (), {"get_secret_value": lambda self: "test-key"})()
            mock_settings.api_key_header_name = "X-API-Key"
            
            from src.services.feedback_client import FeedbackClient
            client = FeedbackClient()
            
            with patch("httpx.AsyncClient", side_effect=Exception("connection refused")):
                # Should not raise
                await client.track_edit(original_text="a", corrected_text="b")

    @pytest.mark.asyncio
    async def test_batch_sends_items_array(self):
        """Batch edits should send as items array."""
        with patch("src.services.feedback_client.settings") as mock_settings:
            mock_settings.feedback_enabled = True
            mock_settings.feedback_service_url = "http://feedback:8090"
            mock_settings.feedback_service_api_key = type("S", (), {"get_secret_value": lambda self: "test-key"})()
            mock_settings.api_key_header_name = "X-API-Key"
            
            from src.services.feedback_client import FeedbackClient
            client = FeedbackClient()

            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=AsyncMock())
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            
            with patch("httpx.AsyncClient", return_value=mock_client_instance):
                edits = [
                    {"feedback_type": "user_edit", "original_text": "a", "corrected_text": "b"},
                    {"feedback_type": "user_edit", "original_text": "c", "corrected_text": "d"},
                ]
                await client.track_edits_batch(edits)
                call_args = mock_client_instance.post.call_args
                assert "items" in call_args[1]["json"]
                assert len(call_args[1]["json"]["items"]) == 2
