# PRD-017: Application Service

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation
**Estimated Effort**: 2 days
**Dependencies**: PRD-011 (Data Models), PRD-016 (Review API)
**Reference**: [Accessibility Remediation Pipeline](../../../docs/features/accessibility-remediation-pipeline.md)
**GitHub Issues**: [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24)

## Problem Statement

After humans approve proposals, the system needs to apply the search-replace edits to the markdown document. This phase:

1. **Applies edits sequentially** - In order to avoid conflicts
2. **Validates matches** - Ensures search text exists and is unique
3. **Handles failures gracefully** - Marks failed proposals, continues with others
4. **Updates observation status** - Marks observations as resolved
5. **Saves final document** - Versioned output to S3

Based on research into AI coding tools, the application uses layered matching strategies to handle minor discrepancies.

## Success Criteria

- [ ] Approved proposals applied via search-replace
- [ ] Exact matching with fallback to whitespace-insensitive
- [ ] Failed proposals marked with reason
- [ ] Observations updated to resolved
- [ ] Final markdown saved with version
- [ ] Application log maintained for audit
- [ ] Job marked completed after successful application

## Technical Requirements

### Application Service

```python
# src/services/application_service.py

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.services.remediation_storage_service import RemediationStorageService
from src.services.storage_service import StorageService
from src.services.job_service import JobService
from src.shared.models.proposal import Proposal
from src.shared.models.observation import Observation

logger = logging.getLogger(__name__)


@dataclass
class ApplicationResult:
    """Result of applying proposals to markdown."""

    applied_count: int
    failed_count: int
    skipped_count: int
    failed_proposals: list[dict[str, Any]]
    final_markdown_url: str | None
    validation_warnings: list[str]


class ApplicationService:
    """Service for applying approved proposals to markdown."""

    def __init__(
        self,
        remediation_storage: RemediationStorageService,
        storage: StorageService,
        job_service: JobService,
    ) -> None:
        self.remediation_storage = remediation_storage
        self.storage = storage
        self.job_service = job_service

    async def apply_approved_proposals(
        self,
        job_id: str,
    ) -> ApplicationResult:
        """Apply all approved proposals to the markdown document.

        Args:
            job_id: Job identifier

        Returns:
            ApplicationResult with counts and any failures
        """
        # Load current state
        markdown = await self.remediation_storage.load_current_markdown(job_id)
        proposals = await self.remediation_storage.load_proposals(job_id)
        observations = await self.remediation_storage.load_observations(job_id)

        if not markdown:
            raise ValueError(f"No markdown found for job {job_id}")

        # Filter to approved proposals
        approved = [p for p in proposals if p.status == "approved"]

        if not approved:
            logger.info(f"Job {job_id}: No approved proposals to apply")
            return ApplicationResult(
                applied_count=0,
                failed_count=0,
                skipped_count=0,
                failed_proposals=[],
                final_markdown_url=None,
                validation_warnings=[],
            )

        logger.info(f"Job {job_id}: Applying {len(approved)} proposals")

        # Sort by page number for predictable ordering
        approved.sort(key=lambda p: min(p.page_nums) if p.page_nums else 0)

        # Apply each proposal
        applied_count = 0
        failed_count = 0
        skipped_count = 0
        failed_proposals = []
        application_log = []

        for proposal in approved:
            result = self._apply_single_proposal(markdown, proposal)

            if result["success"]:
                markdown = result["new_markdown"]
                proposal.status = "applied"
                applied_count += 1

                # Mark resolved observations
                for obs_id in proposal.resolves:
                    for obs in observations:
                        if obs.id == obs_id:
                            obs.status = "resolved"
                            obs.resolved_by = proposal.id

                application_log.append({
                    "proposal_id": proposal.id,
                    "status": "applied",
                    "method": result["method"],
                    "timestamp": datetime.utcnow().isoformat(),
                })

            else:
                proposal.status = "failed"
                proposal.failure_reason = result["error"]
                failed_count += 1
                failed_proposals.append({
                    "proposal_id": proposal.id,
                    "error": result["error"],
                    "search_preview": proposal.diff.search[:100],
                })

                application_log.append({
                    "proposal_id": proposal.id,
                    "status": "failed",
                    "error": result["error"],
                    "timestamp": datetime.utcnow().isoformat(),
                })

        # Validate final markdown
        validation_warnings = self._validate_markdown(markdown)

        # Save updated proposals and observations
        await self.remediation_storage.save_proposals(job_id, proposals)
        await self.remediation_storage.save_observations(job_id, observations)

        # Save application log
        await self.remediation_storage.save_application_log(job_id, application_log)

        # Save final markdown
        final_url = None
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
            failed_proposals=failed_proposals,
            final_markdown_url=final_url,
            validation_warnings=validation_warnings,
        )

    def _apply_single_proposal(
        self,
        markdown: str,
        proposal: Proposal,
    ) -> dict[str, Any]:
        """Apply a single proposal using layered matching.

        Tries in order:
        1. Exact match
        2. Whitespace-normalized match
        3. (Future: fuzzy match)

        Args:
            markdown: Current markdown content
            proposal: Proposal to apply

        Returns:
            Dict with success, new_markdown, method, or error
        """
        search = proposal.diff.search
        replace = proposal.diff.replace

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

        # Method 3: (Future) Fuzzy match with difflib
        # For now, fail if exact and normalized don't work

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

        # Find the actual position in normalized version
        norm_start = markdown_normalized.find(search_normalized)
        norm_end = norm_start + len(search_normalized)

        # Map back to original positions (approximate)
        # This is complex - for now, use regex approach
        pattern = re.sub(r'\s+', r'\\s+', re.escape(search.strip()))
        match = re.search(pattern, markdown)

        if match:
            new_markdown = markdown[:match.start()] + replace + markdown[match.end():]
            return {
                "success": True,
                "new_markdown": new_markdown,
                "method": "whitespace_normalized",
            }

        return {"success": False}

    def _validate_markdown(self, markdown: str) -> list[str]:
        """Validate markdown syntax and structure.

        Returns list of warnings (non-blocking issues).
        """
        warnings = []

        # Check for unbalanced code fences
        fence_count = markdown.count("```")
        if fence_count % 2 != 0:
            warnings.append("Unbalanced code fences detected")

        # Check for broken image syntax
        broken_images = re.findall(r'!\[[^\]]*$', markdown, re.MULTILINE)
        if broken_images:
            warnings.append(f"Potentially broken image syntax: {len(broken_images)} instances")

        # Check for TODO placeholders still present
        todos = re.findall(r'!\[TODO:', markdown)
        if todos:
            warnings.append(f"Unresolved TODO placeholders: {len(todos)}")

        # Check heading structure
        headings = re.findall(r'^(#+)\s', markdown, re.MULTILINE)
        prev_level = 0
        for h in headings:
            level = len(h)
            if level > prev_level + 1 and prev_level > 0:
                warnings.append(f"Heading level skip detected: H{prev_level} -> H{level}")
            prev_level = level

        return warnings


class ApplicationWorker:
    """Background worker for processing application requests."""

    def __init__(
        self,
        application_service: ApplicationService,
        job_service: JobService,
    ) -> None:
        self.service = application_service
        self.job_service = job_service

    async def process_application(self, job_id: str) -> None:
        """Process application for a job.

        Called when job substatus transitions to 'applying'.
        """
        try:
            logger.info(f"Job {job_id}: Starting application phase")

            result = await self.service.apply_approved_proposals(job_id)

            # Determine final status
            if result.failed_count > 0 and result.applied_count == 0:
                # All failed
                await self.job_service.update_job_status(
                    job_id, "failed",
                    error=f"All {result.failed_count} proposals failed to apply",
                )
            else:
                # Success (possibly with some failures)
                # Get final counts
                manual_obs = await self._count_manual_observations(job_id)

                await self.job_service.update_job_status(
                    job_id, "completed",
                    substatus="",
                    markdown_url=result.final_markdown_url,
                    applied_proposals=result.applied_count,
                    failed_proposals_count=result.failed_count,
                    manual_observations=manual_obs,
                    validation_warnings=len(result.validation_warnings),
                )

                logger.info(
                    f"Job {job_id}: Completed - "
                    f"{result.applied_count} applied, "
                    f"{result.failed_count} failed, "
                    f"{manual_obs} manual observations remaining"
                )

        except Exception as e:
            logger.error(f"Job {job_id}: Application failed - {e}", exc_info=True)
            await self.job_service.update_job_status(
                job_id, "failed",
                error=f"Application phase error: {str(e)}",
            )

    async def _count_manual_observations(self, job_id: str) -> int:
        """Count observations still in manual status."""
        observations = await self.service.remediation_storage.load_observations(job_id)
        return sum(1 for o in observations if o.status == "manual")
```

### Integration with Processing Worker

```python
# src/workers/processing_worker.py - Addition

async def check_and_process_applications(
    job_service: JobService,
    application_worker: ApplicationWorker,
) -> None:
    """Check for jobs ready for application and process them."""
    # Find jobs with substatus="applying"
    # This could be a dedicated worker or part of existing processing worker

    # Implementation depends on architecture choice:
    # Option A: Poll Redis for jobs with substatus="applying"
    # Option B: Dedicated application queue
    # Option C: Triggered immediately by review API
```

### Application Log Schema

```python
# Stored in S3: {job_id}/application-log.json

[
    {
        "proposal_id": "prop-123",
        "status": "applied",
        "method": "exact",
        "timestamp": "2024-12-10T10:35:00Z"
    },
    {
        "proposal_id": "prop-124",
        "status": "failed",
        "error": "Search text not found in document",
        "timestamp": "2024-12-10T10:35:01Z"
    }
]
```

### Final S3 Structure

```
results-bucket/{job_id}/
├── output.md                 # Current/final markdown
├── output-v0.md              # Original extraction
├── output-final.md           # Explicit final version
├── manifest.json
├── observations.json
├── proposals.json
└── application-log.json      # Audit trail
```

## Acceptance Criteria

### 1. Exact Matching
- [ ] Finds and replaces exact text
- [ ] Fails if text appears multiple times
- [ ] Preserves surrounding content

### 2. Whitespace-Normalized Matching
- [ ] Falls back when exact fails
- [ ] Handles different whitespace patterns
- [ ] Preserves original formatting where possible

### 3. Failure Handling
- [ ] Failed proposals marked with reason
- [ ] Continues to next proposal after failure
- [ ] Application log captures all outcomes

### 4. Observation Updates
- [ ] Resolved observations marked with proposal ID
- [ ] Manual observations remain open
- [ ] Status transitions valid

### 5. Markdown Validation
- [ ] Checks for unbalanced syntax
- [ ] Warns about TODO placeholders
- [ ] Validates heading structure
- [ ] Warnings non-blocking

### 6. Versioning
- [ ] v0 preserved (original extraction)
- [ ] Final version saved
- [ ] Current version updated

### 7. Job Completion
- [ ] Status set to completed on success
- [ ] Appropriate counts in metadata
- [ ] Manual observations noted

## Deliverables

### Files to Create

```
src/services/
└── application_service.py

src/workers/
└── application_worker.py      # Or integrate with existing

tests/services/
└── test_application_service.py
```

### Files to Modify

```
src/services/remediation_storage_service.py  # Add application log methods
src/workers/processing_worker.py             # Add application trigger
```

## Technical Notes

### Ordering Considerations

Proposals should be applied in a consistent order to avoid conflicts:
- By page number (earliest first)
- By position in document (top to bottom)
- By creation time

### Conflict Prevention

Since search text must be unique, conflicts are rare. However:
- If proposal A changes text that proposal B searches for, B will fail
- Order matters: apply in document order

### Future: Fuzzy Matching

For robustness, consider adding fuzzy matching:
```python
from difflib import SequenceMatcher

def fuzzy_match(markdown: str, search: str, threshold: float = 0.9) -> tuple[int, int] | None:
    """Find best fuzzy match above threshold."""
    # Implementation using SequenceMatcher
    pass
```

Enable this after seeing real-world failure patterns.

### Performance

For large documents with many proposals:
- Apply changes in memory (string manipulation)
- Single write to S3 at end
- Log each proposal individually

## Definition of Done

- [ ] ApplicationService applies proposals correctly
- [ ] Exact and whitespace-normalized matching work
- [ ] Failed proposals handled gracefully
- [ ] Observations updated appropriately
- [ ] Final markdown saved to S3
- [ ] Application log created
- [ ] Job status set to completed
- [ ] Markdown validation implemented
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Documentation complete
