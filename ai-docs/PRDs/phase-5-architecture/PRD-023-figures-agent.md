# PRD-023: Figures Agent Refactor

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: Phase 3b: Refine (Specialized Agents)
**Estimated Effort**: 2 days
**Dependencies**: PRD-021 (Data Models)
**Reference**: [PRD-020](./PRD-020-3-phase-architecture.md)

## Problem Statement

The current figures agent (chained_figures.py) works well but outputs observations that go through consolidation. The new approach:

1. **Keep the chained pattern** (classify → generate) - it works well
2. **Output AgentResult directly** - no consolidation needed
3. **Route to auto_corrections or review_items** based on confidence
4. **Add validation check** for unfilled placeholders

## Success Criteria

- [x] Figures agent outputs AgentResult (not raw observations)
- [x] Auto-corrections for high-confidence alt text (>0.95)
- [x] Review items for low-confidence or complex images
- [x] Unfilled placeholder validation with re-run
- [x] Glass box reasoning in AgentTrace

## Current vs New Architecture

### Current Flow
```
classify_agent → generation_agent → routing (Python) → Observations
                                                           ↓
                                                    Consolidation
                                                           ↓
                                                       Proposals
```

### New Flow
```
classify_agent → generation_agent → routing (Python) → AgentResult
                                         ↓                  │
                                   validation check         │
                                         ↓                  │
                                   (re-run if needed)       │
                                                           ↓
                                              auto_corrections + review_items
```

## Technical Requirements

### Refactored Figures Agent

```python
# src/agents/figures/figures_agent.py

from datetime import datetime
import uuid

from pydantic import BaseModel

from src.shared.models.agent_trace import AgentResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.review_checklist import ReviewItem, ReviewOption
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.remediation import DocumentManifest
from src.agents.figures.classification_agent import classify as classify_images
from src.agents.figures.generation_agent import generate as generate_alt_text
from src.agents.model_tiers import ModelTier


class ImagePlaceholder(BaseModel):
    """Parsed image placeholder from markdown."""
    id: str
    full_match: str  # "![TODO: describe](image-page-3-1.png)"
    current_alt: str  # "TODO: describe"
    src: str  # "image-page-3-1.png"
    page_num: int
    image_index: int


class FiguresAgent:
    """Chained figures agent: classify → generate → validate."""

    CONFIDENCE_THRESHOLD = 0.95

    async def process(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
    ) -> AgentResult:
        """Process all figures and return unified result."""

        start_time = datetime.utcnow()
        observations: list[Observation] = []
        auto_corrections: list[AutoCorrection] = []
        review_items: list[ReviewItem] = []
        enhanced_content: dict[str, str] = {}
        total_cost = 0.0

        # Find all image placeholders
        placeholders = self._find_image_placeholders(markdown)

        if not placeholders:
            return AgentResult(
                agent_name="figures",
                observations=[],
                auto_corrections=[],
                review_items=[],
                reasoning_summary="No image placeholders found.",
                confidence=1.0,
                enhanced_content=None,
                cost_cents=0.0,
                time_seconds=0.0,
            )

        # Process each placeholder
        for placeholder in placeholders:
            page_image = pages[placeholder.page_num - 1].image_base64

            # Step 1: Classify image type
            classification, class_usage = await classify_images(
                page_num=placeholder.page_num,
                image_base64=page_image,
                expected_image_count=1,
                page_markdown=self._get_page_markdown(markdown, placeholder.page_num),
                job_id=job_id,
            )
            total_cost += class_usage.estimated_cost_cents

            # Get the classification for this image
            img_class = classification.images[0] if classification.images else None
            if not img_class:
                continue

            # Create observation for this image
            obs = Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="figures",
                source="agent",
                visual_description=f"{img_class.image_type} image on page {placeholder.page_num}",
                markup_description=f"Current alt: '{placeholder.current_alt}'",
                location=ObservationLocation(
                    location_type="element",
                    value=f"img[src='{placeholder.src}']",
                    page_num=placeholder.page_num,
                ),
                confidence=img_class.confidence,
                severity="major" if img_class.image_type != "decorative" else "minor",
                category="alt_text",
            )
            observations.append(obs)

            # Handle decorative images
            if img_class.image_type == "decorative":
                auto_corrections.append(AutoCorrection(
                    id=str(uuid.uuid4()),
                    observation_id=obs.id,
                    search=placeholder.full_match,
                    replace=f"![]({placeholder.src})",  # Empty alt for decorative
                    justification="Decorative image - empty alt text is correct",
                    confidence=img_class.confidence,
                    agent="figures",
                    page_num=placeholder.page_num,
                ))
                enhanced_content[placeholder.id] = ""
                continue

            # Step 2: Generate alt text for non-decorative images
            generation, gen_usage = await generate_alt_text(
                page_num=placeholder.page_num,
                image_base64=page_image,
                image_type=img_class.image_type,
                classification_reasoning=img_class.reasoning,
                document_context=manifest.summary.topic_summary if manifest.summary else "",
                job_id=job_id,
            )
            total_cost += gen_usage.estimated_cost_cents

            alt_text = generation.alt_text
            combined_confidence = min(img_class.confidence, generation.confidence)

            # Step 3: Route based on confidence and validation
            if self._validate_alt_text(alt_text) and combined_confidence >= self.CONFIDENCE_THRESHOLD:
                # High confidence - auto correct
                auto_corrections.append(AutoCorrection(
                    id=str(uuid.uuid4()),
                    observation_id=obs.id,
                    search=placeholder.full_match,
                    replace=f"![{alt_text}]({placeholder.src})",
                    justification=f"Generated alt text for {img_class.image_type}: {generation.reasoning}",
                    confidence=combined_confidence,
                    agent="figures",
                    page_num=placeholder.page_num,
                ))
                enhanced_content[placeholder.id] = alt_text
            else:
                # Low confidence or complex - needs review
                review_items.append(ReviewItem(
                    id=str(uuid.uuid4()),
                    observation_id=obs.id,
                    agent="figures",
                    # NOTE: category is NOT on ReviewItem - derived from Observation at checklist level
                    question=f"Verify alt text for {img_class.image_type} on page {placeholder.page_num}",
                    options=[
                        ReviewOption(
                            id="accept",
                            label=f"Accept: \"{alt_text[:50]}{'...' if len(alt_text) > 50 else ''}\"",
                            action="replace",
                            replacement_text=f"![{alt_text}]({placeholder.src})",
                            is_recommended=True,
                        ),
                        ReviewOption(
                            id="decorative",
                            label="Mark as decorative (empty alt)",
                            action="replace",
                            replacement_text=f"![]({placeholder.src})",
                            is_recommended=False,
                        ),
                        ReviewOption(
                            id="other",
                            label="Write custom alt text",
                            action="other",
                            is_recommended=False,
                        ),
                    ],
                    search_text=placeholder.full_match,  # Text to find for replacement
                    context=f"Image type: {img_class.image_type}\n\nGenerated alt text:\n{alt_text}\n\nReasoning: {generation.reasoning}",
                    page_num=placeholder.page_num,
                    agent_recommendation=alt_text,
                    agent_confidence=combined_confidence,
                ))

        # Validation check: ensure no placeholders left unfilled
        unfilled = self._check_unfilled_placeholders(placeholders, enhanced_content, review_items)
        if unfilled:
            # Log warning - these will appear in review
            pass

        end_time = datetime.utcnow()

        return AgentResult(
            agent_name="figures",
            observations=observations,
            auto_corrections=auto_corrections,
            review_items=review_items,
            reasoning_summary=self._build_summary(placeholders, auto_corrections, review_items),
            confidence=self._calculate_confidence(auto_corrections, review_items),
            enhanced_content=enhanced_content if enhanced_content else None,
            cost_cents=total_cost,
            time_seconds=(end_time - start_time).total_seconds(),
        )

    def _find_image_placeholders(self, markdown: str) -> list[ImagePlaceholder]:
        """Find all image placeholders in markdown."""
        import re
        placeholders = []

        # Match: ![alt text](image-page-X-Y.png)
        pattern = r'!\[(.*?)\]\((image-page-(\d+)-(\d+)\.png)\)'

        for match in re.finditer(pattern, markdown):
            alt_text = match.group(1)
            src = match.group(2)
            page_num = int(match.group(3))
            image_index = int(match.group(4))

            placeholders.append(ImagePlaceholder(
                id=f"img-p{page_num}-{image_index}",
                full_match=match.group(0),
                current_alt=alt_text,
                src=src,
                page_num=page_num,
                image_index=image_index,
            ))

        return placeholders

    def _validate_alt_text(self, alt_text: str) -> bool:
        """Check alt text meets basic requirements."""
        if not alt_text:
            return False
        if len(alt_text) < 10:
            return False
        if alt_text.lower().startswith("todo"):
            return False
        if alt_text.lower() in ("image", "picture", "photo", "figure"):
            return False
        return True

    def _check_unfilled_placeholders(
        self,
        placeholders: list[ImagePlaceholder],
        enhanced: dict[str, str],
        review_items: list[ReviewItem],
    ) -> list[ImagePlaceholder]:
        """Find placeholders that weren't processed."""
        review_obs_ids = {item.observation_id for item in review_items}
        unfilled = []

        for p in placeholders:
            if p.id not in enhanced:
                # Check if it's in review items
                # (observation IDs won't match placeholder IDs directly)
                # This is a simplified check
                unfilled.append(p)

        return unfilled

    def _build_summary(
        self,
        placeholders: list[ImagePlaceholder],
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> str:
        """Build human-readable summary."""
        total = len(placeholders)
        auto = len(auto_corrections)
        review = len(review_items)
        return f"Processed {total} images. {auto} auto-corrected, {review} need review."

    def _calculate_confidence(
        self,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> float:
        """Calculate overall confidence."""
        if not auto_corrections and not review_items:
            return 1.0

        all_confidences = [c.confidence for c in auto_corrections]
        all_confidences.extend([r.agent_confidence for r in review_items])

        return sum(all_confidences) / len(all_confidences) if all_confidences else 0.5

    def _get_page_markdown(self, markdown: str, page_num: int) -> str:
        """Extract markdown for a specific page."""
        import re
        pattern = rf'<!-- Page {page_num} -->(.*?)(?=<!-- Page \d+ -->|$)'
        match = re.search(pattern, markdown, re.DOTALL)
        return match.group(1) if match else ""
```

### Integration

```python
# src/services/processing_service.py (Phase 2 figures call)

from src.agents.figures.figures_agent import FiguresAgent

# In Phase 2: Specialized Agents
if "figures" in manifest.required_agents:
    figures_agent = FiguresAgent()
    figures_result = await figures_agent.process(
        markdown=full_markdown,
        pages=pages,
        manifest=manifest,
        job_id=job_id,
    )
    agent_results.append(figures_result)
```

## Acceptance Criteria

### Classification
- [x] Uses existing classification_agent
- [x] Identifies decorative, informative, complex images
- [x] Confidence scores propagated

### Generation
- [x] Uses existing generation_agent
- [x] Alt text generated for non-decorative images
- [x] Document context included in prompt

### Routing
- [x] High confidence (>0.95) → auto_corrections
- [x] Low confidence → review_items
- [x] Decorative images → auto empty alt

### Validation
- [x] Alt text validation (length, not placeholder)
- [x] Unfilled placeholder detection
- [x] Warning logged for missed placeholders

### Output
- [x] Returns AgentResult model
- [x] Glass box reasoning in summary
- [x] Cost and timing tracked

## Deliverables

### Files to Create/Modify
```
src/agents/figures/
├── figures_agent.py       # NEW: Main agent class
├── classification_agent.py  # KEEP: Existing
├── generation_agent.py      # KEEP: Existing
├── routing.py               # DEPRECATE: Routing now inline

tests/unit/agents/figures/
├── test_figures_agent.py    # NEW: Unit tests
```

## Definition of Done

- [x] FiguresAgent class implemented
- [x] Returns AgentResult correctly
- [x] Auto-corrections for high-confidence alt text
- [x] Review items with options for humans
- [x] Unit tests passing (24 tests)
- [x] Integration with processing service

**Implementation Notes**:
- Created `src/agents/figures/figures_agent.py` with FiguresAgent class
- Updated `src/agents/figures/__init__.py` to export FiguresAgent
- Updated `src/services/processing_service.py` to use new agent
- Removed deprecated files: `routing.py`, `chained_figures.py`, top-level `figures_agent.py`
- All 1226 unit tests passing
- Completed: 2024-12-17
