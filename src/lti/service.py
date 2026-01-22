"""LTI service for processing file menu launches.

This module handles the business logic for LTI launches:
- Extracting file context from launch claims
- Downloading files from Canvas
- Creating conversion jobs
- Integrating with existing storage/job services
"""

import logging
import uuid
from io import BytesIO
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException

from ..config import settings
from .models import FileMenuContent, LTILaunchData, LTILaunchResponse, LTIUser

if TYPE_CHECKING:
    from ..services.job_service import JobService
    from ..services.storage_service import StorageService

logger = logging.getLogger(__name__)


class LTIServiceError(Exception):
    """Raised when LTI service operations fail."""

    pass


class LTIService:
    """Service for handling LTI file menu launches.

    Orchestrates the flow from LTI launch → file download → job creation.
    Processing is triggered separately via BackgroundTasks in the router.
    """

    def __init__(
        self,
        storage_service: "StorageService",
        job_service: "JobService",
    ):
        """Initialize LTI service with dependencies.

        Args:
            storage_service: Service for S3 storage operations
            job_service: Service for Redis job management
        """
        self.storage = storage_service
        self.jobs = job_service

    async def process_file_menu_launch(
        self,
        launch_data: LTILaunchData,
    ) -> tuple[LTILaunchResponse, str]:
        """Process a file menu launch and create a conversion job.

        This is the main entry point for LTI launches from the Canvas file menu.
        It extracts file information, downloads the file from Canvas, uploads
        to S3, and creates a conversion job.

        Note: Actual document processing is triggered separately via BackgroundTasks.

        Args:
            launch_data: Parsed LTI launch data

        Returns:
            Tuple of (LTILaunchResponse, s3_key) - s3_key needed for processing

        Raises:
            LTIServiceError: If launch processing fails
            HTTPException: If file download or storage fails
        """
        # Validate we have file menu data
        if not launch_data.file_menu:
            raise LTIServiceError("Launch does not contain file menu data")

        file_info = launch_data.file_menu

        if not file_info.file_download_url:
            raise LTIServiceError("No file download URL in launch data")

        logger.info(
            f"Processing LTI file menu launch: file_id={file_info.file_id}, "
            f"filename={file_info.file_name}, user={launch_data.user.sub}"
        )

        # Download file from Canvas
        file_content = await self._fetch_canvas_file(
            download_url=file_info.file_download_url,
            file_name=file_info.file_name,
        )

        # Create job and store file
        job_id, s3_key = await self._store_and_create_job(
            file_content=file_content,
            original_filename=file_info.file_name or "canvas_file.pdf",
            lti_context={
                "canvas_file_id": file_info.file_id,
                "canvas_course_id": file_info.course_id or launch_data.canvas_course_id,
                "canvas_user_id": launch_data.canvas_user_id or launch_data.user.sub,
                "lti_deployment_id": launch_data.deployment_id,
            },
        )

        # Build viewer URL
        viewer_url = f"/viewer/{job_id}"

        logger.info(f"LTI launch processed: job_id={job_id}, viewer_url={viewer_url}")

        response = LTILaunchResponse(
            job_id=job_id,
            viewer_url=viewer_url,
            file_name=file_info.file_name,
            message="File received from Canvas, processing started",
        )

        return response, s3_key

    async def _fetch_canvas_file(
        self,
        download_url: str,
        file_name: str | None = None,
    ) -> bytes:
        """Download a file from Canvas using the provided download URL.

        Canvas provides a direct download URL in the LTI custom claims.
        This URL may require following redirects but doesn't need
        separate authentication since it's scoped to the LTI launch.

        Args:
            download_url: Canvas file download URL
            file_name: Optional filename for logging

        Returns:
            File content as bytes

        Raises:
            LTIServiceError: If download fails
        """
        display_name = file_name or "file"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            ) as client:
                logger.debug(f"Downloading file from Canvas: {download_url[:100]}...")

                response = await client.get(download_url)
                response.raise_for_status()

                # Validate content type
                content_type = response.headers.get("content-type", "")
                if "application/pdf" not in content_type.lower():
                    logger.warning(
                        f"Unexpected content type from Canvas: {content_type} "
                        f"for file {display_name}"
                    )

                content = response.content

                # Validate minimum file size
                if len(content) < 100:
                    raise LTIServiceError(
                        f"File {display_name} is too small ({len(content)} bytes)"
                    )

                # Validate maximum file size
                if len(content) > settings.max_upload_size:
                    raise LTIServiceError(
                        f"File {display_name} exceeds maximum size "
                        f"({len(content)} > {settings.max_upload_size} bytes)"
                    )

                logger.info(
                    f"Downloaded {display_name} from Canvas: {len(content)} bytes"
                )

                return content

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Canvas file download failed with HTTP {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )
            raise LTIServiceError(
                f"Failed to download file from Canvas: HTTP {e.response.status_code}"
            ) from e

        except httpx.TimeoutException as e:
            logger.error(f"Canvas file download timed out for {display_name}")
            raise LTIServiceError(
                f"Timeout downloading file from Canvas: {display_name}"
            ) from e

        except Exception as e:
            logger.error(f"Unexpected error downloading from Canvas: {e}")
            raise LTIServiceError(f"Failed to download file: {str(e)}") from e

    async def _store_and_create_job(
        self,
        file_content: bytes,
        original_filename: str,
        lti_context: dict[str, Any],
    ) -> tuple[str, str]:
        """Store file in S3 and create a conversion job.

        LTI launches skip PII scanning since the user is already
        authenticated via Canvas and the file is from a trusted source.

        Args:
            file_content: PDF file content
            original_filename: Original filename from Canvas
            lti_context: LTI context metadata to store with job

        Returns:
            Tuple of (job_id, s3_key)

        Raises:
            HTTPException: If storage or job creation fails
        """
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"

        try:
            # Upload to S3 temp bucket
            # We use put_object directly instead of upload_fileobj since we have bytes
            from botocore.exceptions import ClientError

            try:
                self.storage.s3_client.put_object(
                    Bucket=self.storage.temp_bucket,
                    Key=s3_key,
                    Body=BytesIO(file_content),
                    ContentType="application/pdf",
                )
            except ClientError as e:
                logger.error(f"S3 upload failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to store file: {str(e)}"
                )

            # Create job in Redis, skipping PII scan
            # LTI launches are from authenticated Canvas users, so we skip PII scanning
            await self.jobs.create_job(
                job_id=job_id,
                s3_key=s3_key,
                status="processing",  # Skip pii_scanning, go directly to processing
                original_filename=original_filename,
                pii_skipped=True,
                pii_skip_reason="LTI launch from authenticated Canvas user",
            )

            # Store LTI context as job metadata
            await self.jobs.redis.hset(
                f"{self.jobs.status_prefix}{job_id}",
                mapping={
                    "lti_canvas_file_id": lti_context.get("canvas_file_id", ""),
                    "lti_canvas_course_id": lti_context.get("canvas_course_id", ""),
                    "lti_canvas_user_id": lti_context.get("canvas_user_id", ""),
                    "lti_deployment_id": lti_context.get("lti_deployment_id", ""),
                    "source": "lti",
                },
            )

            # Note: Processing is triggered via BackgroundTasks in the router

            logger.info(
                f"Created LTI job: job_id={job_id}, "
                f"file={original_filename}, s3_key={s3_key}"
            )

            return job_id, s3_key

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to create LTI job: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create conversion job: {str(e)}"
            )


def parse_launch_claims(claims: dict[str, Any]) -> LTILaunchData:
    """Parse raw LTI JWT claims into structured LTILaunchData.

    Extracts user info, context, and custom claims (file menu data)
    from the raw JWT claims provided by Canvas.

    Args:
        claims: Raw JWT claims dictionary

    Returns:
        Parsed LTILaunchData object
    """
    # Parse user info
    user = LTIUser(
        sub=claims.get("sub", ""),
        name=claims.get("name"),
        given_name=claims.get("given_name"),
        family_name=claims.get("family_name"),
        email=claims.get("email"),
    )

    # Parse context (course) info
    context_data = claims.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
    context = None
    if context_data:
        from .models import LTIContext
        context = LTIContext(
            id=context_data.get("id", ""),
            label=context_data.get("label"),
            title=context_data.get("title"),
            type=context_data.get("type"),
        )

    # Parse resource link
    resource_link_data = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/resource_link", {}
    )
    resource_link = None
    if resource_link_data:
        from .models import LTIResourceLink
        resource_link = LTIResourceLink(
            id=resource_link_data.get("id", ""),
            title=resource_link_data.get("title"),
            description=resource_link_data.get("description"),
        )

    # Parse custom claims (file menu data)
    custom_data = claims.get("https://purl.imsglobal.org/spec/lti/claim/custom", {})
    file_menu = None
    if custom_data:
        file_menu = FileMenuContent(
            file_id=custom_data.get("file_id"),
            file_name=custom_data.get("file_name"),
            file_content_type=custom_data.get("file_content_type"),
            file_download_url=custom_data.get("file_download_url"),
            course_id=custom_data.get("course_id"),
        )

    # Extract Canvas-specific claims
    canvas_user_id = claims.get(
        "https://www.instructure.com/canvas_user_id"
    ) or claims.get("custom_canvas_user_id")

    canvas_course_id = claims.get(
        "https://www.instructure.com/canvas_course_id"
    ) or claims.get("custom_canvas_course_id")

    return LTILaunchData(
        deployment_id=claims.get(
            "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
        ),
        target_link_uri=claims.get(
            "https://purl.imsglobal.org/spec/lti/claim/target_link_uri", ""
        ),
        message_type=claims.get(
            "https://purl.imsglobal.org/spec/lti/claim/message_type", ""
        ),
        user=user,
        context=context,
        resource_link=resource_link,
        file_menu=file_menu,
        canvas_user_id=canvas_user_id,
        canvas_course_id=canvas_course_id,
        raw_claims=claims if settings.environment == "dev" else None,
    )
