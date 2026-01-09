"""Assembly Service for Phase 4: Assemble.

This service combines all agent outputs into the final ProcessingResult.
It applies auto_corrections, builds the processing trace, and computes
final confidence scores.

This is pure Python assembly with no LLM cost.

Key responsibilities:
1. Apply all auto_corrections to markdown
2. Replace placeholders with enhanced content
3. Run final validation
4. Build ProcessingTrace from all AgentTraces
5. Build ReviewChecklist from all review_items
6. Compute final confidence score
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.shared.models.agent_trace import AgentResult, AgentTrace
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation
from src.shared.models.processing_result import (
    AnalysisSummary,
    ExtractionSummary,
    ProcessingResult,
    ProcessingTrace,
    StructureSummary,
)
from src.shared.models.review_checklist import ReviewChecklist, ReviewItem

if TYPE_CHECKING:
    from src.services.structure_loop import StructureTrace
    from src.shared.models.remediation import DocumentManifest

logger = logging.getLogger(__name__)


class AssemblyService:
    """Phase 4: Combine all agent outputs into final result.

    The Assembly Service is the final phase of the 4-phase pipeline.
    It takes outputs from all previous phases and assembles them into
    a ProcessingResult ready for API exposure.

    Example:
        >>> service = AssemblyService()
        >>> result = service.assemble(
        ...     job_id="job-123",
        ...     markdown=refined_markdown,
        ...     manifest=manifest,
        ...     extraction_confidence=0.92,
        ...     extraction_time=45.2,
        ...     extraction_cost=8.4,
        ...     extraction_iterations=1,
        ...     structure_trace=structure_trace,
        ...     agent_results=[figures_result, tables_result],
        ... )
        >>> print(result.status)
        'completed'
    """

    def assemble(
        self,
        job_id: str,
        markdown: str,
        manifest: DocumentManifest,
        extraction_confidence: float,
        extraction_time: float,
        extraction_cost: float,
        extraction_iterations: int,
        structure_trace: StructureTrace,
        agent_results: list[AgentResult],
    ) -> ProcessingResult:
        """Assemble final processing result from all phases.

        Args:
            job_id: The job identifier
            markdown: Markdown from Phase 3 (Refine)
            manifest: Document manifest from Phase 1 (Analyze)
            extraction_confidence: Confidence from extraction phase
            extraction_time: Time spent in extraction (seconds)
            extraction_cost: Cost of extraction (cents)
            extraction_iterations: Number of extraction iterations
            structure_trace: Trace from structure verification loop
            agent_results: Results from all specialized agents

        Returns:
            ProcessingResult ready for API exposure
        """
        start_time = datetime.now(UTC)
        logger.info(f"Job {job_id}: Starting assembly phase")

        # Step 1: Apply all auto_corrections
        current_markdown, applied_corrections = self._apply_corrections(
            markdown, agent_results
        )
        logger.debug(
            f"Job {job_id}: Applied {len(applied_corrections)} corrections"
        )

        # Step 2: Replace placeholders with enhanced content
        current_markdown = self._replace_placeholders(
            current_markdown, agent_results
        )

        # Step 3: Final validation
        validation_issues = self._validate_final(current_markdown)
        if validation_issues:
            logger.warning(
                f"Job {job_id}: Validation issues: {validation_issues}"
            )

        # Step 4: Build ProcessingTrace
        processing_trace = self._build_trace(
            manifest=manifest,
            extraction_confidence=extraction_confidence,
            extraction_time=extraction_time,
            extraction_cost=extraction_cost,
            extraction_iterations=extraction_iterations,
            structure_trace=structure_trace,
            agent_results=agent_results,
            applied_corrections=applied_corrections,
        )

        # Step 5: Build ReviewChecklist
        all_review_items = self._collect_review_items(agent_results)
        all_observations = self._collect_observations(agent_results)
        review_checklist = ReviewChecklist.from_items_and_observations(
            all_review_items, all_observations
        )

        # Step 6: Compute final confidence
        confidence = self._compute_confidence(
            extraction_confidence=extraction_confidence,
            structure_trace=structure_trace,
            agent_results=agent_results,
            review_items=all_review_items,
        )

        # Determine status
        if all_review_items:
            status = "needs_review"
        elif validation_issues:
            status = "needs_review"  # Validation issues need attention
        else:
            status = "completed"

        end_time = datetime.now(UTC)
        processing_time = (end_time - start_time).total_seconds()

        logger.info(
            f"Job {job_id}: Assembly complete - status={status}, "
            f"confidence={confidence:.2f}, "
            f"review_items={len(all_review_items)}, "
            f"corrections_applied={len(applied_corrections)}"
        )

        return ProcessingResult(
            job_id=job_id,
            status=status,  # type: ignore[arg-type]
            markdown=current_markdown,
            confidence=confidence,
            processing_trace=processing_trace,
            review_checklist=review_checklist,
            processing_time_seconds=processing_time,
        )

    def _apply_corrections(
        self,
        markdown: str,
        agent_results: list[AgentResult],
    ) -> tuple[str, list[AutoCorrection]]:
        """Apply all auto_corrections to markdown.

        Corrections are applied in agent order. When a correction is applied,
        the linked Observation is closed with resolution='fixed' per PRD-021.

        Args:
            markdown: Current markdown text
            agent_results: Results from all specialized agents

        Returns:
            Tuple of (corrected_markdown, list_of_applied_corrections)
        """
        current_markdown = markdown
        applied: list[AutoCorrection] = []
        now = datetime.now(UTC)

        for result in agent_results:
            for correction in result.auto_corrections:
                if correction.search in current_markdown:
                    # Apply the correction (first occurrence only)
                    current_markdown = current_markdown.replace(
                        correction.search,
                        correction.replace,
                        1,
                    )

                    # Mark correction as applied
                    correction.applied = True
                    correction.applied_at = now
                    applied.append(correction)

                    # Close linked observation (simplified 2-field lifecycle)
                    obs = self._find_observation(
                        result.observations, correction.observation_id
                    )
                    if obs and obs.status == "open":
                        obs.close("fixed")

                    logger.debug(
                        f"Applied correction from {correction.agent}: "
                        f"'{correction.search[:30]}...' -> '{correction.replace[:30]}...'"
                    )
                else:
                    # Search string not found - log warning
                    logger.warning(
                        f"Correction search string not found: "
                        f"'{correction.search[:50]}...' from {correction.agent}"
                    )

        return current_markdown, applied

    def _find_observation(
        self,
        observations: list[Observation],
        observation_id: str,
    ) -> Observation | None:
        """Find observation by ID.

        Args:
            observations: List of observations to search
            observation_id: ID to find

        Returns:
            Observation if found, None otherwise
        """
        return next(
            (o for o in observations if o.id == observation_id),
            None
        )

    def _replace_placeholders(
        self,
        markdown: str,
        agent_results: list[AgentResult],
    ) -> str:
        """Replace placeholders with enhanced content.

        Enhanced content is stored in AgentResult.enhanced_content as a dict
        mapping placeholder_id to content. Most placeholder replacements are
        already handled via auto_corrections, but this handles any remaining.

        Args:
            markdown: Current markdown text
            agent_results: Results from all specialized agents

        Returns:
            Markdown with placeholders replaced
        """
        current_markdown = markdown

        for result in agent_results:
            if result.enhanced_content:
                for placeholder_id, content in result.enhanced_content.items():
                    # Handle different placeholder formats

                    # Image placeholders: img-p3-1
                    if placeholder_id.startswith("img-"):
                        # These are typically replaced via auto_corrections
                        # but we check here as fallback
                        pass

                    # Table placeholders: p3:t1
                    elif ":" in placeholder_id and placeholder_id.startswith("p"):
                        # Table content is typically replaced via auto_corrections
                        pass

                    # Generic placeholder format: {{placeholder_id}}
                    generic_placeholder = f"{{{{{placeholder_id}}}}}"
                    if generic_placeholder in current_markdown:
                        current_markdown = current_markdown.replace(
                            generic_placeholder, content
                        )
                        logger.debug(
                            f"Replaced placeholder {placeholder_id}"
                        )

        return current_markdown

    def _validate_final(self, markdown: str) -> list[str]:
        """Run final validation on assembled markdown.

        Checks for common issues that indicate incomplete processing.

        Args:
            markdown: Final markdown text

        Returns:
            List of validation issues found (empty if clean)
        """
        issues: list[str] = []

        # Check for unfilled image placeholders
        unfilled_images = re.findall(r"!\[TODO:.*?\]\(.*?\)", markdown)
        if unfilled_images:
            issues.append(
                f"Found {len(unfilled_images)} unfilled image placeholders"
            )

        # Check for unclosed table markers
        table_starts = len(re.findall(r"<!-- TABLE:p\d+:t\d+ -->", markdown))
        table_ends = len(re.findall(r"<!-- /TABLE:p\d+:t\d+ -->", markdown))
        if table_starts != table_ends:
            issues.append(
                f"Mismatched table markers: {table_starts} starts, "
                f"{table_ends} ends"
            )

        # Check for unfilled generic placeholders
        generic_placeholders = re.findall(r"\{\{[^}]+\}\}", markdown)
        if generic_placeholders:
            issues.append(
                f"Found {len(generic_placeholders)} unfilled placeholders: "
                f"{generic_placeholders[:3]}"
            )

        return issues

    def _build_trace(
        self,
        manifest: DocumentManifest,
        extraction_confidence: float,
        extraction_time: float,
        extraction_cost: float,
        extraction_iterations: int,
        structure_trace: StructureTrace,
        agent_results: list[AgentResult],
        applied_corrections: list[AutoCorrection],
    ) -> ProcessingTrace:
        """Build complete processing trace.

        Args:
            manifest: Document manifest from analysis
            extraction_confidence: Confidence from extraction
            extraction_time: Time for extraction (seconds)
            extraction_cost: Cost of extraction (cents)
            extraction_iterations: Number of extraction iterations
            structure_trace: Trace from structure loop
            agent_results: Results from specialized agents
            applied_corrections: List of applied corrections

        Returns:
            Complete ProcessingTrace for glass box transparency
        """
        # Analysis summary
        analysis = AnalysisSummary(
            document_type=manifest.document_type,
            total_pages=manifest.total_pages,
            key_entities=(
                manifest.summary.key_entities if manifest.summary else []
            ),
            required_agents=manifest.required_agents,
            confidence=manifest.analysis_confidence,
            time_seconds=0.0,  # Would need to track from analysis phase
            cost_cents=0.0,
        )

        # Extraction summary
        extraction = ExtractionSummary(
            confidence=extraction_confidence,
            pages_extracted=manifest.total_pages,
            correction_iterations=extraction_iterations,
            time_seconds=extraction_time,
            cost_cents=extraction_cost,
        )

        # Structure summary
        structure = StructureSummary(
            iterations=structure_trace.iterations,
            lint_issues_found=structure_trace.lint_issues_found,
            lint_issues_fixed=structure_trace.lint_issues_fixed,
            ocr_suggestions_processed=structure_trace.ocr_suggestions_processed,
            corrections_applied=len(structure_trace.corrections),
            final_lint_clean=structure_trace.final_lint_clean,
            time_seconds=structure_trace.time_seconds,
            cost_cents=structure_trace.cost_cents,
        )

        # Convert AgentResults to AgentTraces
        agent_traces: list[AgentTrace] = []
        now = datetime.now(UTC)

        for result in agent_results:
            # Skip if agent produced no observations (wasn't needed)
            # Note: We still include agents that ran but found no issues

            # Validate agent_name is valid for AgentTrace
            valid_agents = {"figures", "tables", "structure", "typography"}
            if result.agent_name not in valid_agents:
                logger.warning(
                    f"Skipping unknown agent: {result.agent_name}"
                )
                continue

            trace = AgentTrace(
                agent_name=result.agent_name,  # type: ignore[arg-type]
                observations=result.observations,
                auto_corrections=result.auto_corrections,
                review_items=result.review_items,
                reasoning_summary=result.reasoning_summary,
                confidence=result.confidence,
                cost_cents=result.cost_cents,
                time_seconds=result.time_seconds,
                iterations=result.iterations,
                started_at=now,  # Would need actual times from orchestrator
                completed_at=now,
            )
            agent_traces.append(trace)

        # Aggregate stats
        total_observations = sum(
            len(r.observations) for r in agent_results
        )
        total_cost = (
            extraction_cost
            + structure_trace.cost_cents
            + sum(r.cost_cents for r in agent_results)
        )
        total_time = (
            extraction_time
            + structure_trace.time_seconds
            + sum(r.time_seconds for r in agent_results)
        )

        return ProcessingTrace(
            analysis=analysis,
            extraction=extraction,
            structure=structure,
            agents=agent_traces,
            total_observations=total_observations,
            auto_corrections_applied=len(applied_corrections),
            review_items_generated=sum(
                len(r.review_items) for r in agent_results
            ),
            total_cost_cents=total_cost,
            total_time_seconds=total_time,
            total_tokens=0,  # Would need to track from usage
        )

    def _collect_review_items(
        self,
        agent_results: list[AgentResult],
    ) -> list[ReviewItem]:
        """Collect all review items from all agents.

        Args:
            agent_results: Results from all specialized agents

        Returns:
            Combined list of all review items
        """
        all_items: list[ReviewItem] = []
        for result in agent_results:
            all_items.extend(result.review_items)
        return all_items

    def _collect_observations(
        self,
        agent_results: list[AgentResult],
    ) -> list[Observation]:
        """Collect all observations from all agents.

        Needed for ReviewChecklist.from_items_and_observations() to
        derive categories from linked observations.

        Args:
            agent_results: Results from all specialized agents

        Returns:
            Combined list of all observations
        """
        all_obs: list[Observation] = []
        for result in agent_results:
            all_obs.extend(result.observations)
        return all_obs

    def _compute_confidence(
        self,
        extraction_confidence: float,
        structure_trace: StructureTrace,
        agent_results: list[AgentResult],
        review_items: list[ReviewItem],
    ) -> float:
        """Compute final confidence score.

        Uses weighted average of:
        - Extraction confidence (40%)
        - Structure confidence (20%)
        - Agent confidence average (30%)
        - Review penalty (10%)

        Args:
            extraction_confidence: Confidence from extraction phase
            structure_trace: Trace from structure loop
            agent_results: Results from specialized agents
            review_items: All review items

        Returns:
            Final confidence score (0.0 - 1.0)
        """
        # Weighted components
        weights = {
            "extraction": 0.4,
            "structure": 0.2,
            "agents": 0.3,
            "review_penalty": 0.1,
        }

        # Structure confidence (based on lint clean + iterations)
        structure_confidence = 1.0 if structure_trace.final_lint_clean else 0.8
        if structure_trace.iterations > 2:
            structure_confidence *= 0.9  # Penalty for multiple iterations

        # Agent confidence (average of agents with observations)
        agent_confidences = [
            r.confidence for r in agent_results if r.observations
        ]
        agent_confidence = (
            sum(agent_confidences) / len(agent_confidences)
            if agent_confidences
            else 1.0
        )

        # Review penalty (more items = lower confidence)
        # Each item reduces confidence by 5%, max 50% reduction
        review_penalty = 1.0 - min(0.5, len(review_items) * 0.05)

        # Weighted average
        final_confidence = (
            weights["extraction"] * extraction_confidence
            + weights["structure"] * structure_confidence
            + weights["agents"] * agent_confidence
            + weights["review_penalty"] * review_penalty
        )

        return round(final_confidence, 2)


__all__ = ["AssemblyService"]
