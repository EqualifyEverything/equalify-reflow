"""Unit tests for Canvas API client."""

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from src.canvas.client import CanvasAPIClient, CanvasAPIError

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    """Create a CanvasAPIClient for testing."""
    return CanvasAPIClient(
        base_url="https://canvas.example.com",
        api_token="test-token-123",
        rate_limit_buffer=50,
    )


@pytest.fixture
def docker_client():
    """Create a CanvasAPIClient with Docker host rewriting."""
    return CanvasAPIClient(
        base_url="http://host.docker.internal:3000",
        api_token="test-token-123",
        rate_limit_buffer=50,
    )


def _make_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    headers: dict | None = None,
    text: str = "",
) -> httpx.Response:
    """Build a mock httpx.Response with proper content."""
    resp_headers = dict(headers or {})
    if json_data is not None:
        content = json.dumps(json_data).encode()
        resp_headers.setdefault("content-type", "application/json")
    else:
        content = text.encode() if text else b""

    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=resp_headers,
        request=httpx.Request("GET", "https://canvas.example.com/test"),
    )


class TestInit:
    """Tests for client initialization."""

    def test_strips_trailing_slash(self):
        c = CanvasAPIClient(base_url="https://canvas.example.com/", api_token="t")
        assert c.base_url == "https://canvas.example.com"

    def test_strips_api_v1_suffix(self):
        c = CanvasAPIClient(base_url="https://canvas.example.com/api/v1", api_token="t")
        assert c.base_url == "https://canvas.example.com"

    def test_stores_token_and_buffer(self):
        c = CanvasAPIClient(
            base_url="https://canvas.example.com",
            api_token="my-token",
            rate_limit_buffer=100,
        )
        assert c.api_token == "my-token"
        assert c.rate_limit_buffer == 100

    def test_docker_url_detection(self, docker_client):
        assert docker_client._is_docker_url is True

    def test_non_docker_url_detection(self, client):
        assert client._is_docker_url is False

    def test_docker_host_header_with_port(self, docker_client):
        assert docker_client._docker_host_header == "localhost:3000"

    def test_docker_host_header_custom_port(self):
        c = CanvasAPIClient(
            base_url="http://host.docker.internal:8080",
            api_token="t",
        )
        assert c._docker_host_header == "localhost:8080"

    def test_docker_host_header_no_port(self):
        c = CanvasAPIClient(
            base_url="http://host.docker.internal",
            api_token="t",
        )
        assert c._docker_host_header == "localhost"


class TestRequest:
    """Tests for the internal _request method."""

    @pytest.mark.asyncio
    async def test_sets_auth_header(self, client):
        response = _make_response(json_data={"ok": True})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client._request("GET", "/api/v1/test")

            call_kwargs = mock_http.request.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_sets_host_header_for_docker(self, docker_client):
        response = _make_response(json_data={"ok": True})

        with patch.object(docker_client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await docker_client._request("GET", "/api/v1/test")

            call_kwargs = mock_http.request.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers["Host"] == "localhost:3000"

    @pytest.mark.asyncio
    async def test_sets_host_header_for_docker_custom_port(self):
        custom_port_client = CanvasAPIClient(
            base_url="http://host.docker.internal:8080",
            api_token="test-token-123",
        )
        response = _make_response(json_data={"ok": True})

        with patch.object(custom_port_client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await custom_port_client._request("GET", "/api/v1/test")

            call_kwargs = mock_http.request.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers["Host"] == "localhost:8080"

    @pytest.mark.asyncio
    async def test_no_host_header_for_non_docker(self, client):
        response = _make_response(json_data={"ok": True})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client._request("GET", "/api/v1/test")

            call_kwargs = mock_http.request.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert "Host" not in headers

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, client):
        error_response = _make_response(status_code=500, text="Server Error")

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=error_response)
            mock_get.return_value = mock_http

            with pytest.raises(CanvasAPIError, match="HTTP 500"):
                await client._request("GET", "/api/v1/test")

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self, client):
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            mock_get.return_value = mock_http

            with pytest.raises(CanvasAPIError, match="timed out"):
                await client._request("GET", "/api/v1/test")


class TestRateLimit:
    """Tests for rate limit handling."""

    @pytest.mark.asyncio
    async def test_no_sleep_when_header_missing(self, client):
        response = _make_response(headers={})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_sleep_when_above_buffer(self, client):
        response = _make_response(headers={"X-Rate-Limit-Remaining": "100"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleeps_when_below_buffer(self, client):
        response = _make_response(headers={"X-Rate-Limit-Remaining": "20"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_called_once()
            sleep_time = mock_sleep.call_args[0][0]
            assert sleep_time > 0

    @pytest.mark.asyncio
    async def test_sleeps_10s_when_exhausted(self, client):
        response = _make_response(headers={"X-Rate-Limit-Remaining": "0"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_handles_non_numeric_header(self, client):
        response = _make_response(headers={"X-Rate-Limit-Remaining": "invalid"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleeps_10s_when_negative(self, client):
        """Negative remaining is treated as exhausted."""
        response = _make_response(headers={"X-Rate-Limit-Remaining": "-5"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_no_sleep_at_exact_buffer(self, client):
        """Remaining exactly equal to buffer should not trigger sleep."""
        response = _make_response(headers={"X-Rate-Limit-Remaining": "50"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleeps_just_below_buffer(self, client):
        """Remaining just below buffer (49) triggers a short sleep."""
        response = _make_response(headers={"X-Rate-Limit-Remaining": "49"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_called_once()
            sleep_time = mock_sleep.call_args[0][0]
            assert 1.0 <= sleep_time <= 5.0

    @pytest.mark.asyncio
    async def test_handles_float_remaining(self, client):
        """Canvas may return fractional remaining values."""
        response = _make_response(headers={"X-Rate-Limit-Remaining": "25.5"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(response)
            mock_sleep.assert_called_once()
            sleep_time = mock_sleep.call_args[0][0]
            assert 1.0 <= sleep_time <= 5.0

    @pytest.mark.asyncio
    async def test_sleep_time_scales_with_remaining(self, client):
        """Lower remaining values should produce longer sleep times."""
        resp_low = _make_response(headers={"X-Rate-Limit-Remaining": "5"})
        resp_high = _make_response(headers={"X-Rate-Limit-Remaining": "40"})

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(resp_low)
            low_sleep = mock_sleep.call_args[0][0]

        with patch("src.canvas.client.asyncio.sleep") as mock_sleep:
            await client._handle_rate_limit(resp_high)
            high_sleep = mock_sleep.call_args[0][0]

        assert low_sleep > high_sleep

    @pytest.mark.asyncio
    async def test_request_calls_handle_rate_limit(self, client):
        """Verify _request invokes _handle_rate_limit on response."""
        response = _make_response(json_data={"ok": True})

        with (
            patch.object(client, "_get_client") as mock_get,
            patch.object(client, "_handle_rate_limit", new_callable=AsyncMock) as mock_rl,
        ):
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client._request("GET", "/api/v1/test")

            mock_rl.assert_called_once_with(response)


class TestPagesAPI:
    """Tests for Pages API methods."""

    @pytest.mark.asyncio
    async def test_create_page(self, client):
        page_data = {"url": "my-page", "title": "My Page"}

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=page_data)

            result = await client.create_page(
                course_id="123",
                title="My Page",
                body="<p>Content</p>",
                published=True,
                editing_roles="teachers",
            )

            assert result == page_data
            mock_req.assert_called_once_with(
                "POST",
                "/api/v1/courses/123/pages",
                data={
                    "wiki_page[title]": "My Page",
                    "wiki_page[body]": "<p>Content</p>",
                    "wiki_page[published]": "true",
                    "wiki_page[editing_roles]": "teachers",
                },
            )

    @pytest.mark.asyncio
    async def test_create_page_defaults(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"url": "test"})

            await client.create_page(course_id="1", title="Test", body="Body")

            call_data = mock_req.call_args.kwargs["data"]
            assert call_data["wiki_page[published]"] == "false"
            assert call_data["wiki_page[editing_roles]"] == "teachers"

    @pytest.mark.asyncio
    async def test_update_page_all_fields(self, client):
        updated = {"url": "my-page", "title": "Updated"}

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=updated)

            result = await client.update_page(
                course_id="123",
                page_url="my-page",
                body="<p>New content</p>",
                title="Updated",
                published=True,
                editing_roles="teachers,students",
            )

            assert result == updated
            mock_req.assert_called_once_with(
                "PUT",
                "/api/v1/courses/123/pages/my-page",
                data={
                    "wiki_page[body]": "<p>New content</p>",
                    "wiki_page[title]": "Updated",
                    "wiki_page[published]": "true",
                    "wiki_page[editing_roles]": "teachers,students",
                },
            )

    @pytest.mark.asyncio
    async def test_update_page_body_only(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"url": "pg"})

            await client.update_page(course_id="1", page_url="pg", body="New body")

            call_data = mock_req.call_args.kwargs["data"]
            assert call_data == {"wiki_page[body]": "New body"}

    @pytest.mark.asyncio
    async def test_update_page_title_only(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"url": "pg"})

            await client.update_page(course_id="1", page_url="pg", title="New Title")

            call_data = mock_req.call_args.kwargs["data"]
            assert call_data == {"wiki_page[title]": "New Title"}

    @pytest.mark.asyncio
    async def test_update_page_published_only(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"url": "pg"})

            await client.update_page(course_id="1", page_url="pg", published=False)

            call_data = mock_req.call_args.kwargs["data"]
            assert call_data == {"wiki_page[published]": "false"}

    @pytest.mark.asyncio
    async def test_update_page_no_fields_raises(self, client):
        with pytest.raises(ValueError, match="at least one field"):
            await client.update_page(course_id="1", page_url="pg")

    @pytest.mark.asyncio
    async def test_get_page_found(self, client):
        page = {"url": "my-page", "title": "Found"}

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=page)

            result = await client.get_page(course_id="123", page_url="my-page")

            assert result == page
            mock_req.assert_called_once_with(
                "GET",
                "/api/v1/courses/123/pages/my-page",
            )

    @pytest.mark.asyncio
    async def test_get_page_not_found(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = CanvasAPIError("Canvas API GET /api/v1/courses/123/pages/missing failed: HTTP 404")

            result = await client.get_page(course_id="123", page_url="missing")

            assert result is None

    @pytest.mark.asyncio
    async def test_get_page_other_error_raises(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = CanvasAPIError("Canvas API GET failed: HTTP 500")

            with pytest.raises(CanvasAPIError):
                await client.get_page(course_id="123", page_url="bad")


class TestFilesAPI:
    """Tests for Files API methods."""

    @pytest.mark.asyncio
    async def test_upload_file_3_step(self, client):
        """Test the full 3-step upload process."""
        # Step 1 response: upload URL + params
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {"key": "value", "token": "abc"},
            }
        )

        # Step 2 response: redirect to confirm
        step2_resp = _make_response(
            status_code=301,
            headers={"location": "https://canvas.example.com/api/v1/files/42/confirm"},
        )
        # Mark as redirect
        step2_resp.headers["location"] = "https://canvas.example.com/api/v1/files/42/confirm"

        # Step 3 response: confirmed file
        step3_resp = _make_response(json_data={"id": 42, "url": "https://canvas.example.com/files/42"})

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            mock_http = AsyncMock()
            # Step 2: POST to upload URL returns redirect
            # Step 3: GET confirm URL
            mock_http.post = AsyncMock(return_value=step2_resp)
            mock_http.get = AsyncMock(return_value=step3_resp)

            with patch.object(client, "_get_client", return_value=mock_http):
                result = await client.upload_file(
                    course_id="123",
                    file_content=b"fake-pdf-content",
                    filename="test.pdf",
                    content_type="application/pdf",
                )

            assert result["id"] == 42
            assert "url" in result

            # Verify step 1 called with correct data
            mock_req.assert_called_once_with(
                "POST",
                "/api/v1/courses/123/files",
                data={
                    "name": "test.pdf",
                    "size": str(len(b"fake-pdf-content")),
                    "content_type": "application/pdf",
                    "parent_folder_path": "equalify-reflow",
                },
            )

    @pytest.mark.asyncio
    async def test_upload_file_no_redirect(self, client):
        """Test upload when step 2 returns 200 with file data."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {},
            }
        )

        step2_resp = _make_response(
            status_code=200,
            json_data={"id": 99, "url": "https://canvas.example.com/files/99"},
        )

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=step2_resp)

            with patch.object(client, "_get_client", return_value=mock_http):
                result = await client.upload_file(
                    course_id="1",
                    file_content=b"content",
                    filename="file.png",
                    content_type="image/png",
                )

            assert result["id"] == 99

    @pytest.mark.asyncio
    async def test_upload_file_missing_upload_url(self, client):
        """Test error when step 1 returns no upload_url."""
        step1_resp = _make_response(json_data={"upload_params": {}})

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            with pytest.raises(CanvasAPIError, match="no upload_url"):
                await client.upload_file(
                    course_id="1",
                    file_content=b"content",
                    filename="file.pdf",
                    content_type="application/pdf",
                )

    @pytest.mark.asyncio
    async def test_upload_file_201_with_url(self, client):
        """Test upload when step 2 returns 201 with url in response."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {"token": "xyz"},
            }
        )

        step2_resp = _make_response(
            status_code=201,
            json_data={"id": 55, "url": "https://canvas.example.com/files/55"},
        )

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=step2_resp)

            with patch.object(client, "_get_client", return_value=mock_http):
                result = await client.upload_file(
                    course_id="1",
                    file_content=b"image-data",
                    filename="image.png",
                    content_type="image/png",
                )

            assert result["id"] == 55
            assert result["url"] == "https://canvas.example.com/files/55"

    @pytest.mark.asyncio
    async def test_upload_file_200_needs_confirm(self, client):
        """Test upload when step 2 returns 200 without url, requiring confirmation."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {},
            }
        )

        # Step 2: returns id but no url — needs confirmation
        step2_resp = _make_response(
            status_code=200,
            json_data={"id": 77},
        )

        # Step 3: confirmation response with url
        confirm_resp = _make_response(
            json_data={"id": 77, "url": "https://canvas.example.com/files/77"},
        )

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = [step1_resp, confirm_resp]

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=step2_resp)

            with patch.object(client, "_get_client", return_value=mock_http):
                result = await client.upload_file(
                    course_id="1",
                    file_content=b"content",
                    filename="doc.pdf",
                    content_type="application/pdf",
                )

            assert result["id"] == 77
            assert result["url"] == "https://canvas.example.com/files/77"

            # Verify confirmation POST was made
            assert mock_req.call_count == 2
            confirm_call = mock_req.call_args_list[1]
            assert confirm_call.args == ("POST", "/api/v1/files/77/confirm")

    @pytest.mark.asyncio
    async def test_upload_file_step2_timeout(self, client):
        """Test that step 2 timeout raises CanvasAPIError."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {},
            }
        )

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("upload timed out"))

            with patch.object(client, "_get_client", return_value=mock_http):
                with pytest.raises(CanvasAPIError, match="timed out"):
                    await client.upload_file(
                        course_id="1",
                        file_content=b"content",
                        filename="big.pdf",
                        content_type="application/pdf",
                    )

    @pytest.mark.asyncio
    async def test_upload_file_docker_redirect_rewrite(self, docker_client):
        """Test that redirect location is rewritten for Docker URLs."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "http://host.docker.internal:3000/upload",
                "upload_params": {},
            }
        )

        step2_resp = _make_response(
            status_code=301,
            headers={"location": "http://localhost:3000/api/v1/files/42/confirm"},
        )

        step3_resp = _make_response(
            json_data={"id": 42, "url": "http://localhost:3000/files/42"},
        )

        with patch.object(docker_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=step2_resp)
            mock_http.get = AsyncMock(return_value=step3_resp)

            with patch.object(docker_client, "_get_client", return_value=mock_http):
                result = await docker_client.upload_file(
                    course_id="1",
                    file_content=b"content",
                    filename="file.pdf",
                    content_type="application/pdf",
                )

            assert result["id"] == 42

            # Verify the redirect URL was rewritten from localhost:3000 to docker host
            get_call = mock_http.get.call_args
            called_url = get_call.args[0] if get_call.args else get_call.kwargs.get("url", "")
            assert "host.docker.internal" in called_url

            # Verify Docker headers were set
            get_headers = get_call.kwargs.get("headers", {})
            assert get_headers.get("Host") == "localhost:3000"
            assert "Authorization" in get_headers

    @pytest.mark.asyncio
    async def test_upload_file_docker_redirect_rewrite_custom_port(self):
        """Test that redirect location rewrite uses the correct port."""
        custom_docker = CanvasAPIClient(
            base_url="http://host.docker.internal:8080",
            api_token="test-token-123",
        )

        step1_resp = _make_response(
            json_data={
                "upload_url": "http://host.docker.internal:8080/upload",
                "upload_params": {},
            }
        )

        step2_resp = _make_response(
            status_code=301,
            headers={"location": "http://localhost:8080/api/v1/files/50/confirm"},
        )

        step3_resp = _make_response(
            json_data={"id": 50, "url": "http://localhost:8080/files/50"},
        )

        with patch.object(custom_docker, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=step2_resp)
            mock_http.get = AsyncMock(return_value=step3_resp)

            with patch.object(custom_docker, "_get_client", return_value=mock_http):
                result = await custom_docker.upload_file(
                    course_id="1",
                    file_content=b"content",
                    filename="file.pdf",
                    content_type="application/pdf",
                )

            assert result["id"] == 50

            # Verify the redirect URL was rewritten using the correct port
            get_call = mock_http.get.call_args
            called_url = get_call.args[0] if get_call.args else get_call.kwargs.get("url", "")
            assert "host.docker.internal:8080" in called_url

            # Verify Docker headers use correct port
            get_headers = get_call.kwargs.get("headers", {})
            assert get_headers.get("Host") == "localhost:8080"

    @pytest.mark.asyncio
    async def test_upload_file_custom_folder(self, client):
        """Test upload with custom parent_folder_path."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {},
            }
        )

        step2_resp = _make_response(
            status_code=200,
            json_data={"id": 10, "url": "https://canvas.example.com/files/10"},
        )

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=step2_resp)

            with patch.object(client, "_get_client", return_value=mock_http):
                await client.upload_file(
                    course_id="1",
                    file_content=b"content",
                    filename="file.pdf",
                    content_type="application/pdf",
                    parent_folder_path="custom/folder",
                )

            # Verify custom folder path was sent
            call_data = mock_req.call_args.kwargs["data"]
            assert call_data["parent_folder_path"] == "custom/folder"

    @pytest.mark.asyncio
    async def test_list_course_files(self, client):
        files = [{"id": 1, "display_name": "test.pdf"}]

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=files)

            result = await client.list_course_files(
                course_id="123",
                content_types=["application/pdf"],
                sort="created_at",
                order="desc",
                per_page=25,
            )

            assert result == files
            mock_req.assert_called_once_with(
                "GET",
                "/api/v1/courses/123/files",
                params={
                    "sort": "created_at",
                    "order": "desc",
                    "per_page": 25,
                    "content_types[]": "application/pdf",
                },
            )

    @pytest.mark.asyncio
    async def test_list_course_files_no_filter(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=[])

            await client.list_course_files(course_id="1")

            call_params = mock_req.call_args.kwargs["params"]
            assert "content_types[]" not in call_params

    @pytest.mark.asyncio
    async def test_list_course_files_pagination(self, client):
        """Test that list_course_files follows Link header pagination."""
        page1_files = [{"id": 1, "display_name": "a.pdf"}]
        page2_files = [{"id": 2, "display_name": "b.pdf"}]

        page1_resp = _make_response(
            json_data=page1_files,
            headers={"Link": '<https://canvas.example.com/api/v1/courses/123/files?page=2>; rel="next"'},
        )
        page2_resp = _make_response(json_data=page2_files)

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = [page1_resp, page2_resp]

            result = await client.list_course_files(course_id="123")

            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["id"] == 2
            assert mock_req.call_count == 2

    @pytest.mark.asyncio
    async def test_list_course_files_pagination_three_pages(self, client):
        """Test pagination across three pages."""
        page1_files = [{"id": 1, "display_name": "a.pdf"}]
        page2_files = [{"id": 2, "display_name": "b.pdf"}]
        page3_files = [{"id": 3, "display_name": "c.pdf"}]

        page1_resp = _make_response(
            json_data=page1_files,
            headers={"Link": '<https://canvas.example.com/api/v1/courses/1/files?page=2>; rel="next"'},
        )
        page2_resp = _make_response(
            json_data=page2_files,
            headers={"Link": '<https://canvas.example.com/api/v1/courses/1/files?page=3>; rel="next"'},
        )
        page3_resp = _make_response(json_data=page3_files)

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = [page1_resp, page2_resp, page3_resp]

            result = await client.list_course_files(course_id="1")

            assert len(result) == 3
            assert [f["id"] for f in result] == [1, 2, 3]
            assert mock_req.call_count == 3

    @pytest.mark.asyncio
    async def test_list_course_files_pagination_clears_params(self, client):
        """Test that params are cleared after first page (they're in the URL)."""
        link_url = "https://canvas.example.com/api/v1/courses/1/files?page=2&sort=created_at&order=desc&per_page=50"
        page1_resp = _make_response(
            json_data=[{"id": 1}],
            headers={"Link": f'<{link_url}>; rel="next"'},
        )
        page2_resp = _make_response(json_data=[{"id": 2}])

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = [page1_resp, page2_resp]

            await client.list_course_files(course_id="1")

            # First call has params
            first_call = mock_req.call_args_list[0]
            assert first_call.kwargs["params"]["sort"] == "created_at"

            # Second call has empty params (they're in the URL already)
            second_call = mock_req.call_args_list[1]
            assert second_call.kwargs["params"] == {}


class TestModulesAPI:
    """Tests for Modules API methods."""

    @pytest.mark.asyncio
    async def test_list_modules_single_page(self, client):
        modules = [{"id": 1, "name": "Module 1", "items": []}]

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            resp = _make_response(json_data=modules)
            mock_req.return_value = resp

            result = await client.list_modules(course_id="123")

            assert result == modules
            call_params = mock_req.call_args.kwargs["params"]
            assert call_params["include[]"] == "items"

    @pytest.mark.asyncio
    async def test_list_modules_without_items(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=[])

            await client.list_modules(course_id="123", include_items=False)

            call_params = mock_req.call_args.kwargs["params"]
            assert "include[]" not in call_params

    @pytest.mark.asyncio
    async def test_list_modules_pagination(self, client):
        page1_modules = [{"id": 1, "name": "Mod 1"}]
        page2_modules = [{"id": 2, "name": "Mod 2"}]

        page1_resp = _make_response(
            json_data=page1_modules,
            headers={"Link": '<https://canvas.example.com/api/v1/courses/1/modules?page=2>; rel="next"'},
        )
        page2_resp = _make_response(json_data=page2_modules)

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = [page1_resp, page2_resp]

            result = await client.list_modules(course_id="1")

            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["id"] == 2
            assert mock_req.call_count == 2

    @pytest.mark.asyncio
    async def test_create_module_item(self, client):
        item = {"id": 10, "title": "New Page", "type": "Page"}

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=item)

            result = await client.create_module_item(
                course_id="123",
                module_id="456",
                title="New Page",
                item_type="Page",
                page_url="new-page",
                position=3,
            )

            assert result == item
            mock_req.assert_called_once_with(
                "POST",
                "/api/v1/courses/123/modules/456/items",
                data={
                    "module_item[title]": "New Page",
                    "module_item[type]": "Page",
                    "module_item[page_url]": "new-page",
                    "module_item[position]": "3",
                },
            )

    @pytest.mark.asyncio
    async def test_create_module_item_no_position(self, client):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"id": 1})

            await client.create_module_item(
                course_id="1",
                module_id="2",
                title="Item",
                item_type="Page",
                page_url="item-page",
            )

            call_data = mock_req.call_args.kwargs["data"]
            assert "module_item[position]" not in call_data

    @pytest.mark.asyncio
    async def test_create_module_item_api_error(self, client):
        """Test that Canvas API errors propagate as CanvasAPIError."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = CanvasAPIError("Canvas API POST /api/v1/courses/1/modules/2/items failed: HTTP 422")

            with pytest.raises(CanvasAPIError, match="HTTP 422"):
                await client.create_module_item(
                    course_id="1",
                    module_id="2",
                    title="Bad Item",
                    item_type="Page",
                    page_url="bad-page",
                )

    @pytest.mark.asyncio
    async def test_create_module_item_file_type(self, client):
        """Test creating a File-type module item."""
        item = {"id": 20, "title": "Lecture Notes", "type": "File"}

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=item)

            result = await client.create_module_item(
                course_id="100",
                module_id="200",
                title="Lecture Notes",
                item_type="File",
                page_url="lecture-notes",
            )

            assert result == item
            call_data = mock_req.call_args.kwargs["data"]
            assert call_data["module_item[type]"] == "File"

    @pytest.mark.asyncio
    async def test_create_module_item_position_zero(self, client):
        """Test that position=0 is included in the request data."""
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"id": 1})

            await client.create_module_item(
                course_id="1",
                module_id="2",
                title="First Item",
                item_type="Page",
                page_url="first-item",
                position=0,
            )

            call_data = mock_req.call_args.kwargs["data"]
            assert call_data["module_item[position]"] == "0"

    @pytest.mark.asyncio
    async def test_create_module_item_returns_full_response(self, client):
        """Test that the full Canvas response dict is returned."""
        item = {
            "id": 30,
            "title": "Week 1 Reading",
            "type": "Page",
            "position": 1,
            "module_id": 456,
            "page_url": "week-1-reading",
            "html_url": "https://canvas.example.com/courses/123/modules/items/30",
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=item)

            result = await client.create_module_item(
                course_id="123",
                module_id="456",
                title="Week 1 Reading",
                item_type="Page",
                page_url="week-1-reading",
                position=1,
            )

            assert result["id"] == 30
            assert result["page_url"] == "week-1-reading"
            assert result["html_url"] == "https://canvas.example.com/courses/123/modules/items/30"
            assert result["position"] == 1

    @pytest.mark.asyncio
    async def test_create_module_item_special_characters_in_title(self, client):
        """Test creating an item with special characters in the title."""
        item = {"id": 40, "title": "Lecture: Week 1 — Introduction & Overview", "type": "Page"}

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=item)

            result = await client.create_module_item(
                course_id="1",
                module_id="2",
                title="Lecture: Week 1 — Introduction & Overview",
                item_type="Page",
                page_url="lecture-week-1-introduction-and-overview",
            )

            assert result["title"] == "Lecture: Week 1 — Introduction & Overview"
            call_data = mock_req.call_args.kwargs["data"]
            assert call_data["module_item[title]"] == "Lecture: Week 1 — Introduction & Overview"


class TestParseNextLink:
    """Tests for Link header parsing."""

    def test_parses_next_link(self):
        response = _make_response(
            headers={
                "Link": '<https://canvas.example.com/api?page=2>; rel="next", '
                '<https://canvas.example.com/api?page=1>; rel="prev"'
            }
        )
        assert CanvasAPIClient._parse_next_link(response) == "https://canvas.example.com/api?page=2"

    def test_returns_none_when_no_next(self):
        response = _make_response(headers={"Link": '<https://canvas.example.com/api?page=1>; rel="prev"'})
        assert CanvasAPIClient._parse_next_link(response) is None

    def test_returns_none_when_no_link_header(self):
        response = _make_response(headers={})
        assert CanvasAPIClient._parse_next_link(response) is None


class TestClose:
    """Tests for client cleanup."""

    @pytest.mark.asyncio
    async def test_close_when_client_exists(self, client):
        # Force client creation
        client._get_client()
        assert client._client is not None

        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self, client):
        # Should not raise
        await client.close()


class TestConfigSettings:
    """Tests for Canvas configuration in Settings."""

    def test_canvas_autopublish_defaults(self):
        from src.config import Settings

        s = Settings(
            _env_file=None,
            redis_url="redis://localhost:6379",
        )
        assert s.canvas_autopublish_enabled is False
        assert s.canvas_polling_interval_seconds == 120
        assert s.canvas_rate_limit_buffer == 50

    def test_canvas_config_fields_exist(self):
        from src.config import Settings

        field_names = set(Settings.model_fields.keys())
        assert "canvas_autopublish_enabled" in field_names
        assert "canvas_polling_interval_seconds" in field_names
        assert "canvas_rate_limit_buffer" in field_names


class TestAuthHeaderOnAllMethods:
    """Verify every public method sends Authorization: Bearer {api_token}.

    The _request() helper sets the header, so methods that route through it
    are covered. upload_file step 3 (redirect confirm) sets the header
    manually, so we check that path separately.
    """

    @pytest.mark.asyncio
    async def test_create_page_sends_auth(self, client):
        response = _make_response(json_data={"url": "p"})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client.create_page(course_id="1", title="T", body="B")

            headers = mock_http.request.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_update_page_sends_auth(self, client):
        response = _make_response(json_data={"url": "p"})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client.update_page(course_id="1", page_url="p", body="B")

            headers = mock_http.request.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_get_page_sends_auth(self, client):
        response = _make_response(json_data={"url": "p"})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client.get_page(course_id="1", page_url="p")

            headers = mock_http.request.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_list_course_files_sends_auth(self, client):
        response = _make_response(json_data=[])

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client.list_course_files(course_id="1")

            headers = mock_http.request.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_list_modules_sends_auth(self, client):
        response = _make_response(json_data=[])

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client.list_modules(course_id="1")

            headers = mock_http.request.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_create_module_item_sends_auth(self, client):
        response = _make_response(json_data={"id": 1})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            await client.create_module_item(
                course_id="1", module_id="2", title="T",
                item_type="Page", page_url="p",
            )

            headers = mock_http.request.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_upload_file_step1_sends_auth(self, client):
        """Step 1 goes through _request, which adds auth."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {},
            }
        )
        step2_resp = _make_response(
            status_code=200,
            json_data={"id": 1, "url": "https://canvas.example.com/files/1"},
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=step1_resp)
            mock_http.post = AsyncMock(return_value=step2_resp)
            mock_get.return_value = mock_http

            await client.upload_file(
                course_id="1", file_content=b"data",
                filename="f.pdf", content_type="application/pdf",
            )

            # Step 1 request goes through _request which adds auth
            step1_headers = mock_http.request.call_args.kwargs["headers"]
            assert step1_headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_upload_file_step2_omits_auth(self, client):
        """Step 2 uploads to an external URL (e.g. S3) — no auth header."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {"key": "val"},
            }
        )
        step2_resp = _make_response(
            status_code=200,
            json_data={"id": 1, "url": "https://canvas.example.com/files/1"},
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=step1_resp)
            mock_http.post = AsyncMock(return_value=step2_resp)
            mock_get.return_value = mock_http

            await client.upload_file(
                course_id="1", file_content=b"data",
                filename="f.pdf", content_type="application/pdf",
            )

            # Step 2 posts to external S3 URL — no Authorization header
            step2_headers = mock_http.post.call_args.kwargs["headers"]
            assert "Authorization" not in step2_headers

    @pytest.mark.asyncio
    async def test_upload_file_step3_redirect_sends_auth(self, client):
        """Step 3 confirm (redirect) must include auth header."""
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {},
            }
        )
        step2_resp = _make_response(
            status_code=301,
            headers={"location": "https://canvas.example.com/api/v1/files/42/confirm"},
        )
        step3_resp = _make_response(
            json_data={"id": 42, "url": "https://canvas.example.com/files/42"},
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=step1_resp)
            mock_http.post = AsyncMock(return_value=step2_resp)
            mock_http.get = AsyncMock(return_value=step3_resp)
            mock_get.return_value = mock_http

            await client.upload_file(
                course_id="1", file_content=b"data",
                filename="f.pdf", content_type="application/pdf",
            )

            # Step 3 GET confirm must include Authorization
            step3_headers = mock_http.get.call_args.kwargs["headers"]
            assert step3_headers["Authorization"] == "Bearer test-token-123"


class TestLogging:
    """Verify all methods log at INFO (operations) and DEBUG (request/response details)."""

    @pytest.mark.asyncio
    async def test_create_page_logs_info(self, client, caplog):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"url": "my-page"})

            with caplog.at_level(logging.INFO, logger="src.canvas.client"):
                await client.create_page(course_id="1", title="My Page", body="B")

            info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
            assert any("Creating page" in m for m in info_messages)
            assert any("Created page" in m for m in info_messages)

    @pytest.mark.asyncio
    async def test_update_page_logs_info(self, client, caplog):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"url": "pg"})

            with caplog.at_level(logging.INFO, logger="src.canvas.client"):
                await client.update_page(course_id="1", page_url="pg", body="B")

            info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
            assert any("Updating page" in m for m in info_messages)

    @pytest.mark.asyncio
    async def test_get_page_logs_debug(self, client, caplog):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"url": "pg"})

            with caplog.at_level(logging.DEBUG, logger="src.canvas.client"):
                await client.get_page(course_id="1", page_url="pg")

            debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
            assert any("Getting page" in m for m in debug_messages)

    @pytest.mark.asyncio
    async def test_upload_file_logs_info(self, client, caplog):
        step1_resp = _make_response(
            json_data={
                "upload_url": "https://s3.example.com/upload",
                "upload_params": {},
            }
        )
        step2_resp = _make_response(
            status_code=200,
            json_data={"id": 1, "url": "https://canvas.example.com/files/1"},
        )

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = step1_resp
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=step2_resp)

            with (
                patch.object(client, "_get_client", return_value=mock_http),
                caplog.at_level(logging.INFO, logger="src.canvas.client"),
            ):
                await client.upload_file(
                    course_id="1", file_content=b"data",
                    filename="test.pdf", content_type="application/pdf",
                )

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("Uploading file" in m for m in info_messages)
        assert any("Uploaded file" in m for m in info_messages)

    @pytest.mark.asyncio
    async def test_list_course_files_logs_debug(self, client, caplog):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=[])

            with caplog.at_level(logging.DEBUG, logger="src.canvas.client"):
                await client.list_course_files(course_id="1")

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("Listing files" in m for m in debug_messages)

    @pytest.mark.asyncio
    async def test_list_modules_logs_debug(self, client, caplog):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data=[])

            with caplog.at_level(logging.DEBUG, logger="src.canvas.client"):
                await client.list_modules(course_id="1")

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("Listing modules" in m for m in debug_messages)

    @pytest.mark.asyncio
    async def test_create_module_item_logs_info(self, client, caplog):
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _make_response(json_data={"id": 1})

            with caplog.at_level(logging.INFO, logger="src.canvas.client"):
                await client.create_module_item(
                    course_id="1", module_id="2", title="Item",
                    item_type="Page", page_url="item",
                )

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("Creating module item" in m for m in info_messages)

    @pytest.mark.asyncio
    async def test_request_logs_debug(self, client, caplog):
        response = _make_response(json_data={"ok": True})

        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=response)
            mock_get.return_value = mock_http

            with caplog.at_level(logging.DEBUG, logger="src.canvas.client"):
                await client._request("GET", "/api/v1/test")

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("Canvas API request" in m for m in debug_messages)

    @pytest.mark.asyncio
    async def test_rate_limit_logs_debug(self, client, caplog):
        response = _make_response(headers={"X-Rate-Limit-Remaining": "100"})

        with caplog.at_level(logging.DEBUG, logger="src.canvas.client"):
            await client._handle_rate_limit(response)

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("rate limit remaining" in m for m in debug_messages)

    @pytest.mark.asyncio
    async def test_rate_limit_logs_warning_when_low(self, client, caplog):
        response = _make_response(headers={"X-Rate-Limit-Remaining": "20"})

        with (
            patch("src.canvas.client.asyncio.sleep"),
            caplog.at_level(logging.WARNING, logger="src.canvas.client"),
        ):
            await client._handle_rate_limit(response)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("rate limit approaching" in m for m in warning_messages)

    @pytest.mark.asyncio
    async def test_rate_limit_logs_warning_when_exhausted(self, client, caplog):
        response = _make_response(headers={"X-Rate-Limit-Remaining": "0"})

        with (
            patch("src.canvas.client.asyncio.sleep"),
            caplog.at_level(logging.WARNING, logger="src.canvas.client"),
        ):
            await client._handle_rate_limit(response)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("rate limit exhausted" in m for m in warning_messages)
