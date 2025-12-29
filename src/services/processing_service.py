"""Main processing service orchestrating PDF conversion and AI enhancement.

The processing pipeline consists of:
1. Analyze Phase (Haiku) - Chained analysis: layout → doctype → headings/features/summary
2. Extract Phase (Haiku) - Guided markdown extraction
3. Refine Phase:
   - Phase 3a: Structure Loop - Validation-driven lint/OCR/heading fixes
   - Phase 3b: Specialized agents (figures, tables, structure, typography)
   (Agents produce Observations, AutoCorrections, and ReviewItems)
4. Assemble Phase - Apply auto-corrections and prepare review items
"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from src.services.debug_bundle_service import DebugBundleService

from ..agents.chained_analysis import analyze_document
from ..agents.chained_structure import analyze_structure as chained_structure
from ..agents.extraction import extract_with_validation
from ..agents.factory import aggregate_usage
from ..agents.figures import FiguresAgent
from ..agents.model_tiers import ModelTier, get_model_id

# PRD-024: TablesAgent with AgentResult output
from ..agents.tables import TablesAgent

# PRD-025: Enhanced typography agent with AgentResult output
from ..agents.typography import TypographyAgent

# Final verification agent - last line of defense
from ..agents.verification import verify_final_output
from ..config import settings
from ..services.application_service import ApplicationService

# PRD-026: Assembly Service for Phase 4 (Assemble)
from ..services.assembly_service import AssemblyService
from ..services.correction_approval_service import CorrectionApprovalService
from ..services.document_context_service import DocumentContextService
from ..services.job_service import JobService
from ..services.pdf_converter import PDFConverter
from ..services.queue_service import QueueService
from ..services.remediation_storage_service import RemediationStorageService
from ..services.storage_service import StorageService
from ..services.structure_loop import StructureLoop
from ..shared.constants.statuses import (
    STATUS_AWAITING_CORRECTION_APPROVAL,
    STATUS_NEEDS_REVIEW,
)
from ..shared.models.agent_trace import AgentResult
from ..shared.models.observation import Observation
from ..shared.models.processing import LLMUsage, ProcessingResult
from ..shared.models.processing_result import (
    ProcessingResult as FullProcessingResult,
)
from ..shared.models.queue import ProcessingQueuePayload
from ..shared.models.remediation import HeadingTree
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
        self.remediation_storage = remediation_storage_service or RemediationStorageService(storage_service)
        self.correction_approval = correction_approval_service
        self.application = application_service

        self._tracer = trace.get_tracer("equalify.processing")
        self._debug_service: DebugBundleService | None = None
        logger.info("Processing service initialized")

    async def _get_debug_service(self, job_id: str) -> "DebugBundleService | None":
        """Get debug service if debug bundle was requested for this job.

        Lazily initializes and caches the debug service.

        Args:
            job_id: Job identifier to check for debug flag

        Returns:
            DebugBundleService if debug enabled, None otherwise
        """
        # Check if debug bundle was requested
        job_data = await self.job.get_job(job_id)
        if not job_data or job_data.get("debug_bundle_requested") != "true":
            return None

        # Lazily create debug service
        if self._debug_service is None:
            from src.services.debug_bundle_service import DebugBundleService

            self._debug_service = DebugBundleService(self.storage)

        return self._debug_service

    async def _save_debug_artifact(
        self,
        job_id: str,
        phase: str,
        agent_name: str,
        output_data: Any,
        usage: LLMUsage | None = None,
        input_summary: str = "",
    ) -> None:
        """Save debug artifact for a phase if debug mode is enabled.

        This is a convenience method for saving phase outputs without
        needing to check debug status everywhere.

        Args:
            job_id: Job identifier
            phase: Pipeline phase name
            agent_name: Name of agent/step that produced the output
            output_data: Output data to save (will be JSON serialized)
            usage: Optional LLM usage statistics
            input_summary: Optional description of input
        """
        debug_service = await self._get_debug_service(job_id)
        if not debug_service:
            return

        try:
            # Serialize output
            if hasattr(output_data, "model_dump_json"):
                output_str = output_data.model_dump_json(indent=2)
            elif hasattr(output_data, "model_dump"):
                output_str = json.dumps(output_data.model_dump(), indent=2, default=str)
            else:
                output_str = json.dumps(output_data, indent=2, default=str)

            tokens = {}
            cost = 0.0
            if usage:
                tokens = {
                    "input": usage.input_tokens,
                    "output": usage.output_tokens,
                    "total": usage.total_tokens,
                }
                cost = usage.estimated_cost_cents

            await debug_service.save_artifact_from_agent_run(
                job_id=job_id,
                phase=phase,
                agent_name=agent_name,
                input_data=input_summary or f"Phase {phase} input",
                prompt=f"[Phase {phase}] See OpenTelemetry spans for full prompt details",
                response_raw=output_str,
                output_parsed=output_data,
                tokens=tokens,
                cost_cents=cost,
                model_id="aggregate",
            )
        except Exception as e:
            logger.warning(f"Failed to save debug artifact for {agent_name}: {e}")

    async def process_document(
        self,
        job: ProcessingQueuePayload,
    ) -> ProcessingResult:
        """Process PDF using 4-phase pipeline: Analyze → Extract → Refine → Assemble.

        The processing pipeline:
        1. Analyze Phase (Haiku) - Deep document analysis, manifest generation
        2. Extract Phase (Haiku) - Guided markdown extraction (via ExtractionAgent)
        3. Refine Phase (Sonnet) - Specialized agents generate AutoCorrections/ReviewItems
        4. Assemble Phase - Apply auto-corrections and prepare for review

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
                lambda: self.job.update_job_status(job.job_id, "processing", substatus="analyzing"),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to processing/analyzing",
            )

            # Step 2: Download PDF from S3
            logger.info(f"Downloading PDF from s3://{settings.s3_temp_bucket}/{job.s3_key}")
            pdf_content = await self.storage.download_temp_file(s3_key=job.s3_key)
            logger.info(f"Downloaded {len(pdf_content)} bytes")

            # Step 3: Convert PDF with Docling (we need page images)
            logger.info("Converting PDF with Docling for page images...")
            conversion_result = await self.pdf_converter.convert_with_page_images(pdf_content)

            if not conversion_result.has_page_images:
                raise RuntimeError("Docling failed to generate page images. Cannot proceed without visual content.")

            logger.info(f"PDF converted: {conversion_result.total_pages} pages with images")

            pages = conversion_result.pages

            # Step 4: Analysis Phase (Chained Analysis)
            # Focused prompts: layout → doctype → headings/features in parallel
            logger.info(f"Job {job.job_id}: Starting chained analysis phase (Haiku)...")

            with self._tracer.start_as_current_span("phase.analysis") as span:
                span.set_attribute("job_id", job.job_id)
                span.set_attribute("phase.number", 1)
                span.set_attribute("total_pages", conversion_result.total_pages)
                span.set_attribute("analysis_mode", "chained")

                manifest, initial_observations, analysis_usage = await analyze_document(
                    pages=pages,
                    job_id=job.job_id,
                    parallel=True,
                )

                span.set_attribute("required_agents", str(manifest.required_agents))
                span.set_attribute("initial_observations", len(initial_observations))
                span.set_attribute("tokens.input", analysis_usage.input_tokens)
                span.set_attribute("tokens.output", analysis_usage.output_tokens)
                span.set_attribute("cost.cents", analysis_usage.estimated_cost_cents)

            # Save manifest and initial observations to S3
            await self.remediation_storage.save_manifest(job.job_id, manifest)

            # Save debug artifact for analysis phase
            await self._save_debug_artifact(
                job_id=job.job_id,
                phase="phase_1_analyze",
                agent_name="chained_analysis",
                output_data=manifest,
                usage=analysis_usage,
                input_summary=f"PDF with {conversion_result.total_pages} pages",
            )

            if initial_observations:
                # Convert list[str] to list[Observation] for storage
                # initial_observations from analyze_document is currently list[str]
                # but we'll keep it empty for now since specialized agents generate observations
                pass

            logger.info(
                f"Job {job.job_id}: Analysis complete - "
                f"{len(manifest.required_agents)} agents needed, "
                f"{len(initial_observations)} initial observations, "
                f"analysis cost: ${analysis_usage.estimated_cost_cents / 100:.4f}"
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

            # Step 5: Extraction Phase (Haiku) with validation-driven correction loop
            # Uses plain text output and pure Python validation for reliability
            logger.info(f"Job {job.job_id}: Starting extraction phase (Haiku)...")

            with self._tracer.start_as_current_span("phase.extraction") as span:
                span.set_attribute("job_id", job.job_id)
                span.set_attribute("phase.number", 2)
                span.set_attribute("document_title", manifest.document_title)

                # Extract markdown with validation loop (plain text output)
                extraction_result, extraction_usage = await extract_with_validation(
                    pages=pages,
                    manifest=manifest,
                    job_id=job.job_id,
                )

                # Get values from ExtractionResult
                full_markdown = extraction_result.markdown
                extraction_confidence = extraction_result.metrics.confidence

                span.set_attribute("markdown_length", len(full_markdown))
                span.set_attribute("confidence", extraction_confidence)
                span.set_attribute("is_valid", extraction_result.metrics.is_valid)
                span.set_attribute("attempt_count", extraction_result.attempt_count)
                span.set_attribute("correction_applied", extraction_result.correction_applied)
                span.set_attribute("tokens.input", extraction_usage.input_tokens)
                span.set_attribute("tokens.output", extraction_usage.output_tokens)
                span.set_attribute("cost.cents", extraction_usage.estimated_cost_cents)

            # Save v0 markdown (original extraction, never modified)
            await self.storage.upload_result(
                job_id=job.job_id,
                content=full_markdown,
                format="md",
                suffix="v0",
            )

            # Save debug artifact for extraction phase
            await self._save_debug_artifact(
                job_id=job.job_id,
                phase="phase_2_extract",
                agent_name="extraction",
                output_data={
                    "markdown_length": len(full_markdown),
                    "confidence": extraction_confidence,
                    "is_valid": extraction_result.metrics.is_valid,
                    "attempt_count": extraction_result.attempt_count,
                    "correction_applied": extraction_result.correction_applied,
                    "pages_found": extraction_result.metrics.pages_found,
                    "markdown_preview": full_markdown[:2000] + "..." if len(full_markdown) > 2000 else full_markdown,
                },
                usage=extraction_usage,
                input_summary=f"Manifest-guided extraction from {manifest.total_pages} pages",
            )

            logger.info(
                f"Job {job.job_id}: Extraction complete - "
                f"{len(full_markdown)} chars, "
                f"confidence: {extraction_confidence:.2f}, "
                f"valid: {extraction_result.metrics.is_valid}, "
                f"attempts: {extraction_result.attempt_count}, "
                f"pages: {len(extraction_result.metrics.pages_found)}/{manifest.total_pages}, "
                f"extraction cost: ${extraction_usage.estimated_cost_cents / 100:.4f}"
            )

            # Step 5.5: Phase 3a - Structure Verification Loop
            # Ensures structurally correct markdown before specialized agents
            # Uses validation-driven loop: lint → OCR check → mdformat → LLM fix
            logger.info(f"Job {job.job_id}: Starting structure verification loop (Phase 3a)...")

            structure_loop_usage = LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0)

            with self._tracer.start_as_current_span("phase.structure_loop") as span:
                span.set_attribute("job_id", job.job_id)
                span.set_attribute("phase.number", "3a")

                # Update substatus to "verifying_structure"
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id,
                        "processing",
                        substatus="verifying_structure",
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} substatus to verifying_structure",
                )

                structure_loop = StructureLoop()
                structure_result = await structure_loop.run(
                    markdown=full_markdown,
                    pages=pages,
                    manifest=manifest,
                    job_id=job.job_id,
                )

                # Use structurally-correct markdown for specialized agents
                full_markdown = structure_result.markdown
                structure_trace = structure_result.trace

                # Track usage from structure loop LLM calls
                structure_loop_usage = LLMUsage(
                    input_tokens=0,  # Tracked within structure loop
                    output_tokens=0,
                    total_tokens=0,
                    estimated_cost_cents=structure_trace.cost_cents,
                )

                span.set_attribute("iterations", structure_trace.iterations)
                span.set_attribute("lint_issues_found", structure_trace.lint_issues_found)
                span.set_attribute("lint_issues_fixed", structure_trace.lint_issues_fixed)
                span.set_attribute("ocr_suggestions", structure_trace.ocr_suggestions_processed)
                span.set_attribute("final_lint_clean", structure_trace.final_lint_clean)
                span.set_attribute("time_seconds", structure_trace.time_seconds)
                span.set_attribute("cost.cents", structure_trace.cost_cents)

            logger.info(
                f"Job {job.job_id}: Structure loop complete - "
                f"{structure_trace.iterations} iterations, "
                f"{structure_trace.lint_issues_fixed}/{structure_trace.lint_issues_found} lint issues fixed, "
                f"{structure_trace.ocr_suggestions_processed} OCR suggestions processed, "
                f"clean: {structure_trace.final_lint_clean}, "
                f"cost: ${structure_trace.cost_cents / 100:.4f}"
            )

            # Save debug artifact for structure loop
            await self._save_debug_artifact(
                job_id=job.job_id,
                phase="phase_3a_structure_loop",
                agent_name="structure_loop",
                output_data={
                    "iterations": structure_trace.iterations,
                    "lint_issues_found": structure_trace.lint_issues_found,
                    "lint_issues_fixed": structure_trace.lint_issues_fixed,
                    "ocr_suggestions_processed": structure_trace.ocr_suggestions_processed,
                    "final_lint_clean": structure_trace.final_lint_clean,
                    "time_seconds": structure_trace.time_seconds,
                },
                usage=structure_loop_usage,
                input_summary="Validation-driven structure correction loop",
            )

            # Step 6: Specialized Agents Phase (PRD-023-025)
            # Run specialized agents based on manifest.required_agents
            # All agents return AgentResult for unified processing
            agent_results: list[AgentResult] = []
            specialized_usages: list[LLMUsage] = []

            if manifest.required_agents:
                logger.info(f"Job {job.job_id}: Starting specialized analysis phase (Phase 3b)...")

                with self._tracer.start_as_current_span("phase.specialized_agents") as span:
                    span.set_attribute("job_id", job.job_id)
                    span.set_attribute("phase.number", "3b")
                    span.set_attribute("required_agents", str(manifest.required_agents))

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

                    # PRD-023: FiguresAgent with AgentResult output
                    if "figures" in manifest.required_agents:
                        with self._tracer.start_as_current_span("agent.figures"):
                            figures_agent = FiguresAgent()
                            figures_result = await figures_agent.process(
                                markdown=full_markdown,
                                pages=pages,
                                manifest=manifest,
                                job_id=job.job_id,
                            )
                            agent_results.append(figures_result)
                            figures_usage = LLMUsage(
                                input_tokens=0,
                                output_tokens=0,
                                total_tokens=0,
                                estimated_cost_cents=figures_result.cost_cents,
                            )
                            specialized_usages.append(figures_usage)
                            logger.info(
                                f"Job {job.job_id}: FiguresAgent - "
                                f"{len(figures_result.observations)} observations, "
                                f"{len(figures_result.auto_corrections)} auto-corrections, "
                                f"{len(figures_result.review_items)} review items, "
                                f"cost: ${figures_result.cost_cents / 100:.4f}"
                            )

                    # PRD-024: TablesAgent with AgentResult output
                    if "tables" in manifest.required_agents:
                        with self._tracer.start_as_current_span("agent.tables"):
                            tables_agent = TablesAgent()
                            tables_result = await tables_agent.process(
                                markdown=full_markdown,
                                pages=pages,
                                manifest=manifest,
                                job_id=job.job_id,
                            )
                            agent_results.append(tables_result)
                            tables_usage = LLMUsage(
                                input_tokens=0,
                                output_tokens=0,
                                total_tokens=0,
                                estimated_cost_cents=tables_result.cost_cents,
                            )
                            specialized_usages.append(tables_usage)
                            logger.info(
                                f"Job {job.job_id}: TablesAgent - "
                                f"{len(tables_result.observations)} observations, "
                                f"{len(tables_result.auto_corrections)} auto-corrections, "
                                f"{len(tables_result.review_items)} review items, "
                                f"cost: ${tables_result.cost_cents / 100:.4f}"
                            )

                    # Structure agent (still uses legacy chained pattern)
                    # TODO: Migrate to AgentResult output in future PRD
                    if "structure" in manifest.required_agents:
                        with self._tracer.start_as_current_span("agent.chained_structure"):
                            structure_obs, structure_usage = await chained_structure(
                                pages=pages,
                                manifest=manifest,
                                markdown=full_markdown,
                                job_id=job.job_id,
                            )
                            # Convert legacy output to AgentResult for assembly
                            structure_agent_result = AgentResult(
                                agent_name="structure",
                                observations=structure_obs,
                                auto_corrections=[],
                                review_items=[],
                                reasoning_summary=f"Found {len(structure_obs)} structure issues",
                                confidence=0.9,
                                cost_cents=structure_usage.estimated_cost_cents,
                                time_seconds=0.0,
                            )
                            agent_results.append(structure_agent_result)
                            specialized_usages.append(structure_usage)
                            logger.info(
                                f"Job {job.job_id}: Chained structure - "
                                f"{len(structure_obs)} observations, "
                                f"cost: ${structure_usage.estimated_cost_cents / 100:.4f}"
                            )

                    # PRD-025: TypographyAgent with AgentResult output
                    if "typography" in manifest.required_agents:
                        with self._tracer.start_as_current_span("agent.typography"):
                            typography_agent = TypographyAgent()
                            typography_result = await typography_agent.process(
                                markdown=full_markdown,
                                pages=pages,
                                manifest=manifest,
                                ocr_suggestions=[],  # TODO: Pass from structure loop
                                job_id=job.job_id,
                            )
                            agent_results.append(typography_result)
                            typography_usage = LLMUsage(
                                input_tokens=0,
                                output_tokens=0,
                                total_tokens=0,
                                estimated_cost_cents=typography_result.cost_cents,
                            )
                            specialized_usages.append(typography_usage)
                            logger.info(
                                f"Job {job.job_id}: TypographyAgent - "
                                f"{len(typography_result.observations)} observations, "
                                f"{len(typography_result.auto_corrections)} auto-corrections, "
                                f"{len(typography_result.review_items)} review items, "
                                f"cost: ${typography_result.cost_cents / 100:.4f}"
                            )

                    # Aggregate all specialized usage
                    specialized_usage = aggregate_usage(specialized_usages)

                    # Collect all observations for span attribute
                    all_observations = []
                    for result in agent_results:
                        all_observations.extend(result.observations)

                    span.set_attribute("observations_found", len(all_observations))
                    span.set_attribute("tokens.input", specialized_usage.input_tokens)
                    span.set_attribute("tokens.output", specialized_usage.output_tokens)
                    span.set_attribute("cost.cents", specialized_usage.estimated_cost_cents)

                logger.info(
                    f"Job {job.job_id}: Specialized analysis complete - "
                    f"{len(all_observations)} observations found, "
                    f"specialized cost: ${specialized_usage.estimated_cost_cents / 100:.4f}"
                )

                # Save observations to S3
                if all_observations:
                    await self.remediation_storage.save_observations(job.job_id, all_observations)

                # Save debug artifacts for each agent result
                for result in agent_results:
                    await self._save_debug_artifact(
                        job_id=job.job_id,
                        phase="phase_3b_specialized",
                        agent_name=result.agent_name,
                        output_data={
                            "observations": len(result.observations),
                            "auto_corrections": len(result.auto_corrections),
                            "review_items": len(result.review_items),
                            "confidence": result.confidence,
                            "reasoning_summary": result.reasoning_summary,
                        },
                        usage=LLMUsage(
                            input_tokens=0,
                            output_tokens=0,
                            total_tokens=0,
                            estimated_cost_cents=result.cost_cents,
                        ),
                        input_summary=f"Specialized analysis by {result.agent_name}",
                    )
            else:
                logger.info(f"Job {job.job_id}: No specialized agents required, skipping Phase 3b")
                agent_results = []
                all_observations = []
                specialized_usage = LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0)

            # Step 7: Phase 4 - Assembly (PRD-026)
            # Apply auto_corrections, build ProcessingTrace, compute confidence
            logger.info(f"Job {job.job_id}: Starting assembly phase (Phase 4)...")

            with self._tracer.start_as_current_span("phase.assembly") as span:
                span.set_attribute("job_id", job.job_id)
                span.set_attribute("phase.number", 4)
                span.set_attribute("agent_results_count", len(agent_results))

                # Update substatus to "assembling"
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id,
                        "processing",
                        substatus="assembling",
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} substatus to assembling",
                )

                assembly_service = AssemblyService()
                assembly_result = assembly_service.assemble(
                    job_id=job.job_id,
                    markdown=full_markdown,
                    manifest=manifest,
                    extraction_confidence=extraction_confidence,
                    extraction_time=extraction_usage.estimated_cost_cents,  # Approximation
                    extraction_cost=extraction_usage.estimated_cost_cents,
                    extraction_iterations=extraction_result.attempt_count,
                    structure_trace=structure_trace,
                    agent_results=agent_results,
                )

                # Use assembled markdown with corrections applied
                final_markdown = assembly_result.markdown

                span.set_attribute("corrections_applied", assembly_result.processing_trace.auto_corrections_applied)
                span.set_attribute("review_items", assembly_result.review_checklist.total_items)
                span.set_attribute("final_confidence", assembly_result.confidence)
                span.set_attribute("status", assembly_result.status)

            logger.info(
                f"Job {job.job_id}: Assembly complete - "
                f"status: {assembly_result.status}, "
                f"corrections: {assembly_result.processing_trace.auto_corrections_applied}, "
                f"review_items: {assembly_result.review_checklist.total_items}, "
                f"confidence: {assembly_result.confidence:.2f}"
            )

            # Save debug artifact for assembly phase
            await self._save_debug_artifact(
                job_id=job.job_id,
                phase="phase_4_assemble",
                agent_name="assembly",
                output_data={
                    "status": assembly_result.status,
                    "confidence": assembly_result.confidence,
                    "auto_corrections_applied": assembly_result.processing_trace.auto_corrections_applied,
                    "review_items_count": assembly_result.review_checklist.total_items,
                    "review_items_by_category": {
                        cat: len(items) for cat, items in assembly_result.review_checklist.by_category.items()
                    }
                    if assembly_result.review_checklist.by_category
                    else {},
                    "final_markdown_length": len(assembly_result.markdown),
                },
                usage=None,  # No additional LLM cost in assembly
                input_summary="Assemble agent results into final output",
            )

            # Step 7b: Phase 5 - Final Verification
            # Compare assembled markdown against source images to catch remaining errors
            logger.info(f"Job {job.job_id}: Starting final verification phase (Phase 5)...")

            verification_usage = LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0)

            with self._tracer.start_as_current_span("phase.verification") as span:
                span.set_attribute("job_id", job.job_id)
                span.set_attribute("phase.number", 5)
                span.set_attribute("total_pages", len(pages))

                # Update substatus to "verifying"
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id,
                        "processing",
                        substatus="verifying",
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} substatus to verifying",
                )

                # Extract page images for verification
                page_images = [page.image_base64 for page in pages]

                verification_result, verification_usage = await verify_final_output(
                    markdown=final_markdown,
                    page_images=page_images,
                    job_id=job.job_id,
                )

                # Use verified markdown
                final_markdown = verification_result.corrected_markdown

                span.set_attribute("corrections_applied", verification_result.total_corrections_applied)
                span.set_attribute("corrections_failed", verification_result.total_corrections_failed)
                span.set_attribute("issues_found", verification_result.total_issues)
                span.set_attribute("all_accurate", verification_result.all_pages_accurate)
                span.set_attribute("cost.cents", verification_result.cost_cents)

            logger.info(
                f"Job {job.job_id}: Verification complete - "
                f"{verification_result.total_corrections_applied} corrections applied, "
                f"{verification_result.total_issues} issues found, "
                f"all_accurate={verification_result.all_pages_accurate}, "
                f"cost: ${verification_result.cost_cents / 100:.4f}"
            )

            # Save debug artifact for verification phase
            await self._save_debug_artifact(
                job_id=job.job_id,
                phase="phase_5_verify",
                agent_name="verification",
                output_data={
                    "corrections_applied": verification_result.total_corrections_applied,
                    "corrections_failed": verification_result.total_corrections_failed,
                    "issues_found": verification_result.total_issues,
                    "all_pages_accurate": verification_result.all_pages_accurate,
                    "page_results": [
                        {
                            "page_num": pr.page_num,
                            "is_accurate": pr.is_accurate,
                            "corrections_applied": pr.corrections_applied,
                            "issues": len(pr.issues),
                            "summary": pr.summary[:200] if pr.summary else "",
                        }
                        for pr in verification_result.page_results
                    ],
                },
                usage=verification_usage,
                input_summary="Final verification against source images",
            )

            # Store verification summary in job metadata for phases endpoint
            verification_summary = {
                "total_pages": len(pages),
                "corrections_applied": verification_result.total_corrections_applied,
                "corrections_failed": verification_result.total_corrections_failed,
                "issues_found": verification_result.total_issues,
                "all_pages_accurate": verification_result.all_pages_accurate,
                "cost_cents": verification_result.cost_cents,
                "page_results": [
                    {
                        "page_num": pr.page_num,
                        "is_accurate": pr.is_accurate,
                        "corrections_applied": pr.corrections_applied,
                        "issues_count": len(pr.issues),
                        "summary": pr.summary[:200] if pr.summary else "",
                    }
                    for pr in verification_result.page_results
                ],
            }
            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id,
                    "processing",
                    verification_summary=verification_summary,
                ),
                max_attempts=3,
                operation_name=f"Store verification summary for job {job.job_id}",
            )

            # Combine usage from all phases (needed for both review and completion paths)
            total_usage = LLMUsage(
                input_tokens=analysis_usage.input_tokens
                + extraction_usage.input_tokens
                + structure_loop_usage.input_tokens
                + specialized_usage.input_tokens
                + verification_usage.input_tokens,
                output_tokens=analysis_usage.output_tokens
                + extraction_usage.output_tokens
                + structure_loop_usage.output_tokens
                + specialized_usage.output_tokens
                + verification_usage.output_tokens,
                total_tokens=analysis_usage.total_tokens
                + extraction_usage.total_tokens
                + structure_loop_usage.total_tokens
                + specialized_usage.total_tokens
                + verification_usage.total_tokens,
                estimated_cost_cents=analysis_usage.estimated_cost_cents
                + extraction_usage.estimated_cost_cents
                + structure_loop_usage.estimated_cost_cents
                + specialized_usage.estimated_cost_cents
                + verification_usage.estimated_cost_cents,
            )

            # Step 8: Check for review items
            # Route to review checklist if there are review items or settings require it
            review_item_count = assembly_result.review_checklist.total_items

            should_route_to_review = review_item_count > 0 or settings.always_require_correction_review

            if should_route_to_review:
                # PRD-027: Use new review checklist workflow
                return await self._route_to_review(
                    job=job,
                    assembly_result=assembly_result,
                    total_usage=total_usage,
                    manifest=manifest,
                    start_time=start_time,
                    pages=pages,
                )

            # Step 9: Calculate confidence level using assembled confidence
            avg_confidence = assembly_result.confidence

            if avg_confidence >= settings.confidence_threshold_high:
                confidence_level = "high"
            elif avg_confidence >= settings.confidence_threshold_medium:
                confidence_level = "medium"
            else:
                confidence_level = "low"

            # Step 10: Upload result to S3
            logger.info(f"Job {job.job_id}: Uploading final markdown to S3")
            result_url = await self.storage.upload_result(
                job_id=job.job_id,
                content=final_markdown,
                format="md",
            )

            # Step 11: Update job status to completed
            processing_time = int(time.time() - start_time)

            # Parse heading tree from manifest to get section count and layout
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
                "extraction_method": "analysis_extraction",
                "extraction_model": get_model_id(ModelTier.EFFICIENT),
                "layout_type": heading_tree.layout_type,
                "section_count": len(heading_tree.sections),
                "observation_count": len(all_observations),  # From specialized agents
                "required_agents": ",".join(manifest.required_agents),
                "analysis_model": manifest.analysis_model,
            }

            await retry_with_backoff(
                lambda: self.job.update_job_status(job.job_id, "completed", **update_fields),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to completed",
            )

            logger.info(
                f"Job {job.job_id} completed in {processing_time}s "
                f"(confidence: {avg_confidence:.2f}, "
                f"est. cost: ${total_usage.estimated_cost_cents / 100:.4f})"
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
                lambda: self.job.update_job_status(job.job_id, "failed", error=error_msg),
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
                lambda: self.job.update_job_status(job.job_id, "failed", error=error_msg),
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
        observations: list[Observation],
        pages: list[Any],
        avg_confidence: float,
        analysis_usage: LLMUsage,
        extraction_usage: LLMUsage,
        structure_loop_usage: LLMUsage,
        specialized_usage: LLMUsage,
        manifest: Any,
        start_time: float,
    ) -> ProcessingResult:
        """Route job to correction review workflow.

        This method:
        1. Saves original markdown with -original suffix
        2. Uploads page images for visual comparison
        3. Transforms observations to review format for frontend
        4. Generates approval token via CorrectionApprovalService
        5. Calculates correction_expires_at (now + timeout hours)
        6. Adds job to timeout tracking sorted set
        7. Updates job status to "awaiting_correction_approval" with all metadata

        Args:
            job: Processing queue payload
            full_markdown: Original extracted markdown
            observations: List of all observations
            pages: Page images and metadata
            avg_confidence: Extraction confidence score
            analysis_usage: Analysis phase LLM usage
            extraction_usage: Extraction phase LLM usage
            structure_loop_usage: Structure loop (Phase 3a) LLM usage
            specialized_usage: Specialized agents LLM usage
            manifest: Analysis manifest
            start_time: Processing start time

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

        # Step 2: Upload page images for visual comparison
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

        # Step 3: Transform observations to review format for frontend
        review_items = self._observations_to_review_items(observations)

        # Step 4: Generate approval token (not currently used, but generated for future use)
        if self.correction_approval:
            _ = await self.correction_approval.generate_correction_approval_token(job.job_id)

        # Step 5: Calculate correction_expires_at
        correction_expires_at = datetime.now(UTC) + timedelta(hours=settings.correction_approval_timeout_hours)

        # Step 6: Add job to timeout tracking sorted set
        timeout_key = "eq-pdf:timeouts:correction-approval"
        await self.redis.zadd(timeout_key, {job.job_id: correction_expires_at.timestamp()})
        logger.info(f"Job {job.job_id}: Added to timeout tracking (expires at {correction_expires_at.isoformat()})")

        # Step 7: Update job status to awaiting_correction_approval
        processing_time = int(time.time() - start_time)

        # Combine usage from all phases
        total_usage = LLMUsage(
            input_tokens=analysis_usage.input_tokens
            + extraction_usage.input_tokens
            + structure_loop_usage.input_tokens
            + specialized_usage.input_tokens,
            output_tokens=analysis_usage.output_tokens
            + extraction_usage.output_tokens
            + structure_loop_usage.output_tokens
            + specialized_usage.output_tokens,
            total_tokens=analysis_usage.total_tokens
            + extraction_usage.total_tokens
            + structure_loop_usage.total_tokens
            + specialized_usage.total_tokens,
            estimated_cost_cents=analysis_usage.estimated_cost_cents
            + extraction_usage.estimated_cost_cents
            + structure_loop_usage.estimated_cost_cents
            + specialized_usage.estimated_cost_cents,
        )

        # Parse heading tree from manifest
        heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

        # Calculate confidence level
        if avg_confidence >= settings.confidence_threshold_high:
            confidence_level = "high"
        elif avg_confidence >= settings.confidence_threshold_medium:
            confidence_level = "medium"
        else:
            confidence_level = "low"

        update_fields = {
            "original_markdown_key": original_key,
            "corrected_markdown_key": original_key,  # Initially same as original, updated after corrections
            "page_image_urls": ",".join(page_image_urls),
            "review_items": review_items,
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
            "required_agents": ",".join(manifest.required_agents),
            "analysis_model": manifest.analysis_model,
        }

        await retry_with_backoff(
            lambda: self.job.update_job_status(job.job_id, STATUS_AWAITING_CORRECTION_APPROVAL, **update_fields),
            max_attempts=3,
            operation_name=f"Update job {job.job_id} status to awaiting_correction_approval",
        )

        logger.info(
            f"Job {job.job_id} routed to correction review "
            f"(processing: {processing_time}s, confidence: {avg_confidence:.2f})"
        )

        return ProcessingResult(
            job_id=job.job_id,
            markdown_url=original_key,
            confidence_score=avg_confidence,
            processing_time_seconds=processing_time,
            error_message=None,
        )

    def _observations_to_review_items(self, observations: list[Observation]) -> list[dict[str, Any]]:
        """Transform observations to review items format for frontend.

        Args:
            observations: List of all observations

        Returns:
            List of review items grouped by page
        """
        from collections import defaultdict

        page_items: dict[int, list[dict]] = defaultdict(list)

        for obs in observations:
            page = obs.location.page_num

            page_items[page].append(
                {
                    "id": obs.id,
                    "agent": obs.agent,
                    "type": self._agent_to_type(obs.agent),
                    "visual_description": obs.visual_description,
                    "markup_description": obs.markup_description,
                    "confidence": obs.confidence,
                    "severity": obs.severity,
                    "category": obs.category,
                }
            )

        return [{"page": page, "items": items} for page, items in sorted(page_items.items())]

    def _agent_to_type(self, agent: str) -> str:
        """Map agent name to correction type.

        Args:
            agent: Agent name

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
        return agent_to_type.get(agent, "other")

    async def _route_to_review(
        self,
        job: ProcessingQueuePayload,
        assembly_result: FullProcessingResult,
        total_usage: LLMUsage,
        manifest: Any,
        start_time: float,
        pages: list[Any],
    ) -> ProcessingResult:
        """Route job to new review checklist workflow (PRD-027).

        This method:
        1. Uploads page images to S3 for visual context in review UI
        2. Saves the full ProcessingResult (with review_checklist) to S3
        3. Updates job status to "needs_review" with metadata
        4. Returns simple ProcessingResult for API response

        Args:
            job: Processing queue payload
            assembly_result: Full ProcessingResult from AssemblyService
            total_usage: Combined LLM usage from all phases
            manifest: Analysis manifest
            start_time: Processing start time
            pages: Page images and metadata from PDF conversion

        Returns:
            Simple ProcessingResult indicating needs_review status
        """
        logger.info(
            f"Job {job.job_id}: Routing to review checklist "
            f"({assembly_result.review_checklist.total_items} review items)"
        )

        # Step 1: Upload page images to S3 for visual context
        page_image_urls: list[str] = []
        for page in pages:
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                image_url = await self.storage.upload_page_image(
                    job_id=job.job_id,
                    page_num=page.page_num,
                    image_data=image_bytes,
                )
                page_image_urls.append(image_url)
        logger.info(f"Job {job.job_id}: Uploaded {len(page_image_urls)} page images")

        # Step 2: Save full ProcessingResult to S3
        result_s3_key = await self.storage.save_processing_result(
            job_id=job.job_id,
            result=assembly_result,
        )
        logger.info(f"Job {job.job_id}: Saved ProcessingResult to {result_s3_key}")

        # Step 2: Update job status to needs_review
        processing_time = int(time.time() - start_time)

        # Parse heading tree from manifest
        heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

        # Calculate confidence level
        if assembly_result.confidence >= settings.confidence_threshold_high:
            confidence_level = "high"
        elif assembly_result.confidence >= settings.confidence_threshold_medium:
            confidence_level = "medium"
        else:
            confidence_level = "low"

        update_fields = {
            "processing_result_key": result_s3_key,
            "confidence_score": assembly_result.confidence,
            "confidence_level": confidence_level,
            "processing_time_seconds": processing_time,
            "total_pages": manifest.total_pages,
            "page_image_urls": ",".join(page_image_urls),
            "llm_cost_cents": total_usage.estimated_cost_cents,
            "llm_input_tokens": total_usage.input_tokens,
            "llm_output_tokens": total_usage.output_tokens,
            "llm_total_tokens": total_usage.total_tokens,
            "extraction_method": "analysis_extraction",
            "extraction_model": get_model_id(ModelTier.EFFICIENT),
            "layout_type": heading_tree.layout_type,
            "section_count": len(heading_tree.sections),
            "observation_count": assembly_result.processing_trace.total_observations,
            "review_item_count": assembly_result.review_checklist.total_items,
            "required_agents": ",".join(manifest.required_agents),
            "analysis_model": manifest.analysis_model,
        }

        await retry_with_backoff(
            lambda: self.job.update_job_status(job.job_id, STATUS_NEEDS_REVIEW, **update_fields),
            max_attempts=3,
            operation_name=f"Update job {job.job_id} status to needs_review",
        )

        logger.info(
            f"Job {job.job_id} routed to review checklist "
            f"(processing: {processing_time}s, confidence: {assembly_result.confidence:.2f}, "
            f"review_items: {assembly_result.review_checklist.total_items})"
        )

        return ProcessingResult(
            job_id=job.job_id,
            markdown_url=result_s3_key,
            confidence_score=assembly_result.confidence,
            processing_time_seconds=processing_time,
            error_message=None,
        )
