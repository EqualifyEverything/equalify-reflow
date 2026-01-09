"""Application service for applying auto corrections to markdown.

This service applies search-replace edits from AutoCorrection objects to the
markdown document, using layered matching strategies to handle minor
discrepancies.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.services.job_service import JobService
from src.services.remediation_storage_service import RemediationStorageService
from src.services.storage_service import StorageService
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation

logger = logging.getLogger(__name__)


class ApplicationResult(BaseModel):
    """Result of applying auto corrections to markdown.

    Contains metrics and outputs from applying a set of AutoCorrections
    to a markdown document using the layered matching strategy.

    Attributes:
        applied_count: Number of corrections successfully applied
        failed_count: Number of corrections that failed to apply
        skipped_count: Number of corrections skipped (already applied)
        failed_corrections: Details of failed corrections
        final_markdown_url: S3 key of final markdown (None if no changes)
        validation_warnings: Non-blocking warnings from markdown validation

    Example:
        >>> result = ApplicationResult(
        ...     applied_count=5,
        ...     failed_count=1,
        ...     skipped_count=0,
        ...     failed_corrections=[
        ...         {"correction_id": "corr-123", "error": "Search text not found"}
        ...     ],
        ...     final_markdown_url="results/job-123.md",
        ...     validation_warnings=[]
        ... )
    """

    applied_count: int = Field(
        ...,
        ge=0,
        description="Number of corrections successfully applied"
    )
    failed_count: int = Field(
        ...,
        ge=0,
        description="Number of corrections that failed to apply"
    )
    skipped_count: int = Field(
        ...,
        ge=0,
        description="Number of corrections skipped (already applied)"
    )
    failed_corrections: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Details of failed corrections"
    )
    final_markdown_url: str | None = Field(
        default=None,
        description="S3 key of final markdown (None if no changes)"
    )
    validation_warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking warnings from markdown validation"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "applied_count": 5,
                "failed_count": 1,
                "skipped_count": 0,
                "failed_corrections": [
                    {
                        "correction_id": "corr-550e8400-e29b-41d4-a716-446655440000",
                        "error": "Search text not found in document",
                        "search_preview": "![](missing-image.png)"
                    }
                ],
                "final_markdown_url": "results/job-123.md",
                "validation_warnings": []
            }
        }
    )


class ApplicationService:
    """Service for applying auto corrections to markdown.

    Uses layered matching strategies:
    1. Exact match - search text must appear exactly once
    2. Whitespace-normalized match - collapses whitespace for comparison

    Example:
        >>> service = ApplicationService(remediation_storage, storage, job_service)
        >>> result = await service.apply_auto_corrections("job-123")
        >>> print(f"Applied {result.applied_count} corrections")
    """

    def __init__(
        self,
        remediation_storage: RemediationStorageService,
        storage: StorageService,
        job_service: JobService,
    ) -> None:
        """Initialize application service.

        Args:
            remediation_storage: Service for loading/saving remediation artifacts
            storage: Service for S3 operations
            job_service: Service for job status management
        """
        self.remediation_storage = remediation_storage
        self.storage = storage
        self.job_service = job_service

    async def apply_auto_corrections(
        self,
        job_id: str,
    ) -> ApplicationResult:
        """Apply all unapplied auto corrections to the markdown document.

        Loads the current markdown, filters to unapplied corrections, applies
        each sequentially, and saves the result.

        Args:
            job_id: Job identifier

        Returns:
            ApplicationResult with counts and any failures

        Raises:
            ValueError: If no markdown found for job
        """
        # Load current state
        markdown = await self.remediation_storage.load_current_markdown(job_id)
        corrections = await self.remediation_storage.load_auto_corrections(job_id)
        observations = await self.remediation_storage.load_observations(job_id)

        if not markdown:
            raise ValueError(f"No markdown found for job {job_id}")

        # Filter to unapplied corrections
        unapplied = [c for c in corrections if not c.applied]

        if not unapplied:
            logger.info(f"Job {job_id}: No unapplied corrections to apply")
            return ApplicationResult(
                applied_count=0,
                failed_count=0,
                skipped_count=len(corrections),
                failed_corrections=[],
                final_markdown_url=None,
                validation_warnings=[],
            )

        logger.info(f"Job {job_id}: Applying {len(unapplied)} corrections")

        # Sort by page number for predictable ordering
        unapplied.sort(key=lambda c: c.page_num if c.page_num else 0)

        # Apply each correction
        applied_count = 0
        failed_count = 0
        skipped_count = len(corrections) - len(unapplied)
        failed_corrections: list[dict[str, Any]] = []
        application_log: list[dict[str, Any]] = []

        for correction in unapplied:
            result = self._apply_single_correction(markdown, correction)

            if result["success"]:
                markdown = result["new_markdown"]
                correction.mark_applied()
                applied_count += 1

                # Close the associated observation
                self._close_observation(observations, correction.observation_id)

                application_log.append({
                    "correction_id": correction.id,
                    "status": "applied",
                    "method": result["method"],
                    "timestamp": datetime.now(UTC).isoformat(),
                })

            else:
                failed_count += 1
                failed_corrections.append({
                    "correction_id": correction.id,
                    "error": result["error"],
                    "search_preview": correction.search[:100] if correction.search else "",
                })

                application_log.append({
                    "correction_id": correction.id,
                    "status": "failed",
                    "error": result["error"],
                    "timestamp": datetime.now(UTC).isoformat(),
                })

        # Validate final markdown
        validation_warnings = self._validate_markdown(markdown)

        # Save updated corrections and observations
        await self.remediation_storage.save_auto_corrections(job_id, corrections)
        await self.remediation_storage.save_observations(job_id, observations)

        # Save application log
        await self.remediation_storage.save_application_log(job_id, application_log)

        # Save final markdown
        final_url: str | None = None
        if applied_count > 0:
            # Save as new current version
            s3_key = await self.storage.upload_result(
                job_id=job_id,
                content=markdown,
                format="md",
            )
            final_url = s3_key

            # Also save as versioned final
            await self.storage.upload_result(
                job_id=job_id,
                content=markdown,
                format="md",
                suffix="final",
            )

        logger.info(
            f"Job {job_id}: Application complete - "
            f"{applied_count} applied, {failed_count} failed"
        )

        return ApplicationResult(
            applied_count=applied_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            failed_corrections=failed_corrections,
            final_markdown_url=final_url,
            validation_warnings=validation_warnings,
        )

    def _apply_single_correction(
        self,
        markdown: str,
        correction: AutoCorrection,
    ) -> dict[str, Any]:
        """Apply a single correction using layered matching.

        Tries in order:
        1. Exact match
        2. Whitespace-normalized match

        Args:
            markdown: Current markdown content
            correction: AutoCorrection to apply

        Returns:
            Dict with success, new_markdown, method, or error
        """
        search = correction.search
        replace = correction.replace

        # Method 1: Exact match
        if search in markdown:
            count = markdown.count(search)
            if count == 1:
                new_markdown = markdown.replace(search, replace, 1)
                return {
                    "success": True,
                    "new_markdown": new_markdown,
                    "method": "exact",
                }
            else:
                return {
                    "success": False,
                    "error": f"Search text matches {count} locations (must be unique)",
                }

        # Method 2: Whitespace-normalized match
        normalized_result = self._try_whitespace_normalized(markdown, search, replace)
        if normalized_result["success"]:
            return normalized_result

        return {
            "success": False,
            "error": "Search text not found in document",
        }

    def _try_whitespace_normalized(
        self,
        markdown: str,
        search: str,
        replace: str,
    ) -> dict[str, Any]:
        """Try matching with normalized whitespace.

        Normalizes both search and markdown to single spaces,
        then finds and replaces preserving original formatting.

        Args:
            markdown: Current markdown content
            search: Text to search for
            replace: Text to replace with

        Returns:
            Dict with success, new_markdown, method, or error/success=False
        """
        # Normalize search
        search_normalized = re.sub(r'\s+', ' ', search.strip())

        # Find in normalized markdown
        markdown_normalized = re.sub(r'\s+', ' ', markdown)

        if search_normalized not in markdown_normalized:
            return {"success": False}

        count = markdown_normalized.count(search_normalized)
        if count != 1:
            return {
                "success": False,
                "error": f"Normalized search matches {count} locations",
            }

        # Use regex to find the original text with flexible whitespace
        # First escape special regex chars, then replace escaped spaces with \s+
        escaped = re.escape(search.strip())
        # re.escape escapes spaces as '\ ', we need to replace those with \s+
        pattern = escaped.replace(r'\ ', r'\s+')
        match = re.search(pattern, markdown)

        if match:
            new_markdown = markdown[:match.start()] + replace + markdown[match.end():]
            return {
                "success": True,
                "new_markdown": new_markdown,
                "method": "whitespace_normalized",
            }

        return {"success": False}

    def _close_observation(
        self,
        observations: list[Observation],
        obs_id: str,
    ) -> None:
        """Close an observation as fixed by an auto correction.

        Args:
            observations: List of all observations
            obs_id: Observation ID to close
        """
        for obs in observations:
            if obs.id == obs_id and obs.status == "open":
                obs.close("fixed")
                break

    def _validate_markdown(self, markdown: str) -> list[str]:
        """Validate markdown syntax and structure.

        Checks for common issues that might indicate broken markup.
        Returns warnings (non-blocking issues).

        Args:
            markdown: Markdown content to validate

        Returns:
            List of warning messages
        """
        warnings: list[str] = []

        # Check for unbalanced code fences
        fence_count = markdown.count("```")
        if fence_count % 2 != 0:
            warnings.append("Unbalanced code fences detected")

        # Check for broken image syntax (unclosed brackets)
        broken_images = re.findall(r'!\[[^\]]*$', markdown, re.MULTILINE)
        if broken_images:
            warnings.append(f"Potentially broken image syntax: {len(broken_images)} instances")

        # Check for TODO placeholders still present
        todos = re.findall(r'!\[TODO:', markdown)
        if todos:
            warnings.append(f"Unresolved TODO placeholders: {len(todos)}")

        # Check heading structure (no level skips)
        headings = re.findall(r'^(#+)\s', markdown, re.MULTILINE)
        prev_level = 0
        for h in headings:
            level = len(h)
            if level > prev_level + 1 and prev_level > 0:
                warnings.append(f"Heading level skip detected: H{prev_level} -> H{level}")
                break  # Only report first skip
            prev_level = level

        return warnings

    async def count_open_observations(self, job_id: str) -> int:
        """Count observations still open (not yet resolved).

        Args:
            job_id: Job identifier

        Returns:
            Number of observations with status="open"
        """
        observations = await self.remediation_storage.load_observations(job_id)
        return sum(1 for o in observations if o.status == "open")
