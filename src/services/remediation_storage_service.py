"""Storage service for remediation pipeline artifacts.

Handles S3 operations for storing and retrieving:
- DocumentManifest (analysis output)
- Observations (discrepancies found)
- Proposals (suggested edits)
"""

import json
import logging
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from src.services.storage_service import StorageService
from src.shared.models.observation import Observation
from src.shared.models.proposal import Proposal
from src.shared.models.remediation import DocumentManifest

logger = logging.getLogger(__name__)


class RemediationStorageService:
    """S3 storage operations for remediation artifacts.

    Provides methods for saving and loading remediation pipeline data:
    - manifest.json: DocumentManifest from analysis phase
    - observations.json: All observations from agents and humans
    - proposals.json: All proposals with status tracking

    All files are stored in the results bucket under the job_id prefix:
        results-bucket/{job_id}/manifest.json
        results-bucket/{job_id}/observations.json
        results-bucket/{job_id}/proposals.json

    Example:
        >>> storage = StorageService(s3_client, temp_bucket, results_bucket)
        >>> remediation = RemediationStorageService(storage)
        >>> await remediation.save_manifest(job_id, manifest)
        >>> manifest = await remediation.load_manifest(job_id)
    """

    def __init__(self, storage_service: StorageService):
        """Initialize remediation storage service.

        Args:
            storage_service: StorageService instance for S3 operations
        """
        self.storage = storage_service

    async def save_manifest(self, job_id: str, manifest: DocumentManifest) -> str:
        """Save document manifest to S3.

        Args:
            job_id: Job identifier
            manifest: DocumentManifest from analysis phase

        Returns:
            S3 key where manifest was saved

        Raises:
            Exception: If upload fails
        """
        content = manifest.model_dump_json(indent=2)
        key = f"{job_id}/manifest.json"

        try:
            body = content.encode("utf-8")
            await self._put_json_object(key, body)
            logger.info(f"Saved manifest for job {job_id}")
            return key
        except Exception as e:
            logger.error(f"Failed to save manifest for job {job_id}: {e}")
            raise

    async def load_manifest(self, job_id: str) -> DocumentManifest | None:
        """Load document manifest from S3.

        Args:
            job_id: Job identifier

        Returns:
            DocumentManifest if found, None otherwise
        """
        key = f"{job_id}/manifest.json"

        try:
            content = await self._get_json_object(key)
            if content is None:
                return None
            data = json.loads(content)
            return DocumentManifest(**data)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in manifest for job {job_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load manifest for job {job_id}: {e}")
            return None

    async def save_observations(
        self,
        job_id: str,
        observations: list[Observation]
    ) -> str:
        """Save observations to S3.

        Args:
            job_id: Job identifier
            observations: List of Observation objects

        Returns:
            S3 key where observations were saved

        Raises:
            Exception: If upload fails
        """
        content = json.dumps(
            [obs.model_dump() for obs in observations],
            indent=2,
            default=self._json_serializer
        )
        key = f"{job_id}/observations.json"

        try:
            body = content.encode("utf-8")
            await self._put_json_object(key, body)
            logger.info(f"Saved {len(observations)} observations for job {job_id}")
            return key
        except Exception as e:
            logger.error(f"Failed to save observations for job {job_id}: {e}")
            raise

    async def load_observations(self, job_id: str) -> list[Observation]:
        """Load observations from S3.

        Args:
            job_id: Job identifier

        Returns:
            List of Observation objects (empty list if not found)
        """
        key = f"{job_id}/observations.json"

        try:
            content = await self._get_json_object(key)
            if content is None:
                return []
            data = json.loads(content)
            return [Observation(**item) for item in data]
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in observations for job {job_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to load observations for job {job_id}: {e}")
            return []

    async def append_observations(
        self,
        job_id: str,
        new_observations: list[Observation]
    ) -> None:
        """Append new observations to existing file.

        Loads existing observations, merges with new ones, and saves back.
        Used when specialized agents add observations incrementally.

        Args:
            job_id: Job identifier
            new_observations: New observations to append

        Raises:
            Exception: If save fails
        """
        existing = await self.load_observations(job_id)
        all_observations = existing + new_observations
        await self.save_observations(job_id, all_observations)
        logger.info(
            f"Appended {len(new_observations)} observations for job {job_id} "
            f"(total: {len(all_observations)})"
        )

    async def save_proposals(
        self,
        job_id: str,
        proposals: list[Proposal]
    ) -> str:
        """Save proposals to S3.

        Args:
            job_id: Job identifier
            proposals: List of Proposal objects

        Returns:
            S3 key where proposals were saved

        Raises:
            Exception: If upload fails
        """
        content = json.dumps(
            [prop.model_dump() for prop in proposals],
            indent=2,
            default=self._json_serializer
        )
        key = f"{job_id}/proposals.json"

        try:
            body = content.encode("utf-8")
            await self._put_json_object(key, body)
            logger.info(f"Saved {len(proposals)} proposals for job {job_id}")
            return key
        except Exception as e:
            logger.error(f"Failed to save proposals for job {job_id}: {e}")
            raise

    async def load_proposals(self, job_id: str) -> list[Proposal]:
        """Load proposals from S3.

        Args:
            job_id: Job identifier

        Returns:
            List of Proposal objects (empty list if not found)
        """
        key = f"{job_id}/proposals.json"

        try:
            content = await self._get_json_object(key)
            if content is None:
                return []
            data = json.loads(content)
            return [Proposal(**item) for item in data]
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in proposals for job {job_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to load proposals for job {job_id}: {e}")
            return []

    async def update_proposal_status(
        self,
        job_id: str,
        proposal_id: str,
        status: str,
        **fields: Any
    ) -> bool:
        """Update a single proposal's status.

        Loads all proposals, updates the matching one, and saves back.

        Args:
            job_id: Job identifier
            proposal_id: Proposal ID to update
            status: New status value
            **fields: Additional fields to update (e.g., reviewed_by, review_notes)

        Returns:
            True if proposal was found and updated, False otherwise
        """
        proposals = await self.load_proposals(job_id)

        found = False
        for proposal in proposals:
            if proposal.id == proposal_id:
                setattr(proposal, "status", status)
                for key, value in fields.items():
                    if hasattr(proposal, key):
                        setattr(proposal, key, value)
                found = True
                break

        if found:
            await self.save_proposals(job_id, proposals)
            logger.info(
                f"Updated proposal {proposal_id} to status '{status}' "
                f"for job {job_id}"
            )
        else:
            logger.warning(
                f"Proposal {proposal_id} not found for job {job_id}"
            )

        return found

    async def update_observation_status(
        self,
        job_id: str,
        observation_id: str,
        status: str,
        resolved_by: str | None = None
    ) -> bool:
        """Update a single observation's status.

        Loads all observations, updates the matching one, and saves back.

        Args:
            job_id: Job identifier
            observation_id: Observation ID to update
            status: New status value
            resolved_by: Proposal ID that resolved this (if applicable)

        Returns:
            True if observation was found and updated, False otherwise
        """
        observations = await self.load_observations(job_id)

        found = False
        for observation in observations:
            if observation.id == observation_id:
                setattr(observation, "status", status)
                if resolved_by:
                    observation.resolved_by = resolved_by
                found = True
                break

        if found:
            await self.save_observations(job_id, observations)
            logger.info(
                f"Updated observation {observation_id} to status '{status}' "
                f"for job {job_id}"
            )
        else:
            logger.warning(
                f"Observation {observation_id} not found for job {job_id}"
            )

        return found

    async def load_current_markdown(self, job_id: str) -> str | None:
        """Load the current markdown content for a job.

        Tries to load the current working version first ({job_id}.md),
        falls back to v0 version ({job_id}-v0.md) if not found.

        Args:
            job_id: Job identifier

        Returns:
            Markdown content as string, or None if not found
        """
        # Try current version first
        try:
            response = self.storage.s3_client.get_object(
                Bucket=self.storage.results_bucket,
                Key=f"{job_id}.md"
            )
            content: str = response["Body"].read().decode("utf-8")
            return content
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchKey":
                logger.error(f"Failed to load current markdown for job {job_id}: {e}")
                return None

        # Fall back to v0 version
        try:
            response = self.storage.s3_client.get_object(
                Bucket=self.storage.results_bucket,
                Key=f"{job_id}-v0.md"
            )
            content = response["Body"].read().decode("utf-8")
            return content
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.warning(f"No markdown found for job {job_id}")
                return None
            logger.error(f"Failed to load v0 markdown for job {job_id}: {e}")
            return None

    async def save_current_markdown(self, job_id: str, content: str) -> str:
        """Save the current markdown content for a job.

        Args:
            job_id: Job identifier
            content: Markdown content

        Returns:
            S3 key where markdown was saved
        """
        key = f"{job_id}.md"
        try:
            self.storage.s3_client.put_object(
                Bucket=self.storage.results_bucket,
                Key=key,
                Body=content.encode("utf-8"),
                ContentType="text/markdown"
            )
            logger.info(f"Saved current markdown for job {job_id}")
            return key
        except Exception as e:
            logger.error(f"Failed to save markdown for job {job_id}: {e}")
            raise

    async def save_application_log(
        self,
        job_id: str,
        log_entries: list[dict[str, Any]]
    ) -> str:
        """Save application log to S3 for audit trail.

        The application log records the outcome of each proposal application,
        including success/failure status, method used, and timestamps.

        Args:
            job_id: Job identifier
            log_entries: List of log entry dictionaries with:
                - proposal_id: ID of the proposal
                - status: "applied" or "failed"
                - method: Matching method used (for applied)
                - error: Error message (for failed)
                - timestamp: ISO timestamp

        Returns:
            S3 key where log was saved

        Raises:
            Exception: If upload fails
        """
        content = json.dumps(log_entries, indent=2, default=self._json_serializer)
        key = f"{job_id}/application-log.json"

        try:
            body = content.encode("utf-8")
            await self._put_json_object(key, body)
            logger.info(f"Saved application log for job {job_id} ({len(log_entries)} entries)")
            return key
        except Exception as e:
            logger.error(f"Failed to save application log for job {job_id}: {e}")
            raise

    async def load_application_log(self, job_id: str) -> list[dict[str, Any]]:
        """Load application log from S3.

        Args:
            job_id: Job identifier

        Returns:
            List of log entry dictionaries (empty list if not found)
        """
        key = f"{job_id}/application-log.json"

        try:
            content = await self._get_json_object(key)
            if content is None:
                return []
            result: list[dict[str, Any]] = json.loads(content)
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in application log for job {job_id}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to load application log for job {job_id}: {e}")
            return []

    # Helper methods

    async def _put_json_object(self, key: str, body: bytes) -> None:
        """Put JSON object to results bucket.

        Args:
            key: S3 key
            body: JSON content as bytes
        """
        self.storage.s3_client.put_object(
            Bucket=self.storage.results_bucket,
            Key=key,
            Body=body,
            ContentType="application/json"
        )

    async def _get_json_object(self, key: str) -> str | None:
        """Get JSON object from results bucket.

        Args:
            key: S3 key

        Returns:
            JSON content as string, or None if not found
        """
        try:
            response = self.storage.s3_client.get_object(
                Bucket=self.storage.results_bucket,
                Key=key
            )
            content: str = response["Body"].read().decode("utf-8")
            return content
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    @staticmethod
    def _json_serializer(obj: Any) -> Any:
        """JSON serializer for objects not serializable by default.

        Args:
            obj: Object to serialize

        Returns:
            Serializable representation
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
