"""Figures agent for Phase 3b: Refine (Specialized Agents).

Chained figures agent that orchestrates:
1. Classification (Haiku) - Classify images by type
2. Generation (Haiku) - Generate alt text for non-decorative images
3. Routing (inline) - Route to auto_corrections or review_items

Key changes from previous approach:
- Outputs AgentResult directly (no consolidation layer)
- High confidence (>=0.95) → AutoCorrection
- Low confidence or complex → ReviewItem
- Decorative images → AutoCorrection (empty alt)

Reference: PRD-023 - Figures Agent Refactor
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from src.agents.figures.classification_agent import (
    ImageClassification,
    classify,
)
from src.agents.figures.generation_agent import (
    AltTextGeneration,
    generate,
)
from src.agents.helpers import get_page_features
from src.services.pdf_converter import PageData
from src.shared.models.agent_trace import AgentResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.remediation import DocumentManifest
from src.shared.models.review_checklist import ReviewItem, ReviewOption

logger = logging.getLogger(__name__)


class ImagePlaceholder(BaseModel):
    """Parsed image placeholder from markdown."""

    id: str = Field(description="Unique identifier for this placeholder")
    full_match: str = Field(description="Full markdown match: ![alt](src)")
    current_alt: str = Field(description="Current alt text value")
    src: str = Field(description="Image source filename")
    page_num: int = Field(ge=1, description="Page number from filename")
    image_index: int = Field(ge=1, description="Image index on page from filename")


class FiguresAgent:
    """Chained figures agent: classify → generate → validate.

    Orchestrates the multi-step figures analysis pipeline and outputs
    AgentResult directly for the new 4-phase architecture.

    The agent:
    1. Finds image placeholders in markdown
    2. Classifies each image (decorative, informative, complex, text)
    3. Generates alt text for non-decorative images
    4. Routes based on confidence:
       - High confidence (>=0.95) → AutoCorrection
       - Low confidence or complex/text → ReviewItem
       - Decorative → AutoCorrection (empty alt)

    Attributes:
        CONFIDENCE_THRESHOLD: Minimum confidence for auto-correction (0.95)
    """

    CONFIDENCE_THRESHOLD = 0.95

    async def process(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
    ) -> AgentResult:
        """Process all figures and return unified result.

        Args:
            markdown: Full document markdown with image placeholders
            pages: List of PageData with page images
            manifest: Document manifest with metadata
            job_id: Job identifier for tracking

        Returns:
            AgentResult containing observations, auto_corrections, and review_items
        """
        start_time = datetime.now(UTC)
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

        logger.info(
            f"Job {job_id}: FiguresAgent found {len(placeholders)} image placeholders"
        )

        # Group placeholders by page for efficient processing
        placeholders_by_page: dict[int, list[ImagePlaceholder]] = {}
        for p in placeholders:
            placeholders_by_page.setdefault(p.page_num, []).append(p)

        # Process each page
        for page_num, page_placeholders in placeholders_by_page.items():
            # Get page data
            page_data = self._get_page_data(pages, page_num)
            if not page_data or not page_data.image_base64:
                logger.warning(
                    f"Job {job_id}: No image data for page {page_num}, skipping"
                )
                continue

            # Get page features for expected image count
            page_features = get_page_features(manifest, page_num)
            expected_count = page_features.image_count if page_features else len(page_placeholders)

            # Extract page markdown for context
            page_markdown = self._get_page_markdown(markdown, page_num, manifest.total_pages)

            try:
                # Step 1: Classify all images on this page
                classification_output, class_usage = await classify(
                    page_num=page_num,
                    image_base64=page_data.image_base64,
                    expected_image_count=expected_count,
                    page_markdown=page_markdown,
                    job_id=job_id,
                )
                total_cost += class_usage.estimated_cost_cents

                # Build classification lookup by image index
                classifications_by_index = {
                    c.image_index: c for c in classification_output.classifications
                }

                # Step 2: Generate alt text for non-decorative images
                non_decorative = [
                    c for c in classification_output.classifications
                    if c.image_type != "decorative"
                ]

                generations_by_index: dict[int, AltTextGeneration] = {}
                if non_decorative:
                    document_context = (
                        f"Document: {manifest.document_title} ({manifest.document_type})"
                    )
                    if manifest.summary and manifest.summary.topic_summary:
                        document_context += f"\nTopic: {manifest.summary.topic_summary}"

                    generation_output, gen_usage = await generate(
                        page_num=page_num,
                        image_base64=page_data.image_base64,
                        classifications=classification_output.classifications,
                        page_markdown=page_markdown,
                        document_context=document_context,
                        job_id=job_id,
                    )
                    total_cost += gen_usage.estimated_cost_cents

                    generations_by_index = {
                        g.image_index: g for g in generation_output.generations
                    }

                # Step 3: Route each placeholder
                for placeholder in page_placeholders:
                    classification = classifications_by_index.get(placeholder.image_index)
                    generation = generations_by_index.get(placeholder.image_index)

                    if not classification:
                        logger.warning(
                            f"Job {job_id}: No classification for image "
                            f"{placeholder.image_index} on page {page_num}"
                        )
                        continue

                    # Create observation
                    obs = self._create_observation(
                        placeholder=placeholder,
                        classification=classification,
                        job_id=job_id,
                    )
                    observations.append(obs)

                    # Route based on image type and confidence
                    self._route_image(
                        placeholder=placeholder,
                        classification=classification,
                        generation=generation,
                        observation=obs,
                        auto_corrections=auto_corrections,
                        review_items=review_items,
                        enhanced_content=enhanced_content,
                    )

            except Exception as e:
                logger.error(
                    f"Job {job_id}: FiguresAgent failed on page {page_num}: {e}",
                    exc_info=True,
                )
                # Create review items for each placeholder on this failed page
                for placeholder in page_placeholders:
                    # Create observation for the failed image
                    obs = Observation(
                        id=str(uuid4()),
                        job_id=job_id,
                        agent="figures",
                        source="agent",
                        visual_description=f"Image on page {placeholder.page_num} (processing failed)",
                        markup_description=f"Current alt: '{placeholder.current_alt}'",
                        location=ObservationLocation(
                            location_type="element",
                            value=f"img[src='{placeholder.src}']",
                            page_num=placeholder.page_num,
                        ),
                        confidence=0.0,
                        severity="major",
                        category="alt_text",
                    )
                    observations.append(obs)

                    # Create review item for manual processing
                    review_items.append(
                        ReviewItem(
                            id=str(uuid4()),
                            observation_id=obs.id,
                            agent="figures",
                            question=(
                                f"Provide alt text for image on page {placeholder.page_num} "
                                "(automated processing failed)"
                            ),
                            options=[
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
                                    is_recommended=True,
                                ),
                            ],
                            search_text=placeholder.full_match,
                            context=(
                                f"Automated processing failed for this image.\n\n"
                                f"Error: {str(e)}\n\n"
                                "Manual review and alt text input required."
                            ),
                            page_num=placeholder.page_num,
                            agent_recommendation="Manual input required due to processing error",
                            agent_confidence=0.0,
                        )
                    )
                # Continue with other pages

        # Validation: check for unfilled placeholders
        unfilled = self._check_unfilled_placeholders(
            placeholders, enhanced_content, review_items
        )
        if unfilled:
            logger.warning(
                f"Job {job_id}: {len(unfilled)} placeholders not processed: "
                f"{[p.id for p in unfilled]}"
            )
            # Create review items for each unfilled placeholder
            for placeholder in unfilled:
                # Create observation for unfilled placeholder
                obs = Observation(
                    id=str(uuid4()),
                    job_id=job_id,
                    agent="figures",
                    source="agent",
                    visual_description=f"Image on page {placeholder.page_num} (not processed)",
                    markup_description=f"Current alt: '{placeholder.current_alt}'",
                    location=ObservationLocation(
                        location_type="element",
                        value=f"img[src='{placeholder.src}']",
                        page_num=placeholder.page_num,
                    ),
                    confidence=0.0,
                    severity="major",
                    category="alt_text",
                )
                observations.append(obs)

                # Create review item for manual processing
                review_items.append(
                    ReviewItem(
                        id=str(uuid4()),
                        observation_id=obs.id,
                        agent="figures",
                        question=(
                            f"Provide alt text for image on page {placeholder.page_num} "
                            "(placeholder not processed)"
                        ),
                        options=[
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
                                is_recommended=True,
                            ),
                        ],
                        search_text=placeholder.full_match,
                        context=(
                            f"This image placeholder was not processed by the automated system.\n\n"
                            f"Current alt text: '{placeholder.current_alt}'\n\n"
                            "Manual review and alt text input required."
                        ),
                        page_num=placeholder.page_num,
                        agent_recommendation="Manual input required - placeholder not processed",
                        agent_confidence=0.0,
                    )
                )

        end_time = datetime.now(UTC)

        return AgentResult(
            agent_name="figures",
            observations=observations,
            auto_corrections=auto_corrections,
            review_items=review_items,
            reasoning_summary=self._build_summary(
                placeholders, auto_corrections, review_items
            ),
            confidence=self._calculate_confidence(auto_corrections, review_items),
            enhanced_content=enhanced_content if enhanced_content else None,
            cost_cents=total_cost,
            time_seconds=(end_time - start_time).total_seconds(),
        )

    def _find_image_placeholders(self, markdown: str) -> list[ImagePlaceholder]:
        """Find all image placeholders in markdown.

        Matches patterns like: ![alt text](image-page-X-Y.png)
        where X is page number and Y is image index.

        Args:
            markdown: Full document markdown

        Returns:
            List of ImagePlaceholder objects
        """
        placeholders: list[ImagePlaceholder] = []

        # Match: ![alt text](image-page-X-Y.png)
        pattern = r"!\[(.*?)\]\((image-page-(\d+)-(\d+)\.png)\)"

        for match in re.finditer(pattern, markdown):
            alt_text = match.group(1)
            src = match.group(2)
            page_num = int(match.group(3))
            image_index = int(match.group(4))

            placeholders.append(
                ImagePlaceholder(
                    id=f"img-p{page_num}-{image_index}",
                    full_match=match.group(0),
                    current_alt=alt_text,
                    src=src,
                    page_num=page_num,
                    image_index=image_index,
                )
            )

        return placeholders

    def _get_page_data(
        self, pages: list[PageData], page_num: int
    ) -> PageData | None:
        """Get PageData for a specific page number.

        Args:
            pages: List of all page data
            page_num: Target page number (1-indexed)

        Returns:
            PageData for the page, or None if not found
        """
        for page in pages:
            if page.page_num == page_num:
                return page
        return None

    def _get_page_markdown(self, markdown: str, page_num: int, total_pages: int) -> str:
        """Extract markdown for a specific page.

        Uses page comment markers if present, otherwise estimates
        based on line count.

        Args:
            markdown: Full document markdown
            page_num: Target page number (1-indexed)
            total_pages: Total number of pages in the document

        Returns:
            Markdown content for the page
        """
        # Try to find page markers first
        pattern = rf"<!-- Page {page_num} -->(.*?)(?=<!-- Page \d+ -->|$)"
        match = re.search(pattern, markdown, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Fall back to line-based estimation
        lines = markdown.split("\n")
        if not lines:
            return ""

        chunk_size = max(len(lines) // total_pages, 30)
        start = (page_num - 1) * chunk_size
        end = min(page_num * chunk_size, len(lines))

        return "\n".join(lines[start:end])

    def _create_observation(
        self,
        placeholder: ImagePlaceholder,
        classification: ImageClassification,
        job_id: str,
    ) -> Observation:
        """Create an Observation for an image.

        Args:
            placeholder: The image placeholder
            classification: Classification result for the image
            job_id: Job identifier

        Returns:
            Observation for this image
        """
        # Determine severity
        severity = "minor" if classification.image_type == "decorative" else "major"

        return Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="figures",
            source="agent",
            visual_description=(
                f"{classification.image_type} image on page {placeholder.page_num}: "
                f"{classification.visual_description}"
            ),
            markup_description=f"Current alt: '{placeholder.current_alt}'",
            location=ObservationLocation(
                location_type="element",
                value=f"img[src='{placeholder.src}']",
                page_num=placeholder.page_num,
            ),
            confidence=classification.confidence,
            severity=severity,  # type: ignore[arg-type]
            category="alt_text",
        )

    def _route_image(
        self,
        placeholder: ImagePlaceholder,
        classification: ImageClassification,
        generation: AltTextGeneration | None,
        observation: Observation,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
        enhanced_content: dict[str, str],
    ) -> None:
        """Route an image to auto_corrections or review_items.

        Routing rules:
        - Decorative → AutoCorrection (empty alt)
        - High confidence (>=0.95) + valid alt → AutoCorrection
        - Complex/text images → ReviewItem (always need verification)
        - Low confidence → ReviewItem

        Args:
            placeholder: The image placeholder
            classification: Classification result
            generation: Alt text generation result (None for decorative)
            observation: The observation for this image
            auto_corrections: List to append auto-corrections to
            review_items: List to append review items to
            enhanced_content: Dict to update with enhanced content
        """
        # Handle decorative images
        if classification.image_type == "decorative":
            auto_corrections.append(
                AutoCorrection(
                    id=str(uuid4()),
                    observation_id=observation.id,
                    search=placeholder.full_match,
                    replace=f"![]({placeholder.src})",  # Empty alt for decorative
                    justification="Decorative image - empty alt text is correct per WCAG",
                    confidence=classification.confidence,
                    agent="figures",
                    page_num=placeholder.page_num,
                )
            )
            enhanced_content[placeholder.id] = ""
            return

        # Non-decorative requires generation
        if not generation:
            # Create review item for missing generation
            self._create_review_item_for_missing_generation(
                placeholder=placeholder,
                classification=classification,
                observation=observation,
                review_items=review_items,
            )
            return

        alt_text = generation.alt_text
        combined_confidence = min(classification.confidence, generation.confidence)

        # Complex and text images always go to review (need human verification)
        if classification.image_type in ("complex", "text"):
            self._create_review_item(
                placeholder=placeholder,
                classification=classification,
                generation=generation,
                observation=observation,
                review_items=review_items,
                reason=(
                    f"{classification.image_type.capitalize()} image requires "
                    "human verification of description"
                ),
            )
            return

        # Check confidence and validation for auto-correction
        if (
            self._validate_alt_text(alt_text)
            and combined_confidence >= self.CONFIDENCE_THRESHOLD
        ):
            # High confidence - auto correct
            auto_corrections.append(
                AutoCorrection(
                    id=str(uuid4()),
                    observation_id=observation.id,
                    search=placeholder.full_match,
                    replace=f"![{alt_text}]({placeholder.src})",
                    justification=(
                        f"Generated alt text for {classification.image_type} image "
                        f"with confidence {combined_confidence:.2f}: "
                        f"{generation.generation_notes or 'No additional notes'}"
                    ),
                    confidence=combined_confidence,
                    agent="figures",
                    page_num=placeholder.page_num,
                )
            )
            enhanced_content[placeholder.id] = alt_text
        else:
            # Low confidence or invalid alt text - needs review
            reason = (
                f"Low confidence ({combined_confidence:.2f})"
                if combined_confidence < self.CONFIDENCE_THRESHOLD
                else "Alt text validation failed"
            )
            self._create_review_item(
                placeholder=placeholder,
                classification=classification,
                generation=generation,
                observation=observation,
                review_items=review_items,
                reason=reason,
            )

    def _create_review_item(
        self,
        placeholder: ImagePlaceholder,
        classification: ImageClassification,
        generation: AltTextGeneration,
        observation: Observation,
        review_items: list[ReviewItem],
        reason: str,
    ) -> None:
        """Create a ReviewItem for an image needing human decision.

        Args:
            placeholder: The image placeholder
            classification: Classification result
            generation: Generation result
            observation: The observation
            review_items: List to append to
            reason: Why this needs review
        """
        alt_text = generation.alt_text
        combined_confidence = min(classification.confidence, generation.confidence)

        # Truncate alt text for label if needed
        alt_preview = alt_text[:50] + "..." if len(alt_text) > 50 else alt_text

        review_items.append(
            ReviewItem(
                id=str(uuid4()),
                observation_id=observation.id,
                agent="figures",
                question=(
                    f"Verify alt text for {classification.image_type} image "
                    f"on page {placeholder.page_num}"
                ),
                options=[
                    ReviewOption(
                        id="accept",
                        label=f'Accept: "{alt_preview}"',
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
                search_text=placeholder.full_match,
                context=(
                    f"Image type: {classification.image_type}\n\n"
                    f"Generated alt text:\n{alt_text}\n\n"
                    f"Reasoning: {generation.generation_notes or 'N/A'}\n\n"
                    f"Review reason: {reason}"
                ),
                page_num=placeholder.page_num,
                agent_recommendation=alt_text,
                agent_confidence=combined_confidence,
            )
        )

    def _create_review_item_for_missing_generation(
        self,
        placeholder: ImagePlaceholder,
        classification: ImageClassification,
        observation: Observation,
        review_items: list[ReviewItem],
    ) -> None:
        """Create a ReviewItem when generation failed.

        Args:
            placeholder: The image placeholder
            classification: Classification result
            observation: The observation
            review_items: List to append to
        """
        review_items.append(
            ReviewItem(
                id=str(uuid4()),
                observation_id=observation.id,
                agent="figures",
                question=(
                    f"Provide alt text for {classification.image_type} image "
                    f"on page {placeholder.page_num}"
                ),
                options=[
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
                        is_recommended=True,
                    ),
                ],
                search_text=placeholder.full_match,
                context=(
                    f"Image type: {classification.image_type}\n\n"
                    f"Visual description: {classification.visual_description}\n\n"
                    "Alt text generation failed - manual input required."
                ),
                page_num=placeholder.page_num,
                agent_recommendation="Manual input required",
                agent_confidence=0.0,
            )
        )

    def _validate_alt_text(self, alt_text: str) -> bool:
        """Check if alt text meets basic requirements.

        Requirements:
        - Not empty
        - At least 10 characters
        - Doesn't start with "TODO"
        - Not a generic word like "image", "picture", etc.

        Args:
            alt_text: The alt text to validate

        Returns:
            True if valid, False otherwise
        """
        if not alt_text:
            return False
        if len(alt_text) < 10:
            return False
        if alt_text.lower().startswith("todo"):
            return False
        if alt_text.lower() in ("image", "picture", "photo", "figure", "graphic"):
            return False
        return True

    def _check_unfilled_placeholders(
        self,
        placeholders: list[ImagePlaceholder],
        enhanced: dict[str, str],
        review_items: list[ReviewItem],
    ) -> list[ImagePlaceholder]:
        """Find placeholders that weren't processed.

        Args:
            placeholders: All found placeholders
            enhanced: Dict of placeholder_id -> enhanced content
            review_items: List of review items

        Returns:
            List of unprocessed placeholders
        """
        # Get all placeholder IDs that were processed
        processed_ids = set(enhanced.keys())

        # Get placeholder IDs from review items (by matching search_text)
        for item in review_items:
            for p in placeholders:
                if p.full_match == item.search_text:
                    processed_ids.add(p.id)
                    break

        # Find unprocessed
        unfilled = [p for p in placeholders if p.id not in processed_ids]
        return unfilled

    def _build_summary(
        self,
        placeholders: list[ImagePlaceholder],
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> str:
        """Build human-readable summary.

        Args:
            placeholders: All found placeholders
            auto_corrections: List of auto-corrections
            review_items: List of review items

        Returns:
            Summary string
        """
        total = len(placeholders)
        auto = len(auto_corrections)
        review = len(review_items)
        return f"Processed {total} images. {auto} auto-corrected, {review} need review."

    def _calculate_confidence(
        self,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> float:
        """Calculate overall confidence.

        Args:
            auto_corrections: List of auto-corrections
            review_items: List of review items

        Returns:
            Average confidence score
        """
        if not auto_corrections and not review_items:
            return 1.0

        all_confidences = [c.confidence for c in auto_corrections]
        all_confidences.extend([r.agent_confidence for r in review_items])

        return sum(all_confidences) / len(all_confidences) if all_confidences else 0.5


__all__ = ["FiguresAgent", "ImagePlaceholder"]
