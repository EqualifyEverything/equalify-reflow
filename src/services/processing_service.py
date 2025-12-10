"""Main processing service orchestrating PDF conversion and AI enhancement.

The processing pipeline consists of:
1. Analysis Phase (Sonnet) - Deep document analysis, manifest generation (PRD-012)
2. Extraction Phase (Haiku) - Guided markdown extraction (PRD-013)
3. Specialized Agents (Sonnet) - Figures, tables, structure, typography (PRD-014)
4. Consolidation - Observations to proposals (PRD-015, future)
"""

import logging
import time
from typing import Any

from ..agents.agent_router import AgentRouter
from ..agents.analysis_agent import AnalysisAgent
from ..agents.extraction_agent import ExtractionAgent
from ..agents.figures_agent import FiguresAgent
from ..agents.structure_agent import StructureAgent
from ..agents.tables_agent import TablesAgent
from ..agents.typography_agent import TypographyAgent
from ..config import settings
from ..services.document_context_service import DocumentContextService
from ..services.job_service import JobService
from ..services.pdf_converter import PDFConverter
from ..services.queue_service import QueueService
from ..services.remediation_storage_service import RemediationStorageService
from ..services.storage_service import StorageService
from ..shared.models.processing import LLMUsage, ProcessingResult
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
            specialized_observations = []
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
                specialized_observations = await router.run_required_agents(
                    manifest=manifest,
                    pages=pages,
                    markdown=full_markdown,
                    job_id=job.job_id,
                )

                # Note: Individual agent usage is logged per-agent; we track total here
                # In future, we could aggregate usage from each agent for detailed tracking
                logger.info(
                    f"Job {job.job_id}: Specialized analysis complete - "
                    f"{len(specialized_observations)} additional observations found"
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

            # Combine usage from all phases
            total_usage = LLMUsage(
                input_tokens=analysis_usage.input_tokens
                + extraction_usage.input_tokens
                + specialized_usage.input_tokens,
                output_tokens=analysis_usage.output_tokens
                + extraction_usage.output_tokens
                + specialized_usage.output_tokens,
                total_tokens=analysis_usage.total_tokens
                + extraction_usage.total_tokens
                + specialized_usage.total_tokens,
                estimated_cost_cents=analysis_usage.estimated_cost_cents
                + extraction_usage.estimated_cost_cents
                + specialized_usage.estimated_cost_cents,
            )

            # Step 7: Calculate confidence level
            # Use extraction confidence (analysis confidence is stored in manifest)
            avg_confidence = extraction_confidence

            if avg_confidence >= 0.9:
                confidence_level = "high"
            elif avg_confidence >= 0.7:
                confidence_level = "medium"
            else:
                confidence_level = "low"

            # Step 7: Upload result to S3
            logger.info(f"Job {job.job_id}: Uploading extracted markdown to S3")
            result_url = await self.storage.upload_result(
                job_id=job.job_id,
                content=full_markdown,
                format="md",
            )

            # Step 8: Update job status to completed
            processing_time = int(time.time() - start_time)

            # Parse heading tree from manifest to get section count and layout
            from ..agents.full_document_agent import HeadingTree

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
                "extraction_method": "analysis_extraction",  # PRD-012/013/014 pipeline
                "extraction_model": extraction_agent.model_id,
                "layout_type": heading_tree.layout_type,
                "section_count": len(heading_tree.sections),
                "observation_count": len(all_observations),  # Includes specialized agent findings
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
