"""Document processing endpoints."""

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..config import settings
from ..dependencies import (
    get_job_service,
    get_queue_service,
    get_remediation_storage,
    get_s3_url_service,
    get_storage_service,
)
from ..services import JobService, QueueService, S3URLService, StorageService
from ..services.remediation_storage_service import RemediationStorageService
from ..shared.constants.queues import PROCESSING_QUEUE
from ..shared.models.queue import ProcessingQueuePayload
from .schemas import (
    AgentsPhase,
    AnalysisPhase,
    AwaitingCorrectionApprovalResponse,
    AwaitingPIIApprovalResponse,
    CompletedResponse,
    ConsolidationPhase,
    CorrectionDecision,
    CorrectionItem,
    CorrectionSummary,
    DeniedResponse,
    DocumentStatusResponse,
    ExtractionPhase,
    FailedResponse,
    LLMCostInfo,
    ObservationSummary,
    PageFeatureSummary,
    PIIFinding,
    PIIScanningResponse,
    ProcessingPhasesResponse,
    ProcessingResponse,
    ProposalSummary,
)

router = APIRouter(prefix="/api/documents", tags=["Documents"])


def _build_llm_cost(job: dict[str, Any]) -> LLMCostInfo | None:
    """Build LLM cost info from job data.

    Costs accumulate across all processing phases (structure analysis + transcription).
    """
    llm_cost_cents = job.get("llm_cost_cents")
    if llm_cost_cents is None:
        return None

    try:
        total_cents = float(llm_cost_cents)
    except (TypeError, ValueError):
        return None

    # Get aggregate token counts (accumulated across all phases)
    input_tokens = int(job.get("llm_input_tokens", 0) or 0)
    output_tokens = int(job.get("llm_output_tokens", 0) or 0)
    total_tokens = int(job.get("llm_total_tokens", 0) or 0)

    return LLMCostInfo(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_cents=total_cents,
        estimated_cost_dollars=total_cents / 100.0,
    )


class JobSubmissionResponse(BaseModel):
    """Response for document submission."""

    job_id: str
    status: str
    estimated_completion_minutes: int
    created_at: str


@router.post(
    "/submit", response_model=JobSubmissionResponse, status_code=status.HTTP_201_CREATED
)
async def submit_document(
    file: UploadFile = File(...),
    skip_pii_scan: bool = Form(default=False, description="Skip PII scanning and queue directly for processing"),
    skip_reason: str | None = Form(default=None, description="Optional reason for skipping PII scan (for audit trail)"),
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service),
    job_service: JobService = Depends(get_job_service),
) -> JobSubmissionResponse:
    """Submit a PDF document for processing.

    Args:
        file: PDF file to process
        skip_pii_scan: If True, bypass PII scanning and queue directly for processing
        skip_reason: Optional justification for skipping PII scan (recorded in audit trail)
    """
    job_id, s3_key = await storage.store_document(file)

    if skip_pii_scan:
        # Direct to processing queue (bypass PII scanning)
        await job_service.create_job(
            job_id,
            s3_key,
            status="processing",
            original_filename=file.filename,
            pii_skipped=True,
            pii_skip_reason=skip_reason or "User requested PII scan skip"
        )
        processing_payload = ProcessingQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            approved_at=None  # No approval needed - PII scan skipped
        )
        await queue.enqueue(PROCESSING_QUEUE, processing_payload)

        return JobSubmissionResponse(
            job_id=job_id,
            status="processing",
            estimated_completion_minutes=settings.estimated_processing_minutes,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    else:
        # Standard flow: PII scanning first
        await job_service.create_job(
            job_id, s3_key, status="pii_scanning", original_filename=file.filename
        )
        await queue.queue_pii_job(job_id, s3_key)

        return JobSubmissionResponse(
            job_id=job_id,
            status="pii_scanning",
            estimated_completion_minutes=settings.estimated_processing_minutes,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )


@router.get("/{job_id}", response_model=DocumentStatusResponse)
async def get_job(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    url_service: S3URLService = Depends(get_s3_url_service),
) -> DocumentStatusResponse:
    """
    Get current status of a processing job.

    Returns a clean, status-specific response with only relevant fields.
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found"
        )

    base = {
        "job_id": job["job_id"],
        "status": job["status"],
        "filename": job.get("original_filename"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }

    match job["status"]:
        case "pii_scanning":
            return PIIScanningResponse(
                **base,
                estimated_completion_minutes=settings.estimated_processing_minutes,
            )

        case "processing":
            return ProcessingResponse(
                **base,
                estimated_completion_minutes=settings.estimated_processing_minutes,
                pii_skipped=job.get("pii_skipped") == "true" if job.get("pii_skipped") else None,
            )

        case "awaiting_approval":
            pii_findings = [
                PIIFinding(**f) for f in (job.get("pii_findings") or [])
            ]
            token = job.get("approval_token", "")
            return AwaitingPIIApprovalResponse(
                **base,
                pii_findings=pii_findings,
                approval_token=token,
                approval_expires_at=job.get("approval_expires_at", ""),
                approval_url=f"/api/approval/{token}/decision",
            )

        case "awaiting_correction_approval":
            # Build correction summary and full correction list
            correction_results = job.get("correction_results", [])
            by_type: dict[str, int] = {}
            total = 0
            auto_applied = 0
            manual_review = 0
            corrections_list: list[CorrectionItem] = []

            for page_result in correction_results:
                page_num = page_result.get("page", 1)
                for c in page_result.get("corrections", []):
                    ctype = c.get("type", "other")
                    is_auto = c.get("is_auto_applied", False)
                    by_type[ctype] = by_type.get(ctype, 0) + 1
                    total += 1
                    if is_auto:
                        auto_applied += 1
                    else:
                        manual_review += 1

                    corrections_list.append(CorrectionItem(
                        page=page_num,
                        type=ctype,
                        original_snippet=c.get("original", "")[:200],
                        corrected_snippet=c.get("corrected", "")[:200],
                        confidence=c.get("confidence", 0.0),
                        explanation=c.get("explanation", ""),
                        is_auto_applied=is_auto,
                    ))

            token = job.get("correction_approval_token", "")
            # page_image_urls is stored as comma-separated string in Redis
            page_keys_raw = job.get("page_image_urls", "")
            page_keys = page_keys_raw.split(",") if page_keys_raw else []

            return AwaitingCorrectionApprovalResponse(
                **base,
                correction_summary=CorrectionSummary(
                    total_corrections=total,
                    auto_applied_count=auto_applied,
                    manual_review_count=manual_review,
                    confidence_score=float(job.get("confidence_score", 0.0)),
                    corrections_by_type=by_type,
                ),
                corrections=corrections_list,
                approval_token=token,
                approval_expires_at=job.get("correction_expires_at", ""),
                review_url=f"/api/corrections/{job_id}/review?token={token}",
                original_markdown_url=await url_service.generate_url(
                    job["original_markdown_key"], bucket=url_service.results_bucket
                ),
                corrected_markdown_url=await url_service.generate_url(
                    job["corrected_markdown_key"], bucket=url_service.results_bucket
                ),
                page_image_urls=[
                    await url_service.generate_url(k, bucket=url_service.temp_bucket)
                    for k in page_keys
                ],
                llm_cost=_build_llm_cost(job) or LLMCostInfo(
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    estimated_cost_cents=0,
                    estimated_cost_dollars=0
                ),
            )

        case "completed":
            return CompletedResponse(
                **base,
                markdown_url=await url_service.generate_url(
                    f"{job_id}.md", bucket=url_service.results_bucket
                ),
                confidence_score=float(job.get("confidence_score", 0.0)),
                correction_decision=CorrectionDecision(
                    # Default to "auto_completed" when no manual review was performed
                    decision=job.get("correction_decision", "auto_completed"),
                    reviewed_by=job.get("correction_reviewed_by", ""),
                    reviewed_at=job.get("correction_reviewed_at", ""),
                    justification=job.get("correction_justification", ""),
                ),
                llm_cost=_build_llm_cost(job) or LLMCostInfo(
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    estimated_cost_cents=0,
                    estimated_cost_dollars=0
                ),
            )

        case "failed":
            return FailedResponse(
                **base,
                error=job.get("error", "Unknown error"),
            )

        case "denied":
            return DeniedResponse(
                **base,
                reason=job.get("denial_reason", "PII not approved"),
            )

        case _:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unknown job status: {job['status']}",
            )


@router.get("/{job_id}/phases", response_model=ProcessingPhasesResponse)
async def get_job_phases(
    job_id: str,
    show_raw: bool = False,
    job_service: JobService = Depends(get_job_service),
    url_service: S3URLService = Depends(get_s3_url_service),
    remediation_storage: RemediationStorageService = Depends(get_remediation_storage),
) -> ProcessingPhasesResponse:
    """
    Get detailed processing phase outputs for a job.

    Returns structured data from each phase of the processing pipeline:
    - Analysis: Document structure, page features, heading tree
    - Extraction: Original markdown (v0), extraction confidence
    - Agents: Observations from specialized agents
    - Consolidation: Proposals with auto/manual routing

    Query params:
        show_raw: Include full raw JSON from each phase artifact
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found"
        )

    # Load artifacts from S3
    manifest = await remediation_storage.load_manifest(job_id)
    observations = await remediation_storage.load_observations(job_id)
    proposals = await remediation_storage.load_proposals(job_id)

    # Build Analysis Phase
    analysis_phase = AnalysisPhase(status="skipped")
    if manifest:
        page_features_list = []
        for pf in manifest.page_features:
            page_features_list.append(PageFeatureSummary(
                page_num=pf.page_num,
                has_images=pf.has_images,
                image_count=pf.image_count,
                has_tables=pf.has_tables,
                table_count=pf.table_count,
                has_lists=pf.has_lists,
                complexity_score=pf.complexity_score,
            ))

        # Build heading tree from manifest's heading_tree_json
        heading_tree = None
        layout_type = None
        if manifest.heading_tree_json:
            try:
                tree_data = json.loads(manifest.heading_tree_json)
                heading_tree = tree_data
                # HeadingTree model has layout_type at document level
                layout_type = tree_data.get("layout_type")
            except json.JSONDecodeError:
                pass

        # If no layout from heading tree, derive from page features
        if not layout_type and manifest.page_features:
            layouts = [pf.layout_type for pf in manifest.page_features]
            # Use most common layout as the document layout
            layout_counts = Counter(layouts)
            layout_type = layout_counts.most_common(1)[0][0] if layout_counts else None

        analysis_phase = AnalysisPhase(
            status="completed",
            document_title=manifest.document_title,
            document_type=manifest.document_type,
            total_pages=manifest.total_pages,
            layout_type=layout_type,
            required_agents=manifest.required_agents,
            analysis_confidence=manifest.analysis_confidence,
            page_features=page_features_list,
            heading_tree=heading_tree,
            raw_manifest=manifest.model_dump(mode="json") if show_raw else None,
        )

    # Build Extraction Phase
    extraction_phase = ExtractionPhase(status="skipped")
    v0_key = f"{job_id}-v0.md"
    try:
        v0_url = await url_service.generate_url(v0_key, bucket=url_service.results_bucket)
        extraction_phase = ExtractionPhase(
            status="completed",
            markdown_url=v0_url,
            confidence_score=float(job.get("confidence_score", 0.0)),
            extraction_model=job.get("extraction_model"),
        )
    except Exception:
        pass  # v0 may not exist yet

    # Build Agents Phase
    agents_phase = AgentsPhase(status="skipped")
    if observations:
        agents_run = list(set(obs.agent for obs in observations))
        obs_summaries = [
            ObservationSummary(
                id=obs.id,
                agent=obs.agent,
                severity=obs.severity,
                confidence=obs.confidence,
                route=obs.route,
                status=obs.status,
                visual_description=obs.visual_description[:200] if obs.visual_description else None,
                markup_description=obs.markup_description[:200] if obs.markup_description else None,
                page_num=obs.location.page_num if obs.location else None,
            )
            for obs in observations
        ]
        agents_phase = AgentsPhase(
            status="completed",
            agents_run=agents_run,
            observation_count=len(observations),
            observations=obs_summaries,
            raw_observations=[obs.model_dump(mode="json") for obs in observations] if show_raw else None,
        )

    # Build Consolidation Phase
    consolidation_phase = ConsolidationPhase(status="skipped")
    if proposals:
        auto_count = sum(1 for p in proposals if p.route == "auto")
        manual_count = sum(1 for p in proposals if p.route == "manual")

        proposal_summaries = [
            ProposalSummary(
                id=p.id,
                route=p.route,
                status=p.status,
                page_nums=p.page_nums,
                resolves_count=len(p.resolves),
                search_preview=p.diff.search[:100] if p.diff else "",
                replace_preview=p.diff.replace[:100] if p.diff else "",
                justification=p.justification,
                estimated_impact=p.estimated_impact,
            )
            for p in proposals
        ]
        consolidation_phase = ConsolidationPhase(
            status="completed",
            proposal_count=len(proposals),
            auto_count=auto_count,
            manual_count=manual_count,
            proposals=proposal_summaries,
            raw_proposals=[p.model_dump(mode="json") for p in proposals] if show_raw else None,
        )

    return ProcessingPhasesResponse(
        job_id=job["job_id"],
        filename=job.get("original_filename", ""),
        status=job["status"],
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        analysis=analysis_phase,
        extraction=extraction_phase,
        agents=agents_phase,
        consolidation=consolidation_phase,
        total_llm_cost=_build_llm_cost(job),
    )
