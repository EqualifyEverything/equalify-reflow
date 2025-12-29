"""Debug bundle service for pipeline observability.

This service manages the collection and packaging of debug artifacts
for jobs that have debug bundle generation enabled.

Debug artifacts are stored in S3 under the debug/ prefix and include:
- Agent prompts and responses
- Intermediate outputs
- Page images
- Final results

The bundle is packaged as a zip file for download.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from src.config import settings
from src.shared.models.debug_bundle import (
    DebugArtifact,
    DebugBundleManifest,
    DebugPhaseSummary,
)

if TYPE_CHECKING:
    from src.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# S3 prefix for debug artifacts
DEBUG_PREFIX = "debug"


class DebugBundleService:
    """Service for collecting and packaging debug artifacts.

    This service provides methods to:
    1. Save individual debug artifacts during processing
    2. Generate a complete debug bundle zip file
    3. Clean up debug artifacts after download

    Artifacts are stored in S3 with the structure:
        debug/{job_id}/phase_{N}_{name}/{agent_name}.json

    Example:
        >>> service = DebugBundleService(storage_service)
        >>> await service.save_artifact(
        ...     job_id="job123",
        ...     phase="analyze",
        ...     agent_name="layout",
        ...     artifact=DebugArtifact(...)
        ... )
        >>> zip_bytes = await service.generate_bundle("job123")
    """

    def __init__(self, storage_service: "StorageService"):
        """Initialize debug bundle service.

        Args:
            storage_service: StorageService instance for S3 operations
        """
        self.storage = storage_service

    async def save_artifact(
        self,
        job_id: str,
        phase: str,
        agent_name: str,
        artifact: DebugArtifact,
    ) -> str:
        """Save a debug artifact to S3.

        Args:
            job_id: Job identifier
            phase: Pipeline phase (analyze, extract, refine, assemble)
            agent_name: Name of the agent
            artifact: DebugArtifact with prompt/response data

        Returns:
            S3 key where artifact was saved
        """
        key = f"{DEBUG_PREFIX}/{job_id}/{phase}/{agent_name}.json"
        content = artifact.model_dump_json(indent=2)

        try:
            self.storage.s3_client.put_object(
                Bucket=self.storage.temp_bucket,
                Key=key,
                Body=content.encode("utf-8"),
                ContentType="application/json",
            )
            logger.debug(f"Saved debug artifact: {key}")
            return key
        except ClientError as e:
            logger.error(f"Failed to save debug artifact {key}: {e}")
            raise

    async def save_artifact_from_agent_run(
        self,
        job_id: str,
        phase: str,
        agent_name: str,
        input_data: Any,
        prompt: str,
        response_raw: str,
        output_parsed: Any,
        tokens: dict[str, int],
        cost_cents: float,
        model_id: str,
        duration_ms: float | None = None,
    ) -> str:
        """Save debug artifact from an agent run.

        Convenience method that constructs a DebugArtifact from agent run data.

        Args:
            job_id: Job identifier
            phase: Pipeline phase
            agent_name: Name of the agent
            input_data: Input data (will be JSON serialized)
            prompt: Full prompt sent to LLM
            response_raw: Raw LLM response
            output_parsed: Parsed output (will be JSON serialized)
            tokens: Token counts {"input": N, "output": N, "total": N}
            cost_cents: Estimated cost in cents
            model_id: Model identifier used
            duration_ms: Execution duration in milliseconds

        Returns:
            S3 key where artifact was saved
        """
        # Serialize input/output to JSON strings
        if isinstance(input_data, str):
            input_summary = input_data
        else:
            try:
                input_summary = json.dumps(input_data, indent=2, default=str)
            except (TypeError, ValueError):
                input_summary = str(input_data)

        if isinstance(output_parsed, str):
            output_str = output_parsed
        else:
            try:
                if hasattr(output_parsed, "model_dump_json"):
                    output_str = output_parsed.model_dump_json(indent=2)
                else:
                    output_str = json.dumps(output_parsed, indent=2, default=str)
            except (TypeError, ValueError):
                output_str = str(output_parsed)

        artifact = DebugArtifact(
            agent_name=agent_name,
            phase=phase,
            timestamp=datetime.now(UTC),
            input_summary=input_summary,
            prompt=prompt,
            response_raw=response_raw,
            output_parsed=output_str,
            metadata={
                "tokens": tokens,
                "cost_cents": cost_cents,
                "model_id": model_id,
                "duration_ms": duration_ms,
            },
        )

        return await self.save_artifact(job_id, phase, agent_name, artifact)

    async def generate_bundle(
        self,
        job_id: str,
        include_pdf: bool = True,
        include_page_images: bool = True,
    ) -> bytes:
        """Generate a complete debug bundle zip file.

        Collects all artifacts for the job and packages them into a zip file
        with the following structure:

            debug_{job_id}.zip
            ├── README.md
            ├── manifest.json
            ├── input/
            │   ├── original.pdf (if available)
            │   └── pages/
            │       ├── page_001.png
            │       └── ...
            ├── phase_1_analyze/
            │   ├── layout.json
            │   └── ...
            ├── phase_2_extract/
            │   └── ...
            ├── phase_3_refine/
            │   └── ...
            ├── phase_4_assemble/
            │   └── ...
            └── output/
                ├── manifest.json
                ├── observations.json
                └── final_markdown.md

        Args:
            job_id: Job identifier
            include_pdf: Include original PDF in bundle
            include_page_images: Include page images in bundle

        Returns:
            Zip file contents as bytes
        """
        logger.info(f"Generating debug bundle for job {job_id}")

        # Create in-memory zip file
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Track stats for manifest
            artifacts_count = 0
            phases: dict[str, DebugPhaseSummary] = {}

            # 1. Collect debug artifacts from temp bucket
            artifacts_count += await self._add_debug_artifacts(zf, job_id, phases)

            # 2. Add original PDF if requested
            if include_pdf:
                await self._add_original_pdf(zf, job_id)

            # 3. Add page images if requested
            if include_page_images:
                await self._add_page_images(zf, job_id)

            # 4. Add output files from results bucket
            await self._add_output_files(zf, job_id)

            # 5. Generate and add manifest
            manifest = await self._generate_manifest(job_id, phases, artifacts_count)
            zf.writestr("manifest.json", manifest.model_dump_json(indent=2))

            # 6. Generate and add README
            readme = self._generate_readme(job_id, manifest)
            zf.writestr("README.md", readme)

        zip_buffer.seek(0)
        logger.info(
            f"Debug bundle generated for job {job_id}: {artifacts_count} artifacts, {len(zip_buffer.getvalue())} bytes"
        )
        return zip_buffer.getvalue()

    async def _add_debug_artifacts(
        self,
        zf: zipfile.ZipFile,
        job_id: str,
        phases: dict[str, DebugPhaseSummary],
    ) -> int:
        """Add debug artifacts from S3 to zip file.

        Returns count of artifacts added.
        """
        artifacts_count = 0
        prefix = f"{DEBUG_PREFIX}/{job_id}/"

        try:
            response = self.storage.s3_client.list_objects_v2(
                Bucket=self.storage.temp_bucket,
                Prefix=prefix,
            )

            for obj in response.get("Contents", []):
                key = obj["Key"]
                # Parse key: debug/{job_id}/{phase}/{agent}.json
                parts = key.replace(prefix, "").split("/")
                if len(parts) >= 2:
                    phase = parts[0]
                    filename = "/".join(parts[1:])

                    # Fetch artifact content
                    try:
                        artifact_response = self.storage.s3_client.get_object(
                            Bucket=self.storage.temp_bucket,
                            Key=key,
                        )
                        content = artifact_response["Body"].read()

                        # Add to zip under phase directory
                        zip_path = f"{phase}/{filename}"
                        zf.writestr(zip_path, content)
                        artifacts_count += 1

                        # Update phase summary
                        if phase not in phases:
                            phases[phase] = DebugPhaseSummary(phase=phase)
                        phases[phase].agents_run.append(filename.replace(".json", ""))

                        # Parse artifact for token/cost info
                        try:
                            artifact_data = json.loads(content)
                            metadata = artifact_data.get("metadata", {})
                            tokens = metadata.get("tokens", {})
                            phases[phase].total_tokens += tokens.get("total", 0)
                            phases[phase].total_cost_cents += metadata.get("cost_cents", 0)
                        except json.JSONDecodeError:
                            pass

                    except ClientError as e:
                        logger.warning(f"Failed to fetch artifact {key}: {e}")

        except ClientError as e:
            logger.warning(f"Failed to list debug artifacts for {job_id}: {e}")

        return artifacts_count

    async def _add_original_pdf(self, zf: zipfile.ZipFile, job_id: str) -> None:
        """Add original PDF from temp bucket to zip file."""
        # Try common key patterns
        for key_pattern in [
            f"temp/{job_id}/original.pdf",
            f"temp/{job_id}.pdf",
            f"{job_id}/original.pdf",
        ]:
            try:
                response = self.storage.s3_client.get_object(
                    Bucket=self.storage.temp_bucket,
                    Key=key_pattern,
                )
                content = response["Body"].read()
                zf.writestr("input/original.pdf", content)
                logger.debug(f"Added original PDF from {key_pattern}")
                return
            except ClientError:
                continue

        logger.debug(f"Original PDF not found for job {job_id}")

    async def _add_page_images(self, zf: zipfile.ZipFile, job_id: str) -> None:
        """Add page images from temp bucket to zip file."""
        prefix = f"temp/{job_id}/pages/"

        try:
            response = self.storage.s3_client.list_objects_v2(
                Bucket=self.storage.temp_bucket,
                Prefix=prefix,
            )

            for obj in response.get("Contents", []):
                key = obj["Key"]
                filename = key.replace(prefix, "")

                try:
                    img_response = self.storage.s3_client.get_object(
                        Bucket=self.storage.temp_bucket,
                        Key=key,
                    )
                    content = img_response["Body"].read()
                    zf.writestr(f"input/pages/{filename}", content)
                except ClientError as e:
                    logger.warning(f"Failed to fetch page image {key}: {e}")

        except ClientError as e:
            logger.debug(f"No page images found for job {job_id}: {e}")

    async def _add_output_files(self, zf: zipfile.ZipFile, job_id: str) -> None:
        """Add output files from results bucket to zip file."""
        output_files = [
            (f"{job_id}/manifest.json", "output/manifest.json"),
            (f"{job_id}/observations.json", "output/observations.json"),
            (f"{job_id}/auto_corrections.json", "output/auto_corrections.json"),
            (f"{job_id}/review_items.json", "output/review_items.json"),
            (f"{job_id}.md", "output/final_markdown.md"),
            (f"{job_id}-v0.md", "output/markdown_v0.md"),
            (f"jobs/{job_id}/processing_result.json", "output/processing_result.json"),
        ]

        for s3_key, zip_path in output_files:
            try:
                response = self.storage.s3_client.get_object(
                    Bucket=self.storage.results_bucket,
                    Key=s3_key,
                )
                content = response["Body"].read()
                zf.writestr(zip_path, content)
                logger.debug(f"Added output file: {zip_path}")

                # Extract final markdown from processing_result.json
                if zip_path == "output/processing_result.json":
                    try:
                        result_data = json.loads(content.decode("utf-8"))
                        if "markdown" in result_data:
                            zf.writestr("output/final_markdown.md", result_data["markdown"])
                            logger.debug("Extracted final_markdown.md from processing_result.json")
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Could not extract markdown from processing_result.json: {e}")
            except ClientError:
                # File doesn't exist, skip silently
                pass

    async def _generate_manifest(
        self,
        job_id: str,
        phases: dict[str, DebugPhaseSummary],
        artifacts_count: int,
    ) -> DebugBundleManifest:
        """Generate bundle manifest with summary statistics."""
        # Calculate totals
        total_tokens = sum(p.total_tokens for p in phases.values())
        total_cost = sum(p.total_cost_cents for p in phases.values())

        # Sort phases by name for consistent ordering
        sorted_phases = sorted(phases.values(), key=lambda p: p.phase)

        return DebugBundleManifest(
            job_id=job_id,
            created_at=datetime.now(UTC),
            total_tokens=total_tokens,
            total_cost_cents=total_cost,
            phases=sorted_phases,
            agents_executed=artifacts_count,
            artifacts_count=artifacts_count,
        )

    def _generate_readme(self, job_id: str, manifest: DebugBundleManifest) -> str:
        """Generate README.md content for the bundle."""
        phase_table = "\n".join(
            f"| {p.phase} | {len(p.agents_run)} | {p.total_tokens:,} | ${p.total_cost_cents / 100:.4f} |"
            for p in manifest.phases
        )

        return f"""# Debug Bundle: {job_id}

Generated: {manifest.created_at.isoformat()}

## Summary

- **Total Agents Executed:** {manifest.agents_executed}
- **Total Tokens:** {manifest.total_tokens:,}
- **Total Cost:** ${manifest.total_cost_cents / 100:.4f}

## Phase Summary

| Phase | Agents | Tokens | Cost |
|-------|--------|--------|------|
{phase_table}

## Bundle Contents

- `input/` - Original PDF and page images
- `phase_*/` - Debug artifacts for each pipeline phase
- `output/` - Final results (manifest, observations, markdown)

## How to Analyze This Bundle

This bundle contains the complete execution trace of the PDF processing pipeline.
You can use an LLM to help analyze issues:

### Quick Start

Share this bundle with an LLM and ask:

> "I'm debugging a PDF accessibility pipeline. Review the contents of this debug
> bundle and help me understand what happened during processing."

### Common Analysis Questions

1. **Why did extraction produce incorrect output?**
   - Look at `phase_2_extract/` for the extraction agent's prompt and response
   - Compare `input/pages/` images with the extracted markdown

2. **Why was an image classified incorrectly?**
   - Check `phase_3_refine/figures_*.json` for classification reasoning
   - Review the confidence scores and quality signals

3. **Why did structure validation fail?**
   - Look at `phase_3_refine/structure_*.json` for validation observations
   - Check the heading hierarchy in `output/manifest.json`

4. **What corrections were applied automatically?**
   - Review `output/auto_corrections.json` for all auto-applied changes
   - Check `output/observations.json` for linked observations

## File Reference

### Debug Artifact Format

Each `.json` file in `phase_*/` contains:

```json
{{
  "agent_name": "layout",
  "phase": "analyze",
  "timestamp": "2024-01-01T00:00:00Z",
  "input_summary": "...",
  "prompt": "Full prompt sent to LLM...",
  "response_raw": "Raw LLM response...",
  "output_parsed": "Parsed/validated output as JSON...",
  "metadata": {{
    "tokens": {{"input": 1000, "output": 500, "total": 1500}},
    "cost_cents": 0.05,
    "model_id": "anthropic.claude-3-haiku-...",
    "duration_ms": 1234
  }}
}}
```
"""

    async def cleanup_artifacts(self, job_id: str) -> int:
        """Delete all debug artifacts for a job.

        Called after bundle download or when TTL expires.

        Args:
            job_id: Job identifier

        Returns:
            Number of objects deleted
        """
        prefix = f"{DEBUG_PREFIX}/{job_id}/"
        deleted_count = 0

        try:
            # List all objects with prefix
            response = self.storage.s3_client.list_objects_v2(
                Bucket=self.storage.temp_bucket,
                Prefix=prefix,
            )

            objects_to_delete = [{"Key": obj["Key"]} for obj in response.get("Contents", [])]

            if objects_to_delete:
                self.storage.s3_client.delete_objects(
                    Bucket=self.storage.temp_bucket,
                    Delete={"Objects": objects_to_delete},
                )
                deleted_count = len(objects_to_delete)
                logger.info(f"Cleaned up {deleted_count} debug artifacts for job {job_id}")

        except ClientError as e:
            logger.error(f"Failed to cleanup debug artifacts for {job_id}: {e}")

        return deleted_count

    async def check_debug_enabled(self, job_id: str) -> bool:
        """Check if debug bundle was requested for this job.

        This is a helper to check the job metadata. The actual flag
        is stored in Redis by the job service.

        Args:
            job_id: Job identifier

        Returns:
            True if debug bundle was requested
        """
        # This will be checked via job service in the API layer
        # This method is here for potential future S3-based flag storage
        return True


# Singleton instance management
_debug_bundle_service: DebugBundleService | None = None


def get_debug_bundle_service(
    storage_service: "StorageService",
) -> DebugBundleService:
    """Get or create the debug bundle service.

    Args:
        storage_service: StorageService instance

    Returns:
        DebugBundleService instance
    """
    global _debug_bundle_service

    if _debug_bundle_service is None:
        _debug_bundle_service = DebugBundleService(storage_service)

    return _debug_bundle_service


def reset_debug_bundle_service() -> None:
    """Reset the singleton instance (for testing)."""
    global _debug_bundle_service
    _debug_bundle_service = None


__all__ = [
    "DebugBundleService",
    "get_debug_bundle_service",
    "reset_debug_bundle_service",
    "DEBUG_PREFIX",
]
