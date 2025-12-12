"""Main processing service orchestrating PDF conversion and AI enhancement.

The processing pipeline consists of:
1. Analysis Phase (Sonnet) - Deep document analysis, manifest generation (PRD-012)
2. Extraction Phase (Haiku) - Guided markdown extraction (PRD-013)
3. Specialized Agents (Sonnet) - Figures, tables, structure, typography (PRD-014)
4. Consolidation (Sonnet) - Observations to proposals (PRD-015)
"""

import base64
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ..agents.agent_router import AgentRouter
from ..agents.analysis_agent import AnalysisAgent
from ..agents.extraction_agent import ExtractionAgent
from ..agents.figures_agent import FiguresAgent
from ..agents.model_tiers import ModelTier, get_model_id
from ..agents.structure_agent import StructureAgent
from ..agents.tables_agent import TablesAgent
from ..agents.typography_agent import TypographyAgent
from ..config import settings
from ..services.application_service import ApplicationService
from ..services.consolidation_service import ConsolidationService
from ..services.correction_approval_service import CorrectionApprovalService
from ..services.document_context_service import DocumentContextService
from ..services.job_service import JobService
from ..services.pdf_converter import PDFConverter
from ..services.queue_service import QueueService
from ..services.remediation_storage_service import RemediationStorageService
from ..services.storage_service import StorageService
from ..shared.constants.statuses import STATUS_AWAITING_CORRECTION_APPROVAL
from ..shared.models.observation import Observation
from ..shared.models.processing import LLMUsage, ProcessingResult
from ..shared.models.proposal import Proposal
from ..shared.models.queue import ProcessingQueuePayload
from ..utils.retry_helpers import retry_with_backoff

logger = logging.getLogger(__name__)


class ProcessingService:
    """Main service for orchestrating PDF-to-accessible-markdown conversion."""

    def __init__(
        self,
        storage_service: StorageService,
        queue_service: QueueService,
        job_service: JobService,
        redis_client: Any = None,
        pdf_converter: PDFConverter | None = None,
        document_context_service: DocumentContextService | None = None,
        remediation_storage_service: RemediationStorageService | None = None,
        correction_approval_service: CorrectionApprovalService | None = None,
        application_service: ApplicationService | None = None,
    ) -> None:
        """Initialize processing service with dependencies.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
            redis_client: Redis client for token storage
            pdf_converter: Optional PDF converter (created if not provided)
            document_context_service: Optional context service for direct extraction
            remediation_storage_service: Optional remediation storage (created if not provided)
            correction_approval_service: Optional correction approval service
            application_service: Optional application service for applying proposals
        """
        self.storage = storage_service
        self.queue = queue_service
        self.job = job_service
        self.redis = redis_client
        self.pdf_converter = pdf_converter or PDFConverter()
        self.context_service = document_context_service or DocumentContextService()
        self.remediation_storage = (
            remediation_storage_service or RemediationStorageService(storage_service)
        )
        self.correction_approval = correction_approval_service
        self.application = application_service

        logger.info("Processing service initialized")

    async def process_document(
        self,
        job: ProcessingQueuePayload,
    ) -> ProcessingResult:
        """Process PDF using analysis + extraction pipeline.

        The processing pipeline:
        1. Analysis Phase (Sonnet) - Deep document analysis, manifest generation
        2. Extraction Phase (Haiku) - Guided markdown extraction (via FullDocumentAgent)
        3. Future: Specialized agents, consolidation, review (PRD-014-017)

        Args:
            job: Processing queue payload with job_id and s3_key

        Returns:
            ProcessingResult with markdown URL and confidence metrics
        """
        start_time = time.time()
        logger.info(f"Starting processing for job {job.job_id}")

        try:
            # Step 1: Update job status to processing with "analyzing" substatus
            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "processing", substatus="analyzing"
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to processing/analyzing",
            )

            # Step 2: Download PDF from S3
            logger.info(
                f"Downloading PDF from s3://{settings.s3_temp_bucket}/{job.s3_key}"
            )
            pdf_content = await self.storage.download_temp_file(s3_key=job.s3_key)
            logger.info(f"Downloaded {len(pdf_content)} bytes")

            # Step 3: Convert PDF with Docling (we need page images)
            logger.info("Converting PDF with Docling for page images...")
            conversion_result = await self.pdf_converter.convert_with_page_images(
                pdf_content
            )

            if not conversion_result.has_page_images:
                raise RuntimeError(
                    "Docling failed to generate page images. "
                    "Cannot proceed without visual content."
                )

            logger.info(
                f"PDF converted: {conversion_result.total_pages} pages with images"
            )

            pages = conversion_result.pages

            # Step 4: Analysis Phase (Sonnet) - PRD-012
            logger.info(
                f"Job {job.job_id}: Starting analysis phase (Sonnet)..."
            )
            analysis_agent = AnalysisAgent()
            manifest, initial_observations, analysis_usage = await analysis_agent.analyze(
                pages, job.job_id
            )

            # Save manifest and initial observations to S3
            await self.remediation_storage.save_manifest(job.job_id, manifest)
            if initial_observations:
                await self.remediation_storage.save_observations(
                    job.job_id, initial_observations
                )

            logger.info(
                f"Job {job.job_id}: Analysis complete - "
                f"{len(manifest.required_agents)} agents needed, "
                f"{len(initial_observations)} initial observations, "
                f"analysis cost: ${analysis_usage.estimated_cost_cents/100:.4f}"
            )

            # Update substatus to "extracting"
            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id,
                    "processing",
                    substatus="extracting",
                    observation_count=len(initial_observations),
                    analysis_model=manifest.analysis_model,
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} substatus to extracting",
            )

            # Step 5: Extraction Phase (Haiku) - PRD-013
            # ExtractionAgent uses manifest guidance for accurate transcription
            logger.info(
                f"Job {job.job_id}: Starting extraction phase (Haiku)..."
            )
            extraction_agent = ExtractionAgent()

            # Extract markdown guided by the manifest from analysis phase
            full_markdown, extraction_confidence, extraction_usage = (
                await extraction_agent.extract(
                    pages=pages,
                    manifest=manifest,
                    job_id=job.job_id,
                )
            )

            # Save v0 markdown (original extraction, never modified)
            await self.storage.upload_result(
                job_id=job.job_id,
                content=full_markdown,
                format="md",
                suffix="v0",
            )

            logger.info(
                f"Job {job.job_id}: Extraction complete - "
                f"{len(full_markdown)} chars, "
                f"confidence: {extraction_confidence:.2f}, "
                f"extraction cost: ${extraction_usage.estimated_cost_cents/100:.4f}"
            )

            # Step 6: Specialized Agents Phase (Sonnet) - PRD-014
            # Run specialized agents based on manifest.required_agents
            specialized_observations: list[Observation] = []
            specialized_usage = LLMUsage(
                input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0
            )

            if manifest.required_agents:
                logger.info(
                    f"Job {job.job_id}: Starting specialized analysis phase (Sonnet)..."
                )

                # Update substatus to "analyzing_specialized"
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id,
                        "processing",
                        substatus="analyzing_specialized",
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} substatus to analyzing_specialized",
                )

                # Create and configure agent router with all specialized agents
                router = AgentRouter()
                router.register_agent("figures", FiguresAgent())
                router.register_agent("tables", TablesAgent())
                router.register_agent("structure", StructureAgent())
                router.register_agent("typography", TypographyAgent())

                # Run required agents and collect observations
                specialized_observations, combined_agent_usage = await router.run_required_agents(
                    manifest=manifest,
                    pages=pages,
                    markdown=full_markdown,
                    job_id=job.job_id,
                )

                # Convert combined usage to LLMUsage for consistent tracking
                specialized_usage = combined_agent_usage.to_llm_usage()

                logger.info(
                    f"Job {job.job_id}: Specialized analysis complete - "
                    f"{len(specialized_observations)} additional observations found, "
                    f"specialized cost: ${specialized_usage.estimated_cost_cents/100:.4f}"
                )

                # Append specialized observations to initial observations
                all_observations = initial_observations + specialized_observations

                # Save updated observations to S3
                if specialized_observations:
                    await self.remediation_storage.save_observations(
                        job.job_id, all_observations
                    )
            else:
                logger.info(
                    f"Job {job.job_id}: No specialized agents required, skipping Phase 3"
                )
                all_observations = initial_observations

            # Step 7: Consolidation Phase (Sonnet) - PRD-015
            # Transform observations into actionable proposals
            consolidation_usage = LLMUsage(
                input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0
            )
            proposal_count = 0
            auto_proposal_count = 0
            manual_proposal_count = 0

            if all_observations:
                logger.info(
                    f"Job {job.job_id}: Starting consolidation phase (Sonnet)..."
                )

                # Update substatus to "consolidating"
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id,
                        "processing",
                        substatus="consolidating",
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} substatus to consolidating",
                )

                # Run consolidation service
                consolidation_service = ConsolidationService(self.remediation_storage)
                proposals, auto_proposal_count, manual_proposal_count, consolidation_usage = (
                    await consolidation_service.consolidate_observations(
                        job_id=job.job_id,
                        markdown=full_markdown,
                    )
                )

                proposal_count = len(proposals)

                logger.info(
                    f"Job {job.job_id}: Consolidation complete - "
                    f"{proposal_count} proposals ({auto_proposal_count} auto, {manual_proposal_count} manual), "
                    f"consolidation cost: ${consolidation_usage.estimated_cost_cents/100:.4f}"
                )
            else:
                logger.info(
                    f"Job {job.job_id}: No observations to consolidate, skipping Phase 4"
                )
                proposals = []

            # Check if correction review is needed
            should_route_to_review = (
                manual_proposal_count > 0 or
                settings.always_require_correction_review
            )

            if should_route_to_review and self.correction_approval and self.redis:
                logger.info(
                    f"Job {job.job_id}: Routing to correction review "
                    f"({manual_proposal_count} manual proposals)"
                )
                return await self._route_to_correction_review(
                    job=job,
                    full_markdown=full_markdown,
                    proposals=proposals if all_observations else [],
                    observations=all_observations,
                    pages=conversion_result.pages,
                    avg_confidence=extraction_confidence,
                    analysis_usage=analysis_usage,
                    extraction_usage=extraction_usage,
                    specialized_usage=specialized_usage,
                    consolidation_usage=consolidation_usage,
                    manifest=manifest,
                    start_time=start_time,
                    auto_proposal_count=auto_proposal_count,
                    manual_proposal_count=manual_proposal_count,
                )

            # Combine usage from all phases
            total_usage = LLMUsage(
                input_tokens=analysis_usage.input_tokens
                + extraction_usage.input_tokens
                + specialized_usage.input_tokens
                + consolidation_usage.input_tokens,
                output_tokens=analysis_usage.output_tokens
                + extraction_usage.output_tokens
                + specialized_usage.output_tokens
                + consolidation_usage.output_tokens,
                total_tokens=analysis_usage.total_tokens
                + extraction_usage.total_tokens
                + specialized_usage.total_tokens
                + consolidation_usage.total_tokens,
                estimated_cost_cents=analysis_usage.estimated_cost_cents
                + extraction_usage.estimated_cost_cents
                + specialized_usage.estimated_cost_cents
                + consolidation_usage.estimated_cost_cents,
            )

            # Step 8: Calculate confidence level
            # Use extraction confidence (analysis confidence is stored in manifest)
            avg_confidence = extraction_confidence

            if avg_confidence >= 0.9:
                confidence_level = "high"
            elif avg_confidence >= 0.7:
                confidence_level = "medium"
            else:
                confidence_level = "low"

            # Step 9: Upload result to S3
            logger.info(f"Job {job.job_id}: Uploading extracted markdown to S3")
            result_url = await self.storage.upload_result(
                job_id=job.job_id,
                content=full_markdown,
                format="md",
            )

            # Step 10: Update job status to completed
            processing_time = int(time.time() - start_time)

            # Parse heading tree from manifest to get section count and layout
            from ..shared.models.remediation import HeadingTree

            heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

            update_fields = {
                "markdown_url": result_url,
                "confidence_score": avg_confidence,
                "confidence_level": confidence_level,
                "processing_time_seconds": processing_time,
                "total_pages": conversion_result.total_pages,
                "llm_cost_cents": total_usage.estimated_cost_cents,
                "llm_input_tokens": total_usage.input_tokens,
                "llm_output_tokens": total_usage.output_tokens,
                "llm_total_tokens": total_usage.total_tokens,
                "extraction_method": "analysis_extraction",  # PRD-012/013/014/015 pipeline
                "extraction_model": extraction_agent.model_id,
                "layout_type": heading_tree.layout_type,
                "section_count": len(heading_tree.sections),
                "observation_count": len(all_observations),  # From specialized agents
                "proposal_count": proposal_count,  # From consolidation (PRD-015)
                "auto_proposal_count": auto_proposal_count,
                "manual_proposal_count": manual_proposal_count,
                "required_agents": ",".join(manifest.required_agents),
                "analysis_model": manifest.analysis_model,
            }

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "completed", **update_fields
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to completed",
            )

            logger.info(
                f"Job {job.job_id} completed in {processing_time}s "
                f"(confidence: {avg_confidence:.2f}, "
                f"est. cost: ${total_usage.estimated_cost_cents/100:.4f})"
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=result_url,
                confidence_score=avg_confidence,
                processing_time_seconds=processing_time,
                error_message=None,
            )

        except ValueError as e:
            # Handle validation errors (e.g., no pages provided)
            error_msg = f"Processing failed: {str(e)}"
            logger.error(f"Job {job.job_id} failed: {error_msg}")

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "failed", error=error_msg
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} to failed",
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=None,
                confidence_score=None,
                processing_time_seconds=int(time.time() - start_time),
                error_message=error_msg,
            )

        except Exception as e:
            error_msg = f"Processing failed: {str(e)}"
            logger.error(f"Job {job.job_id} failed: {error_msg}", exc_info=True)

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "failed", error=error_msg
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} to failed",
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=None,
                confidence_score=None,
                processing_time_seconds=int(time.time() - start_time),
                error_message=error_msg,
            )
    async def _route_to_correction_review(
        self,
        job: ProcessingQueuePayload,
        full_markdown: str,
        proposals: list[Proposal],
        observations: list[Observation],
        pages: list[Any],
        avg_confidence: float,
        analysis_usage: LLMUsage,
        extraction_usage: LLMUsage,
        specialized_usage: LLMUsage,
        consolidation_usage: LLMUsage,
        manifest: Any,
        start_time: float,
        auto_proposal_count: int,
        manual_proposal_count: int,
    ) -> ProcessingResult:
        """Route job to correction review workflow.

        This method:
        1. Saves original markdown with -original suffix
        2. Applies auto proposals to create corrected version (if ApplicationService available)
        3. Saves corrected markdown with -corrected suffix
        4. Uploads page images for visual comparison
        5. Transforms proposals to correction_results format for frontend
        6. Generates approval token via CorrectionApprovalService
        7. Calculates correction_expires_at (now + timeout hours)
        8. Adds job to timeout tracking sorted set
        9. Updates job status to "awaiting_correction_approval" with all metadata

        Args:
            job: Processing queue payload
            full_markdown: Original extracted markdown
            proposals: List of all proposals (auto + manual)
            observations: List of all observations
            pages: Page images and metadata
            avg_confidence: Extraction confidence score
            analysis_usage: Analysis phase LLM usage
            extraction_usage: Extraction phase LLM usage
            specialized_usage: Specialized agents LLM usage
            consolidation_usage: Consolidation phase LLM usage
            manifest: Analysis manifest
            start_time: Processing start time
            auto_proposal_count: Count of auto-route proposals
            manual_proposal_count: Count of manual-route proposals

        Returns:
            ProcessingResult with awaiting_correction_approval status
        """
        logger.info(f"Job {job.job_id}: Preparing correction review workflow")

        # Step 1: Save original markdown with -original suffix
        await self.storage.upload_result(
            job_id=job.job_id,
            content=full_markdown,
            format="md",
            suffix="original",
        )
        original_key = f"{job.job_id}-original.md"
        logger.info(f"Job {job.job_id}: Saved original markdown to {original_key}")

        # Step 2: Apply auto proposals to create corrected version
        corrected_markdown = full_markdown
        if self.application and auto_proposal_count > 0:
            logger.info(
                f"Job {job.job_id}: Applying {auto_proposal_count} auto proposals"
            )
            # Filter to auto-route proposals and mark them as approved
            auto_proposals = [p for p in proposals if p.route == "auto"]
            for p in auto_proposals:
                p.status = "approved"

            # Save proposals so ApplicationService can load them
            await self.remediation_storage.save_proposals(job.job_id, proposals)

            # Apply approved proposals
            from ..services.application_service import ApplicationService
            app_service = ApplicationService(
                remediation_storage=self.remediation_storage,
                storage=self.storage,
                job_service=self.job,
            )

            # Apply proposals to markdown
            for proposal in auto_proposals:
                result = app_service._apply_single_proposal(corrected_markdown, proposal)
                if result["success"]:
                    corrected_markdown = result["new_markdown"]
                    logger.debug(
                        f"Applied proposal {proposal.id} using {result['method']} method"
                    )
                else:
                    logger.warning(
                        f"Failed to apply auto proposal {proposal.id}: {result.get('error')}"
                    )

        # Step 3: Save corrected markdown with -corrected suffix
        await self.storage.upload_result(
            job_id=job.job_id,
            content=corrected_markdown,
            format="md",
            suffix="corrected",
        )
        corrected_key = f"{job.job_id}-corrected.md"
        logger.info(f"Job {job.job_id}: Saved corrected markdown to {corrected_key}")

        # Step 4: Upload page images for visual comparison
        page_image_urls = []
        for page in pages:
            if page.image_base64:
                # Decode base64 image and upload to S3
                image_bytes = base64.b64decode(page.image_base64)
                image_url = await self.storage.upload_page_image(
                    job_id=job.job_id,
                    page_num=page.page_num,
                    image_data=image_bytes,
                )
                page_image_urls.append(image_url)

        logger.info(f"Job {job.job_id}: Uploaded {len(page_image_urls)} page images")

        # Step 5: Transform ALL proposals to correction_results format for frontend
        correction_results = self._proposals_to_correction_results(
            proposals=proposals,  # Include all proposals (auto + manual)
            observations=observations,
        )

        # Step 6: Generate approval token (not currently used, but generated for future use)
        if self.correction_approval:
            _ = await self.correction_approval.generate_correction_approval_token(
                job.job_id
            )

        # Step 7: Calculate correction_expires_at
        correction_expires_at = datetime.now(UTC) + timedelta(
            hours=settings.correction_approval_timeout_hours
        )

        # Step 8: Add job to timeout tracking sorted set
        timeout_key = "eq-pdf:timeouts:correction-approval"
        await self.redis.zadd(
            timeout_key,
            {job.job_id: correction_expires_at.timestamp()}
        )
        logger.info(
            f"Job {job.job_id}: Added to timeout tracking "
            f"(expires at {correction_expires_at.isoformat()})"
        )

        # Step 9: Update job status to awaiting_correction_approval
        processing_time = int(time.time() - start_time)

        # Combine usage from all phases
        total_usage = LLMUsage(
            input_tokens=analysis_usage.input_tokens
            + extraction_usage.input_tokens
            + specialized_usage.input_tokens
            + consolidation_usage.input_tokens,
            output_tokens=analysis_usage.output_tokens
            + extraction_usage.output_tokens
            + specialized_usage.output_tokens
            + consolidation_usage.output_tokens,
            total_tokens=analysis_usage.total_tokens
            + extraction_usage.total_tokens
            + specialized_usage.total_tokens
            + consolidation_usage.total_tokens,
            estimated_cost_cents=analysis_usage.estimated_cost_cents
            + extraction_usage.estimated_cost_cents
            + specialized_usage.estimated_cost_cents
            + consolidation_usage.estimated_cost_cents,
        )

        # Parse heading tree from manifest
        from ..shared.models.remediation import HeadingTree
        heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

        # Calculate confidence level
        if avg_confidence >= 0.9:
            confidence_level = "high"
        elif avg_confidence >= 0.7:
            confidence_level = "medium"
        else:
            confidence_level = "low"

        update_fields = {
            "original_markdown_key": original_key,
            "corrected_markdown_key": corrected_key,
            "page_image_urls": ",".join(page_image_urls),
            "correction_results": correction_results,
            "correction_expires_at": correction_expires_at.isoformat(),
            "confidence_score": avg_confidence,
            "confidence_level": confidence_level,
            "processing_time_seconds": processing_time,
            "total_pages": len(pages),
            "llm_cost_cents": total_usage.estimated_cost_cents,
            "llm_input_tokens": total_usage.input_tokens,
            "llm_output_tokens": total_usage.output_tokens,
            "llm_total_tokens": total_usage.total_tokens,
            "extraction_method": "analysis_extraction",
            "extraction_model": get_model_id(ModelTier.EFFICIENT),
            "layout_type": heading_tree.layout_type,
            "section_count": len(heading_tree.sections),
            "observation_count": len(observations),
            "proposal_count": len(proposals),
            "auto_proposal_count": auto_proposal_count,
            "manual_proposal_count": manual_proposal_count,
            "required_agents": ",".join(manifest.required_agents),
            "analysis_model": manifest.analysis_model,
        }

        await retry_with_backoff(
            lambda: self.job.update_job_status(
                job.job_id, STATUS_AWAITING_CORRECTION_APPROVAL, **update_fields
            ),
            max_attempts=3,
            operation_name=f"Update job {job.job_id} status to awaiting_correction_approval",
        )

        logger.info(
            f"Job {job.job_id} routed to correction review "
            f"(processing: {processing_time}s, confidence: {avg_confidence:.2f})"
        )

        return ProcessingResult(
            job_id=job.job_id,
            markdown_url=corrected_key,
            confidence_score=avg_confidence,
            processing_time_seconds=processing_time,
            error_message=None,
        )

    def _proposals_to_correction_results(
        self,
        proposals: list[Proposal],
        observations: list[Observation]
    ) -> list[dict[str, Any]]:
        """Transform all proposals to correction_results format for frontend.

        Args:
            proposals: List of all proposals (auto + manual)
            observations: List of all observations

        Returns:
            List of correction results grouped by page, with is_auto_applied flag
        """
        from collections import defaultdict

        obs_map = {obs.id: obs for obs in observations}
        page_corrections: dict[int, list[dict]] = defaultdict(list)

        for proposal in proposals:
            page = proposal.page_nums[0] if proposal.page_nums else 1

            # Calculate confidence from resolved observations
            resolved_confidences = [
                obs_map[obs_id].confidence
                for obs_id in proposal.resolves
                if obs_id in obs_map
            ]
            avg_confidence = (
                sum(resolved_confidences) / len(resolved_confidences)
                if resolved_confidences else 0.8
            )

            page_corrections[page].append({
                "type": self._infer_correction_type(proposal, obs_map),
                "original": proposal.diff.search[:200],
                "corrected": proposal.diff.replace[:200],
                "confidence": avg_confidence,
                "explanation": proposal.justification,
                "is_auto_applied": proposal.route == "auto",
            })

        return [
            {"page": page, "corrections": corrections}
            for page, corrections in sorted(page_corrections.items())
        ]

    def _infer_correction_type(
        self,
        proposal: Proposal,
        obs_map: dict[str, Observation]
    ) -> str:
        """Infer correction type from proposal's resolved observations.

        Args:
            proposal: Proposal to infer type for
            obs_map: Map of observation IDs to observations

        Returns:
            Correction type string
        """
        agent_to_type = {
            "figures": "alt_text",
            "tables": "table_format",
            "structure": "heading_level",
            "typography": "emphasis",
            "analysis": "other",
        }
        for obs_id in proposal.resolves:
            if obs_id in obs_map:
                agent = obs_map[obs_id].agent
                if agent in agent_to_type:
                    return agent_to_type[agent]
        return "other"
