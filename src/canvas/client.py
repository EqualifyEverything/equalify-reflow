"""Async Canvas LMS REST API client with rate limiting.

Provides methods for Pages, Files, and Modules APIs. Handles Docker
host rewriting for local development and Canvas rate limit headers.
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class CanvasAPIError(Exception):
    """Raised when a Canvas API call fails."""

    pass


class CanvasAPIClient:
    """Async client for Canvas LMS REST API with rate limiting.

    Handles authentication, rate limiting, Docker host rewriting,
    and the 3-step Canvas file upload workflow.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        rate_limit_buffer: int = 50,
    ):
        """Initialize the Canvas API client.

        Args:
            base_url: Canvas instance base URL (e.g., "https://canvas.uic.edu")
            api_token: Bearer token for Canvas API authentication
            rate_limit_buffer: Pause API calls when remaining < this value
        """
        # Strip trailing slash and /api/v1 suffix for consistent URL construction
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/api/v1"):
            self.base_url = self.base_url[: -len("/api/v1")]
        self.api_token = api_token
        self.rate_limit_buffer = rate_limit_buffer
        self._client: httpx.AsyncClient | None = None

    @property
    def _is_docker_url(self) -> bool:
        """Check if base_url uses host.docker.internal."""
        return "host.docker.internal" in self.base_url

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # --- Internal ---

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """Make an authenticated request to the Canvas API.

        Adds Bearer token auth, Docker host header when needed,
        and handles rate limiting after each response.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., "/api/v1/courses/123/pages")
            **kwargs: Additional keyword arguments passed to httpx request

        Returns:
            httpx.Response

        Raises:
            CanvasAPIError: On HTTP errors or timeouts
        """
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Authorization": f"Bearer {self.api_token}"}

        if self._is_docker_url:
            headers["Host"] = "localhost:3000"

        # Merge any caller-provided headers
        if "headers" in kwargs:
            caller_headers = kwargs.pop("headers")
            if isinstance(caller_headers, dict):
                headers.update(caller_headers)

        logger.debug(f"Canvas API request: {method} {path}")

        try:
            client = self._get_client()
            response = await client.request(
                method,
                url,
                headers=headers,
                **kwargs,  # type: ignore[arg-type]
            )
            await self._handle_rate_limit(response)
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500] if e.response.text else "No response body"
            logger.error(f"Canvas API error: {method} {path} returned HTTP {e.response.status_code}: {error_text}")
            raise CanvasAPIError(f"Canvas API {method} {path} failed: HTTP {e.response.status_code}") from e

        except httpx.TimeoutException as e:
            logger.error(f"Canvas API timeout: {method} {path}")
            raise CanvasAPIError(f"Canvas API {method} {path} timed out") from e

    async def _handle_rate_limit(self, response: httpx.Response) -> None:
        """Check Canvas rate limit headers and pause if needed.

        Canvas returns X-Rate-Limit-Remaining in response headers.
        When remaining drops below the buffer threshold, we sleep
        to avoid being blocked.

        Args:
            response: httpx response to check headers on
        """
        remaining_str = response.headers.get("X-Rate-Limit-Remaining")
        if remaining_str is None:
            return

        try:
            remaining = float(remaining_str)
        except (ValueError, TypeError):
            return

        logger.debug(f"Canvas rate limit remaining: {remaining}")

        if remaining <= 0:
            logger.warning("Canvas rate limit exhausted, sleeping 10 seconds")
            await asyncio.sleep(10)
        elif remaining < self.rate_limit_buffer:
            sleep_time = max(1.0, (self.rate_limit_buffer - remaining) / self.rate_limit_buffer * 5)
            logger.warning(
                f"Canvas rate limit approaching: {remaining} remaining "
                f"(buffer={self.rate_limit_buffer}), sleeping {sleep_time:.1f}s"
            )
            await asyncio.sleep(sleep_time)

    # --- Pages API ---

    async def create_page(
        self,
        course_id: str,
        title: str,
        body: str,
        published: bool = False,
        editing_roles: str = "teachers",
    ) -> dict:
        """Create a new page in a Canvas course.

        Args:
            course_id: Canvas course ID
            title: Page title
            body: Page HTML body content
            published: Whether the page should be published immediately
            editing_roles: Who can edit the page (e.g., "teachers")

        Returns:
            Canvas page object dict

        Raises:
            CanvasAPIError: If the API call fails
        """
        logger.info(f"Creating page '{title}' in course {course_id}")

        response = await self._request(
            "POST",
            f"/api/v1/courses/{course_id}/pages",
            data={
                "wiki_page[title]": title,
                "wiki_page[body]": body,
                "wiki_page[published]": str(published).lower(),
                "wiki_page[editing_roles]": editing_roles,
            },
        )
        result = response.json()
        logger.info(f"Created page '{title}' with url={result.get('url')}")
        return result

    async def update_page(
        self,
        course_id: str,
        page_url: str,
        body: str,
        title: str | None = None,
        published: bool | None = None,
    ) -> dict:
        """Update an existing Canvas page.

        Args:
            course_id: Canvas course ID
            page_url: Page URL slug (e.g., "my-page-title")
            body: Updated HTML body content
            title: Optional new title
            published: Optional publish state

        Returns:
            Updated Canvas page object dict

        Raises:
            CanvasAPIError: If the API call fails
        """
        logger.info(f"Updating page '{page_url}' in course {course_id}")

        data: dict[str, str] = {"wiki_page[body]": body}
        if title is not None:
            data["wiki_page[title]"] = title
        if published is not None:
            data["wiki_page[published]"] = str(published).lower()

        response = await self._request(
            "PUT",
            f"/api/v1/courses/{course_id}/pages/{page_url}",
            data=data,
        )
        return response.json()

    async def get_page(self, course_id: str, page_url: str) -> dict | None:
        """Get a Canvas page by URL slug.

        Args:
            course_id: Canvas course ID
            page_url: Page URL slug

        Returns:
            Canvas page object dict, or None if not found

        Raises:
            CanvasAPIError: If the API call fails with a non-404 status
        """
        logger.debug(f"Getting page '{page_url}' from course {course_id}")

        try:
            response = await self._request(
                "GET",
                f"/api/v1/courses/{course_id}/pages/{page_url}",
            )
            return response.json()
        except CanvasAPIError as e:
            if "HTTP 404" in str(e):
                logger.debug(f"Page '{page_url}' not found in course {course_id}")
                return None
            raise

    # --- Files API ---

    async def upload_file(
        self,
        course_id: str,
        file_content: bytes,
        filename: str,
        content_type: str,
        parent_folder_path: str = "equalify-reflow",
    ) -> dict:
        """Upload a file to a Canvas course using the 3-step upload process.

        Step 1: POST to /api/v1/courses/{id}/files to get upload URL
        Step 2: POST file to the upload URL (multipart form)
        Step 3: Confirm upload (follow redirect or POST confirm)

        Args:
            course_id: Canvas course ID
            file_content: File bytes to upload
            filename: Name for the uploaded file
            content_type: MIME type of the file
            parent_folder_path: Folder path in Canvas Files

        Returns:
            Canvas file object dict with 'url' field

        Raises:
            CanvasAPIError: If any upload step fails
        """
        logger.info(
            f"Uploading file '{filename}' ({len(file_content)} bytes) to course {course_id}/{parent_folder_path}"
        )

        # Step 1: Request upload URL
        step1_response = await self._request(
            "POST",
            f"/api/v1/courses/{course_id}/files",
            data={
                "name": filename,
                "size": str(len(file_content)),
                "content_type": content_type,
                "parent_folder_path": parent_folder_path,
            },
        )
        upload_info = step1_response.json()
        upload_url = upload_info.get("upload_url")
        upload_params = upload_info.get("upload_params", {})

        if not upload_url:
            raise CanvasAPIError("Canvas file upload step 1 failed: no upload_url in response")

        logger.debug(f"Step 1 complete: upload_url={upload_url[:100]}...")

        # Step 2: Upload file to the upload URL
        try:
            client = self._get_client()

            # Build multipart form with upload_params + file
            files_dict: dict[str, object] = {
                "file": (filename, file_content, content_type),
            }
            form_data: dict[str, str] = dict(upload_params)

            # Canvas upload URLs may be external (e.g., S3), so no auth header
            step2_headers: dict[str, str] = {}
            if self._is_docker_url and "host.docker.internal" in upload_url:
                step2_headers["Host"] = "localhost:3000"

            step2_response = await client.post(
                upload_url,
                data=form_data,
                files=files_dict,
                headers=step2_headers,
                follow_redirects=False,
            )

            logger.debug(f"Step 2 complete: status={step2_response.status_code}")

        except httpx.TimeoutException as e:
            logger.error(f"Canvas file upload step 2 timed out: {filename}")
            raise CanvasAPIError(f"File upload timed out for {filename}") from e

        # Step 3: Confirm upload
        # Canvas may return a 3xx redirect or a 2xx with location header
        if step2_response.is_redirect:
            location = step2_response.headers.get("location", "")
            if self._is_docker_url:
                location = location.replace("http://localhost:3000", self.base_url)

            confirm_headers: dict[str, str] = {"Authorization": f"Bearer {self.api_token}"}
            if self._is_docker_url:
                confirm_headers["Host"] = "localhost:3000"

            confirm_response = await client.get(
                location,
                headers=confirm_headers,
                follow_redirects=True,
            )
            confirm_response.raise_for_status()
            result = confirm_response.json()
        elif step2_response.status_code in (200, 201):
            result = step2_response.json()
            # Some Canvas versions need explicit confirmation
            file_id = result.get("id")
            if file_id and "url" not in result:
                confirm_response = await self._request(
                    "POST",
                    f"/api/v1/files/{file_id}/confirm",
                )
                result = confirm_response.json()
        else:
            step2_response.raise_for_status()
            result = step2_response.json()

        logger.info(f"Uploaded file '{filename}' to Canvas: id={result.get('id')}, url={result.get('url', 'N/A')}")
        return result

    async def list_course_files(
        self,
        course_id: str,
        content_types: list[str] | None = None,
        sort: str = "created_at",
        order: str = "desc",
        per_page: int = 50,
    ) -> list[dict]:
        """List files in a Canvas course.

        Args:
            course_id: Canvas course ID
            content_types: Optional filter by MIME types
            sort: Sort field (e.g., "created_at", "name")
            order: Sort order ("asc" or "desc")
            per_page: Number of results per page

        Returns:
            List of Canvas file object dicts

        Raises:
            CanvasAPIError: If the API call fails
        """
        logger.debug(f"Listing files in course {course_id}")

        params: dict[str, str | int] = {
            "sort": sort,
            "order": order,
            "per_page": per_page,
        }
        if content_types:
            params["content_types[]"] = ",".join(content_types)

        response = await self._request(
            "GET",
            f"/api/v1/courses/{course_id}/files",
            params=params,
        )
        return response.json()

    # --- Modules API ---

    async def list_modules(
        self,
        course_id: str,
        include_items: bool = True,
    ) -> list[dict]:
        """List modules in a Canvas course with pagination.

        Args:
            course_id: Canvas course ID
            include_items: Whether to include module items in response

        Returns:
            List of Canvas module object dicts

        Raises:
            CanvasAPIError: If the API call fails
        """
        logger.debug(f"Listing modules in course {course_id}")

        params: dict[str, str | int] = {"per_page": 50}
        if include_items:
            params["include[]"] = "items"

        all_modules: list[dict] = []
        path = f"/api/v1/courses/{course_id}/modules"

        while path:
            response = await self._request("GET", path, params=params)
            all_modules.extend(response.json())

            # Handle Link header pagination
            next_url = self._parse_next_link(response)
            if next_url:
                # Strip base_url to get the path for next request
                if next_url.startswith(self.base_url):
                    path = next_url[len(self.base_url) :]
                else:
                    path = next_url
                params = {}  # params are already in the URL
            else:
                path = ""

        return all_modules

    async def create_module_item(
        self,
        course_id: str,
        module_id: str,
        title: str,
        item_type: str,
        page_url: str,
        position: int | None = None,
    ) -> dict:
        """Create a new item in a Canvas module.

        Args:
            course_id: Canvas course ID
            module_id: Canvas module ID
            title: Item title
            item_type: Item type (e.g., "Page", "File")
            page_url: Page URL slug for Page type items
            position: Optional position in module

        Returns:
            Canvas module item object dict

        Raises:
            CanvasAPIError: If the API call fails
        """
        logger.info(f"Creating module item '{title}' in course {course_id}, module {module_id}")

        data: dict[str, str] = {
            "module_item[title]": title,
            "module_item[type]": item_type,
            "module_item[page_url]": page_url,
        }
        if position is not None:
            data["module_item[position]"] = str(position)

        response = await self._request(
            "POST",
            f"/api/v1/courses/{course_id}/modules/{module_id}/items",
            data=data,
        )
        return response.json()

    @staticmethod
    def _parse_next_link(response: httpx.Response) -> str | None:
        """Parse the 'next' URL from Canvas Link header pagination.

        Args:
            response: httpx response with Link header

        Returns:
            Next page URL or None
        """
        link_header = response.headers.get("Link", "")
        if not link_header:
            return None

        for part in link_header.split(","):
            if 'rel="next"' in part:
                # Extract URL from <url> format
                url_start = part.find("<")
                url_end = part.find(">")
                if url_start != -1 and url_end != -1:
                    return part[url_start + 1 : url_end]
        return None
