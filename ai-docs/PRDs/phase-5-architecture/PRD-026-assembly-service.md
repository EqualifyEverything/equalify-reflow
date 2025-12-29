# PRD-026: Assembly Service (Phase 4: Assemble)

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: Phase 4: Assemble
**Estimated Effort**: 2 days
**Dependencies**: PRD-021 (Data Models), PRD-023-025 (Agent Refactors)
**Reference**: [PRD-020](./PRD-020-3-phase-architecture.md)

## Problem Statement

After all specialized agents complete, we need to:
1. Apply all auto_corrections to the markdown
2. Replace placeholders with enhanced content
3. Run final validation
4. Build ProcessingTrace from all AgentTraces
5. Build ReviewChecklist from all review_items
6. Compute final confidence score

This is **Phase 4: Assemble** - pure Python assembly with no LLM cost.

## Success Criteria

- [ ] All auto_corrections applied correctly
- [ ] Placeholders replaced with enhanced content
- [ ] Final markdown passes lint validation
- [ ] ProcessingTrace captures all phases
- [ ] ReviewChecklist properly grouped
- [ ] Confidence score computed from all sources
- [ ] ProcessingResult ready for API exposure

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 4: ASSEMBLE                                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Inputs:                                                    │
│  • markdown (from Phase 3: Refine)                          │
│  • structure_trace (from Phase 3a: Structure Loop)          │
│  • agent_results[] (from Phase 3b: Specialized Agents)      │
│  • manifest (from Phase 1: Analyze)                         │
│                                                             │
│  Step 1: Apply auto_corrections                             │
│  ├── For each agent_result                                  │
│  │   └── For each auto_correction                           │
│  │       └── Replace search → replace in markdown           │
│  │                                                          │
│  Step 2: Replace placeholders                               │
│  ├── For each agent_result.enhanced_content                 │
│  │   └── Replace placeholder with content                   │
│  │                                                          │
│  Step 3: Final validation                                   │
│  ├── Run markdown lint                                      │
│  ├── Check all placeholders resolved                        │
│  │                                                          │
│  Step 4: Build ProcessingTrace                              │
│  ├── analysis: from manifest                                │
│  ├── extraction: from extraction result                     │
│  ├── structure: from structure_trace                        │
│  ├── agents[]: from agent_results                           │
│  │                                                          │
│  Step 5: Build ReviewChecklist                              │
│  ├── Collect all review_items                               │
│  ├── Group by category, agent, page                         │
│  │                                                          │
│  Step 6: Compute confidence                                 │
│  ├── Weighted average of all phases                         │
│  │                                                          │
│  Output: ProcessingResult                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Technical Requirements

### Assembly Service Implementation

```python
# src/services/assembly_service.py

from datetime import datetime
import re

from src.shared.models.processing_result import (
    ProcessingResult,
    ProcessingTrace,
    AnalysisSummary,
    ExtractionSummary,
    StructureSummary,
)
from src.shared.models.agent_trace import AgentTrace, AgentResult
from src.shared.models.review_checklist import ReviewChecklist, ReviewItem
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation
from src.shared.models.remediation import DocumentManifest
from src.services.structure_loop import StructureTrace


class AssemblyService:
    """Phase 4: Combine all agent outputs into final result."""

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
        """Assemble final processing result from all phases."""

        start_time = datetime.utcnow()

        # Step 1: Apply all auto_corrections
        current_markdown, applied_corrections = self._apply_corrections(
            markdown, agent_results
        )

        # Step 2: Replace placeholders with enhanced content
        current_markdown = self._replace_placeholders(
            current_markdown, agent_results
        )

        # Step 3: Final validation
        validation_issues = self._validate_final(current_markdown)

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

        end_time = datetime.utcnow()

        return ProcessingResult(
            job_id=job_id,
            status=status,
            markdown=current_markdown,
            confidence=confidence,
            processing_trace=processing_trace,
            review_checklist=review_checklist,
            processing_time_seconds=(end_time - start_time).total_seconds(),
        )

    def _apply_corrections(
        self,
        markdown: str,
        agent_results: list[AgentResult],
    ) -> tuple[str, list[AutoCorrection]]:
        """Apply all auto_corrections to markdown.

        Also closes the linked Observation with resolution='fixed'.
        See PRD-021 simplified 2-field lifecycle.
        """

        current_markdown = markdown
        applied: list[AutoCorrection] = []
        now = datetime.utcnow()

        for result in agent_results:
            for correction in result.auto_corrections:
                if correction.search in current_markdown:
                    # Apply the correction
                    current_markdown = current_markdown.replace(
                        correction.search,
                        correction.replace,
                        1,  # Only first occurrence
                    )

                    # Mark correction as applied
                    correction.applied = True
                    correction.applied_at = now
                    applied.append(correction)

                    # Close linked observation (simplified 2-field lifecycle)
                    obs = self._find_observation(result.observations, correction.observation_id)
                    if obs:
                        obs.close("fixed")
                else:
                    # Search string not found - log warning
                    pass

        return current_markdown, applied

    def _find_observation(
        self,
        observations: list["Observation"],
        observation_id: str,
    ) -> "Observation | None":
        """Find observation by ID."""
        return next((o for o in observations if o.id == observation_id), None)

    def _replace_placeholders(
        self,
        markdown: str,
        agent_results: list[AgentResult],
    ) -> str:
        """Replace placeholders with enhanced content."""

        current_markdown = markdown

        for result in agent_results:
            if result.enhanced_content:
                for placeholder_id, content in result.enhanced_content.items():
                    # Handle different placeholder formats

                    # Image placeholders: img-p3-1
                    if placeholder_id.startswith("img-"):
                        # These are replaced via auto_corrections already
                        pass

                    # Table placeholders: p3:t1
                    elif ":" in placeholder_id and placeholder_id.startswith("p"):
                        # Table content is replaced via auto_corrections
                        pass

        return current_markdown

    def _validate_final(self, markdown: str) -> list[str]:
        """Run final validation on assembled markdown."""

        issues = []

        # Check for unfilled image placeholders
        unfilled_images = re.findall(r'!\[TODO:.*?\]\(.*?\)', markdown)
        if unfilled_images:
            issues.append(f"Found {len(unfilled_images)} unfilled image placeholders")

        # Check for unclosed table markers
        table_starts = len(re.findall(r'<!-- TABLE:p\d+:t\d+ -->', markdown))
        table_ends = len(re.findall(r'<!-- /TABLE:p\d+:t\d+ -->', markdown))
        if table_starts != table_ends:
            issues.append(f"Mismatched table markers: {table_starts} starts, {table_ends} ends")

        # Run markdown lint (optional - may be slow)
        # lint_issues = self._run_lint(markdown)
        # issues.extend(lint_issues)

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
        """Build complete processing trace."""

        # Analysis summary
        analysis = AnalysisSummary(
            document_type=manifest.document_type,
            total_pages=manifest.total_pages,
            key_entities=manifest.summary.key_entities if manifest.summary else [],
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
            time_seconds=0.0,  # Would need to track
            cost_cents=0.0,
        )

        # Convert AgentResults to AgentTraces
        agent_traces = []
        for result in agent_results:
            trace = AgentTrace(
                agent_name=result.agent_name,
                observations=result.observations,
                auto_corrections=result.auto_corrections,
                review_items=result.review_items,
                reasoning_summary=result.reasoning_summary,
                confidence=result.confidence,
                cost_cents=result.cost_cents,
                time_seconds=result.time_seconds,
                iterations=result.iterations,
                started_at=datetime.utcnow(),  # Would need actual times
                completed_at=datetime.utcnow(),
            )
            agent_traces.append(trace)

        # Aggregate stats
        total_observations = sum(len(r.observations) for r in agent_results)
        total_cost = (
            extraction_cost +
            sum(r.cost_cents for r in agent_results)
        )
        total_time = (
            extraction_time +
            sum(r.time_seconds for r in agent_results)
        )
        total_tokens = 0  # Would need to track from usage

        return ProcessingTrace(
            analysis=analysis,
            extraction=extraction,
            structure=structure,
            agents=agent_traces,
            total_observations=total_observations,
            auto_corrections_applied=len(applied_corrections),
            review_items_generated=sum(len(r.review_items) for r in agent_results),
            total_cost_cents=total_cost,
            total_time_seconds=total_time,
            total_tokens=total_tokens,
        )

    def _collect_review_items(
        self,
        agent_results: list[AgentResult],
    ) -> list[ReviewItem]:
        """Collect all review items from all agents."""

        all_items = []
        for result in agent_results:
            all_items.extend(result.review_items)
        return all_items

    def _collect_observations(
        self,
        agent_results: list[AgentResult],
    ) -> list[Observation]:
        """Collect all observations from all agents.

        Needed for ReviewChecklist.from_items_and_observations() to derive categories.
        """

        all_obs = []
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
        """Compute final confidence score."""

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

        # Agent confidence (average)
        agent_confidences = [r.confidence for r in agent_results if r.observations]
        agent_confidence = (
            sum(agent_confidences) / len(agent_confidences)
            if agent_confidences else 1.0
        )

        # Review penalty (more items = lower confidence)
        review_penalty = 1.0 - min(0.5, len(review_items) * 0.05)

        # Weighted average
        final_confidence = (
            weights["extraction"] * extraction_confidence +
            weights["structure"] * structure_confidence +
            weights["agents"] * agent_confidence +
            weights["review_penalty"] * review_penalty
        )

        return round(final_confidence, 2)
```

### Integration with Processing Service

```python
# src/services/processing_service.py (modifications)

from src.services.assembly_service import AssemblyService

async def process_document(...):
    # Phase 1: Analyze
    # Phase 2: Extract
    # Phase 3: Refine (Structure Loop + Specialized Agents)

    # Phase 4: Assemble
    assembly_service = AssemblyService()

    processing_result = assembly_service.assemble(
        job_id=job_id,
        markdown=full_markdown,
        manifest=manifest,
        extraction_confidence=extraction_result.metrics.confidence,
        extraction_time=extraction_time,
        extraction_cost=extraction_usage.estimated_cost_cents,
        extraction_iterations=extraction_result.attempt_count,
        structure_trace=structure_result.trace,
        agent_results=agent_results,
    )

    # Store result
    await storage.save_processing_result(job_id, processing_result)

    # Update job status
    await job_service.update_job_status(
        job_id,
        status="completed" if processing_result.status == "completed" else "processing",
        substatus=processing_result.status,
        confidence_score=processing_result.confidence,
        review_items_count=processing_result.review_checklist.total_items,
    )

    return processing_result
```

## Acceptance Criteria

### Correction Application
- [ ] All auto_corrections applied in order
- [ ] Search strings matched exactly
- [ ] First occurrence only replaced
- [ ] AutoCorrection.applied status tracked
- [ ] Linked Observation closed with resolution="fixed"

### Placeholder Replacement
- [ ] Image placeholders handled
- [ ] Table placeholders handled
- [ ] Code block placeholders handled

### Validation
- [ ] Unfilled placeholders detected
- [ ] Mismatched markers detected
- [ ] Optional lint check

### Trace Building
- [ ] All phases included
- [ ] Agent traces complete
- [ ] Stats aggregated correctly

### Checklist Building
- [ ] All review items collected
- [ ] All observations collected for category derivation
- [ ] from_items_and_observations() used
- [ ] Proper grouping (category derived from observations, agent, page)
- [ ] Summary generated

### Confidence
- [ ] Weighted calculation
- [ ] Review penalty applied
- [ ] Reasonable range (0-1)

## Deliverables

### Files to Create
```
src/services/
├── assembly_service.py

tests/unit/services/
├── test_assembly_service.py
```

### Files to Modify
```
src/services/processing_service.py  # Integration
src/dependencies.py                 # Add assembly service
```

## Definition of Done

- [ ] AssemblyService implemented
- [ ] All corrections applied correctly
- [ ] ProcessingTrace complete
- [ ] ReviewChecklist properly grouped
- [ ] Confidence computed
- [ ] Unit tests passing
- [ ] Integration with processing service
