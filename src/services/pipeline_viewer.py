"""Pipeline Viewer service — versioned step-by-step PDF processing.

Starts with Docling extraction (v0) and can grow one step at a time.
Each step reads the previous version's markdown and produces a new version.

Usage:
    service = PipelineViewerService()
    result = await service.process(file_content, filename, images_scale=2.0)
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import logging
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from .pipeline_viewer_models import (
    CandidateChange,
    CodeBlockInfo,
    DocumentChange,
    FeedbackItem,
    FeedbackItemType,
    FigureData,
    FootnoteInfo,
    HeadingReconciliationOutput,
    ImageDescriptionResult,
    OutlineEntry,
    PageAttributes,
    PageCorrectionResult,
    PipelineViewerResult,
    RevisionTask,
    RevisionTaskCategory,
    SectionEntry,
    SectionMap,
    StepResult,
    StructurePageOutput,
    StructureResult,
    TaskDecompositionResult,
    TextSelector,
)

logger = logging.getLogger(__name__)

PROCEDURES_DIR = Path(__file__).parent.parent / "agents" / "prompts" / "procedures"
MAX_STR_REPLACE_RETRIES = 3
PAGE_AGENT_SEMAPHORE_LIMIT = 3


def _pil_to_base64(image: object) -> str:
    """Convert a PIL Image to base64-encoded PNG string."""
    buf = BytesIO()
    image.save(buf, format="PNG")  # type: ignore[union-attr]
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _load_fragment(name: str) -> str:
    """Load a procedure fragment by relative name.

    Args:
        name: Fragment path relative to page_correction/, without .md extension.
              e.g. "base", "layout/double_column", "content/academic".
    """
    path = PROCEDURES_DIR / "page_correction" / f"{name}.md"
    if not path.exists():
        logger.warning(f"No fragment at {path}")
        return ""
    return path.read_text()


def _compose_page_prompt(attrs: PageAttributes) -> str:
    """Compose a correction prompt from page attribute fragments.

    Always includes the base fragment. Layout fragment is selected by
    attrs.layout. Boolean flags toggle content and quality fragments.
    """
    fragments = []

    base = _load_fragment("base")
    if base:
        fragments.append(base)

    layout = _load_fragment(f"layout/{attrs.layout.value}")
    if layout:
        fragments.append(layout)

    if attrs.is_academic:
        acad = _load_fragment("content/academic")
        if acad:
            fragments.append(acad)

    if attrs.has_images:
        img = _load_fragment("content/has_images")
        if img:
            fragments.append(img)

    if attrs.has_tables:
        tbl = _load_fragment("content/has_tables")
        if tbl:
            fragments.append(tbl)

    if attrs.has_equations:
        eq = _load_fragment("content/has_equations")
        if eq:
            fragments.append(eq)

    if attrs.is_scanned:
        scan = _load_fragment("quality/scanned")
        if scan:
            fragments.append(scan)

    return "\n\n".join(fragments)


def _fuzzy_find_line(lines: list[str], target: str, threshold: float = 0.6) -> int:
    """Find the line index best matching *target* via fuzzy match.

    Returns the index of the best match, or -1 if nothing exceeds *threshold*.
    """
    target_lower = target.strip().lower()
    if not target_lower:
        return -1
    best_idx = -1
    best_ratio = 0.0
    for i, line in enumerate(lines):
        line_lower = line.strip().lower()
        if not line_lower:
            continue
        ratio = difflib.SequenceMatcher(None, target_lower, line_lower).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i
    return best_idx if best_ratio >= threshold else -1


def _apply_code_block_fence(
    page_md: str,
    cb: CodeBlockInfo,
    page_num: int,
) -> tuple[str, DocumentChange | None]:
    """Locate a code block in *page_md* and wrap it in fences.

    Strategy:
      1. Check if the code is already inside a fenced block (```) — if so,
         just add the language tag if missing.
      2. Otherwise, fuzzy-find ``cb.first_line`` and ``cb.last_line`` among
         the markdown lines and wrap the region with ```language ... ```.

    Returns (new_markdown, change) or (original, None) if nothing matched.
    """
    lines = page_md.split("\n")

    # --- Case 1: already fenced (bare ```) -----------------------------------
    fence_pattern = re.compile(r"^```\s*$")
    skip_to = -1
    for i, line in enumerate(lines):
        if i <= skip_to:
            continue
        if not fence_pattern.match(line):
            continue
        # Find matching close fence
        for j in range(i + 1, len(lines)):
            if lines[j].strip() == "```":
                # Check if the content inside matches cb.first_line
                first_line_ratio = difflib.SequenceMatcher(
                    None,
                    cb.first_line.strip().lower(),
                    lines[i + 1].strip().lower() if i + 1 < len(lines) else "",
                ).ratio()
                if first_line_ratio >= 0.6:
                    # Tag the opening fence
                    old_fence = lines[i]
                    lines[i] = f"```{cb.language}"
                    return "\n".join(lines), DocumentChange(
                        page=page_num,
                        old_text=old_fence,
                        new_text=lines[i],
                        reasoning=(
                            f"Tagged existing code fence as {cb.language} "
                            f"(first line match {first_line_ratio:.0%})"
                        ),
                        stage="code_block",
                    )
                skip_to = j  # skip past this fenced block
                break  # close fence found but didn't match, keep looking

    # --- Case 2: unfenced code ------------------------------------------------
    start_idx = _fuzzy_find_line(lines, cb.first_line)
    if start_idx < 0:
        return page_md, None

    end_idx = _fuzzy_find_line(lines[start_idx:], cb.last_line)
    if end_idx < 0:
        # last_line not found — try using just the start line as a single-line block
        end_idx = 0
    end_idx += start_idx  # convert to absolute index

    # Ensure end >= start
    if end_idx < start_idx:
        end_idx = start_idx

    # Extract the code region
    code_lines = lines[start_idx : end_idx + 1]
    old_text = "\n".join(code_lines)

    # Build fenced replacement
    new_text = f"```{cb.language}\n{old_text}\n```"

    # Splice into lines
    new_lines = lines[:start_idx] + [new_text] + lines[end_idx + 1 :]

    return "\n".join(new_lines), DocumentChange(
        page=page_num,
        old_text=old_text[:200] + ("..." if len(old_text) > 200 else ""),
        new_text=new_text[:200] + ("..." if len(new_text) > 200 else ""),
        reasoning=(
            f"Wrapped unfenced code in ```{cb.language} fence "
            f"(structure analysis: {cb.reasoning})"
        ),
        stage="code_block",
    )


def _replace_image_placeholders(
    markdown: str,
    figures: list[FigureData],
) -> str:
    """Replace ``<!-- image -->`` placeholders with markdown image syntax.

    Docling emits one ``<!-- image -->`` placeholder per picture in document
    order.  The Nth placeholder corresponds to the Nth figure.  We walk through
    both lists sequentially: for each figure we replace the *first* remaining
    ``<!-- image -->`` with ``![caption](figures/ref_id.png)``.

    Uses a relative ``figures/`` path so the markdown is portable (works in ZIP
    bundles or relative to the S3 result prefix).

    If there are more placeholders than figures, the extras are left as-is.
    If there are more figures than placeholders (shouldn't happen), extras are
    ignored.
    """
    placeholder = "<!-- image -->"

    for fig in figures:
        idx = markdown.find(placeholder)
        if idx == -1:
            break
        alt = fig.caption.replace("]", "\\]") if fig.caption else ""
        image_ref = f"![{alt}](figures/{fig.ref_id}.png)"
        markdown = markdown[:idx] + image_ref + markdown[idx + len(placeholder) :]

    return markdown


def _detect_page_columns(doc: Any, page_no: int) -> str:
    """Infer column layout from Docling item bounding boxes.

    Analyses the horizontal positions of text items on a page to determine
    whether they form one column or two.  Uses median item width (robust
    against full-width titles/abstracts) and checks for items in both
    halves of the page.

    Returns:
        "double_column", "single_column", or "unknown".
    """
    page = doc.pages.get(page_no)
    if not page or not page.size or page.size.width <= 0:
        return "unknown"

    page_width = page.size.width

    item_widths: list[float] = []
    item_centers: list[float] = []

    for item, _level in doc.iterate_items(page_no=page_no):
        if not hasattr(item, "prov") or not item.prov:
            continue
        for p in item.prov:
            if p.page_no != page_no:
                continue
            bbox = p.bbox
            width = abs(bbox.r - bbox.l)
            # Skip very narrow items (footnote markers, page numbers, etc.)
            if width < page_width * 0.1:
                continue
            item_widths.append(width / page_width)
            item_centers.append(((bbox.l + bbox.r) / 2) / page_width)

    if len(item_widths) < 4:
        return "unknown"

    sorted_widths = sorted(item_widths)
    median_width = sorted_widths[len(sorted_widths) // 2]

    if median_width < 0.55:
        # Most items are narrow — verify items exist in both halves
        left_count = sum(1 for c in item_centers if c < 0.45)
        right_count = sum(1 for c in item_centers if c > 0.55)
        if left_count >= 2 and right_count >= 2:
            return "double_column"

    return "single_column"


def _is_running_header(line: str, doc_title: str, page_num: int) -> bool:
    """Detect if *line* is a running page header (repeated title at top of page).

    Never matches markdown headings (lines starting with #).
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False

    if doc_title:
        # Fuzzy-match against doc title (strip trailing digits/spaces)
        cleaned = stripped.lower().rstrip("0123456789 ")
        ratio = difflib.SequenceMatcher(None, cleaned, doc_title.lower()).ratio()
        if ratio >= 0.7:
            return True

    # Pattern: "{title text} {page_num}" at end of line
    m = re.match(r"^(.+?)\s+(\d+)\s*$", stripped)
    if m and int(m.group(2)) == page_num and len(m.group(1)) < 80:
        return True

    return False


def _is_running_footer(line: str, page_num: int) -> bool:
    """Detect if *line* is a running page footer (page number, etc.)."""
    stripped = line.strip()
    if not stripped:
        return False

    # Bare digit close to page number
    if stripped.isdigit() and abs(int(stripped) - page_num) <= 1:
        return True

    # "Page N" or "N of M"
    if re.match(r"^[Pp]age\s+\d+", stripped):
        return True
    if re.match(r"^\d+\s+of\s+\d+$", stripped):
        return True

    return False


class PipelineViewerService:
    """Versioned pipeline that stores full markdown at every step.

    v0: Docling extraction (raw PDF -> markdown)
    Phase 1: Structure analysis (metadata only — outline, page types, footnotes)
    v1: Page content corrections (parallel, procedure-loaded by doc type)
    v2: Cross-page fixes (boundaries + footnotes on assembled document)
    v3: Cleanup (deterministic)
    """

    async def process(
        self,
        file_content: bytes,
        filename: str,
        *,
        images_scale: float = 2.0,
        do_table_structure: bool = True,
        enable_structure: bool = False,
        enable_page_content: bool = False,
        enable_boundaries: bool = False,
    ) -> PipelineViewerResult:
        """Run the pipeline on a PDF.

        Args:
            file_content: Raw PDF bytes.
            filename: Original filename.
            images_scale: Scale factor for page image generation.
            do_table_structure: Whether to run Docling table structure recognition.
            enable_structure: Run Phase 1 structure analysis.
            enable_page_content: Run Phase 2 per-page corrections.
            enable_boundaries: Run Phase 3 cross-page fixes.

        Returns:
            PipelineViewerResult with versioned markdowns, images, figures, and stats.
        """
        result = PipelineViewerResult(filename=filename, total_pages=0)

        await self._step_docling(result, file_content, filename, images_scale, do_table_structure)

        structure: StructureResult | None = None

        if enable_structure:
            structure = await self._step_structure(result)
            structure = await self._step_heading_reconciliation(result, structure)
            await self._step_heading_levels(result, structure)

        section_map: SectionMap | None = None
        if enable_page_content and structure is not None:
            section_map = self._build_section_map(result, structure)
            page_hints = self._generate_page_hints(result, structure, section_map)
            await self._step_page_content(result, structure, page_hints=page_hints)
            await self._step_code_blocks(result, structure)

        if enable_boundaries and structure is not None:
            if section_map is None and structure is not None:
                section_map = self._build_section_map(result, structure)
            await self._step_boundaries(result, structure, section_map=section_map)
            await self._step_cleanup(result)

        return result

    async def _step_docling(
        self,
        result: PipelineViewerResult,
        file_content: bytes,
        filename: str,
        images_scale: float,
        do_table_structure: bool,
    ) -> None:
        """Run Docling PDF extraction, producing v0."""
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.document import DocumentStream  # type: ignore[attr-defined]
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        step_start = time.time()

        pipeline_options = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=do_table_structure,
            generate_page_images=True,
            generate_picture_images=True,
            images_scale=images_scale,
        )

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )

        pdf_stream = BytesIO(file_content)
        doc_stream = DocumentStream(name=filename, stream=pdf_stream)
        conv_result = await asyncio.to_thread(converter.convert, source=doc_stream)
        doc = conv_result.document

        result.total_pages = len(doc.pages)

        # Full document markdown -> v0
        try:
            full_md = doc.export_to_markdown()
        except Exception as e:
            logger.warning(f"Failed to export full markdown: {e}")
            full_md = ""

        result.versions["v0"] = full_md

        # Per-page markdown and images
        total_chars = 0
        page_mds: dict[str, str] = {}
        for page_no in sorted(doc.pages.keys()):
            page_md = doc.export_to_markdown(page_no=page_no)
            total_chars += len(page_md)
            page_key = str(page_no)
            page_mds[page_key] = page_md

            # Extract page image
            page = doc.pages[page_no]
            if page.image and hasattr(page.image, "pil_image") and page.image.pil_image:
                result.page_images[page_key] = _pil_to_base64(page.image.pil_image)

        result.page_markdowns["v0"] = page_mds

        # Extract figures
        for i, pic in enumerate(doc.pictures):
            if pic.image and hasattr(pic.image, "pil_image") and pic.image.pil_image:
                result.figures.append(
                    FigureData(
                        ref_id=f"figure-{i + 1}",
                        caption=pic.caption_text(doc=doc) or "",
                        page_number=pic.prov[0].page_no if pic.prov else 1,
                        image_base64=_pil_to_base64(pic.image.pil_image),
                    )
                )

        # Replace <!-- image --> placeholders with inline image refs in v0.
        # Done immediately so all downstream steps see image syntax.
        if result.figures:
            # Per-page replacement: filter figures by page_number
            for page_key, page_md in page_mds.items():
                page_figures = [f for f in result.figures if str(f.page_number) == page_key]
                if page_figures:
                    page_mds[page_key] = _replace_image_placeholders(page_md, page_figures)
            result.page_markdowns["v0"] = page_mds

            # Full document replacement
            result.versions["v0"] = _replace_image_placeholders(
                result.versions["v0"], result.figures
            )

        # Detect column layout per page from Docling bounding boxes
        layout_hints: dict[str, str] = {}
        for page_no in sorted(doc.pages.keys()):
            layout_hints[str(page_no)] = _detect_page_columns(doc, page_no)

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="docling",
                display_name="Docling Extraction",
                version_before=None,
                version_after="v0",
                elapsed_ms=elapsed_ms,
                changes=[],
                metadata={
                    "do_table_structure": do_table_structure,
                    "images_scale": images_scale,
                },
            )
        )

        # Compute stats
        chars_per_page = total_chars / result.total_pages if result.total_pages > 0 else 0
        is_likely_scanned = chars_per_page < 50 and result.total_pages > 0

        result.stats = {
            "total_chars": total_chars,
            "chars_per_page": round(chars_per_page, 1),
            "is_likely_scanned": is_likely_scanned,
            "figure_count": len(result.figures),
            "images_scale": images_scale,
            "do_table_structure": do_table_structure,
            "total_elapsed_ms": elapsed_ms,
            "layout_hints": layout_hints,
        }

    # ------------------------------------------------------------------
    # Phase 1 — Structure Analysis (sequential, analysis-only)
    # ------------------------------------------------------------------

    async def _step_structure(
        self,
        result: PipelineViewerResult,
    ) -> StructureResult:
        """Analyze document structure page by page.

        Processes pages sequentially, accumulating an outline. Each call
        receives the page image + markdown + outline-so-far and returns
        structural findings (headings, footnotes, page type).

        Does NOT modify the markdown — produces metadata consumed by
        later phases.

        Returns:
            StructureResult with outline, page types, and footnotes.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
        from ..agents.prompts.structure_analysis import (
            STRUCTURE_SYSTEM_PROMPT,
            build_structure_user_message,
        )

        step_start = time.time()

        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        agent: Agent[None, StructurePageOutput] = Agent(
            model=model,
            output_type=StructurePageOutput,
            system_prompt=STRUCTURE_SYSTEM_PROMPT,
        )

        structure = StructureResult()
        page_mds = result.page_markdowns["v0"]
        total_input_tokens = 0
        total_output_tokens = 0

        for page_num in range(1, result.total_pages + 1):
            page_key = str(page_num)
            page_md = page_mds.get(page_key, "")
            page_image_b64 = result.page_images.get(page_key)

            # Build text portion of user message
            outline_dicts = [e.model_dump() for e in structure.outline]
            layout_hints = result.stats.get("layout_hints", {})
            layout_hint = layout_hints.get(page_key)
            text_msg = build_structure_user_message(
                page_markdown=page_md,
                outline_so_far=outline_dicts,
                page_number=page_num,
                total_pages=result.total_pages,
                layout_hint=layout_hint,
            )

            # Build message list: image + text
            messages: list[Any] = []
            if page_image_b64:
                image_bytes = base64.b64decode(page_image_b64)
                messages.append(BinaryContent(data=image_bytes, media_type="image/png"))
            messages.append(text_msg)

            # Run agent for this page
            try:
                agent_result = await agent.run(messages)
                page_output = agent_result.output
                usage = agent_result.usage()
                total_input_tokens += usage.request_tokens or 0
                total_output_tokens += usage.response_tokens or 0
            except Exception as e:
                logger.error(f"Structure analysis failed on page {page_num}: {e}")
                continue

            # Accumulate results
            structure.page_attributes[page_num] = page_output.page_attributes

            for heading in page_output.headings:
                structure.outline.append(
                    OutlineEntry(
                        level=heading.recommended_level,
                        text=heading.text,
                        page=page_num,
                        reasoning=heading.reasoning,
                    )
                )

            for fn in page_output.footnotes:
                structure.footnotes.append(
                    FootnoteInfo(
                        number=fn.number,
                        body_text=fn.body_text,
                        source_page=page_num,
                    )
                )

            for cb in page_output.code_blocks:
                structure.code_blocks.append(
                    CodeBlockInfo(
                        language=cb.language,
                        first_line=cb.first_line,
                        last_line=cb.last_line,
                        page=page_num,
                        reasoning=cb.reasoning,
                    )
                )

            attrs = page_output.page_attributes
            logger.info(
                f"Structure page {page_num}/{result.total_pages}: "
                f"layout={attrs.layout.value}, "
                f"headings={len(page_output.headings)}, "
                f"footnotes={len(page_output.footnotes)}, "
                f"code_blocks={len(page_output.code_blocks)}"
            )

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

        # Phase 1 is analysis-only: no version change, just metadata
        result.steps.append(
            StepResult(
                name="structure",
                display_name="Structure Analysis",
                version_before="v0",
                version_after="v0",  # no markdown change
                elapsed_ms=elapsed_ms,
                changes=[],
                metadata={
                    "page_attributes": {
                        str(k): v.model_dump() for k, v in structure.page_attributes.items()
                    },
                    "outline": [e.model_dump() for e in structure.outline],
                    "footnotes": [f.model_dump() for f in structure.footnotes],
                    "code_blocks": [cb.model_dump() for cb in structure.code_blocks],
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

        return structure

    # ------------------------------------------------------------------
    # Phase 1b — Heading Reconciliation (single LLM call, all headings)
    # ------------------------------------------------------------------

    async def _step_heading_reconciliation(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> StructureResult:
        """Reconcile heading levels across the full document.

        The per-page structure analysis assigns heading levels based on
        limited context (one page + accumulated outline). This step reviews
        ALL heading candidates at once and assigns globally consistent levels.

        Returns:
            Updated StructureResult with reconciled outline.
        """
        from pydantic_ai import Agent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
        from ..agents.prompts.heading_reconciliation import (
            HEADING_RECONCILIATION_SYSTEM_PROMPT,
            build_heading_reconciliation_message,
        )

        step_start = time.time()

        if not structure.outline:
            result.steps.append(
                StepResult(
                    name="heading_reconciliation",
                    display_name="Heading Reconciliation",
                    version_before="v0",
                    version_after="v0",
                    elapsed_ms=0,
                    changes=[],
                    metadata={"headings_reviewed": 0, "headings_changed": 0},
                    skipped=True,
                )
            )
            return structure

        # Build heading candidates with reasoning from per-page analysis
        heading_candidates = []
        for entry in structure.outline:
            heading_candidates.append({
                "text": entry.text,
                "page": entry.page,
                "recommended_level": entry.level,
                "reasoning": entry.reasoning or f"Assigned h{entry.level} on page {entry.page}",
            })

        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        agent: Agent[None, HeadingReconciliationOutput] = Agent(
            model=model,
            output_type=HeadingReconciliationOutput,
            system_prompt=HEADING_RECONCILIATION_SYSTEM_PROMPT,
        )

        user_msg = build_heading_reconciliation_message(
            heading_candidates=heading_candidates,
            total_pages=result.total_pages,
        )

        total_input_tokens = 0
        total_output_tokens = 0
        changes: list[DocumentChange] = []

        try:
            agent_result = await agent.run(user_msg)
            reconciled = agent_result.output
            usage = agent_result.usage()
            total_input_tokens += usage.request_tokens or 0
            total_output_tokens += usage.response_tokens or 0

            # Compare old and new outlines, record changes
            old_by_key = {
                (e.text, e.page): e.level for e in structure.outline
            }
            for entry in reconciled.outline:
                key = (entry.text, entry.page)
                old_level = old_by_key.get(key)
                if old_level is not None and old_level != entry.level:
                    changes.append(
                        DocumentChange(
                            page=entry.page,
                            old_text=f"h{old_level}: {entry.text}",
                            new_text=f"h{entry.level}: {entry.text}",
                            reasoning=(
                                f"Reconciliation changed from h{old_level} to h{entry.level}"
                            ),
                            stage="heading_reconciliation",
                        )
                    )

            # Update the structure's outline with reconciled levels
            structure.outline = reconciled.outline

        except Exception as e:
            logger.error(f"Heading reconciliation failed: {e}")
            # Keep original outline on failure

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

        result.steps.append(
            StepResult(
                name="heading_reconciliation",
                display_name="Heading Reconciliation",
                version_before="v0",
                version_after="v0",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "headings_reviewed": len(heading_candidates),
                    "headings_changed": len(changes),
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

        return structure

    # ------------------------------------------------------------------
    # Phase 1c — Deterministic heading level fix
    # ------------------------------------------------------------------

    async def _step_heading_levels(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> None:
        """Fix heading levels in v0 markdown using outline metadata.

        For each page, scans markdown lines starting with ``#`` and fuzzy-
        matches against the outline entries for that page.  When a match is
        found the ``#`` prefix is replaced with the correct level.

        Mutates ``v0`` page markdowns in-place so that the page-content step
        reads already-corrected headings.  Produces no new version.
        """
        step_start = time.time()
        changes: list[DocumentChange] = []
        page_mds = result.page_markdowns["v0"]
        heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

        for page_num in range(1, result.total_pages + 1):
            page_key = str(page_num)
            page_md = page_mds.get(page_key, "")
            page_entries = [e for e in structure.outline if e.page == page_num]
            if not page_entries:
                continue

            lines = page_md.split("\n")
            changed = False
            for i, line in enumerate(lines):
                m = heading_re.match(line)
                if not m:
                    continue
                current_hashes = m.group(1)
                heading_text = m.group(2).strip()

                # Find best-matching outline entry via fuzzy match
                best_entry = None
                best_ratio = 0.0
                for entry in page_entries:
                    ratio = difflib.SequenceMatcher(
                        None, heading_text.lower(), entry.text.lower()
                    ).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_entry = entry

                if best_entry is None or best_ratio < 0.8:
                    continue

                correct_hashes = "#" * best_entry.level
                if current_hashes == correct_hashes:
                    continue

                old_line = line
                new_line = f"{correct_hashes} {heading_text}"
                lines[i] = new_line
                changed = True
                changes.append(
                    DocumentChange(
                        page=page_num,
                        old_text=old_line,
                        new_text=new_line,
                        reasoning=(
                            f"Outline says \"{best_entry.text}\" is level {best_entry.level} "
                            f"(was {'#' * len(current_hashes)} = level {len(current_hashes)}, "
                            f"match {best_ratio:.0%})"
                        ),
                        stage="heading_level",
                    )
                )

            if changed:
                page_mds[page_key] = "\n".join(lines)

        # Rebuild full v0 from corrected pages
        if changes:
            result.versions["v0"] = "\n\n".join(
                page_mds[str(p)] for p in range(1, result.total_pages + 1)
            )

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="heading_levels",
                display_name="Heading Levels",
                version_before="v0",
                version_after="v0",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "fixes_applied": len(changes),
                },
            )
        )

    # ------------------------------------------------------------------
    # Section Map Construction (deterministic, no LLM)
    # ------------------------------------------------------------------

    def _build_section_map(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> SectionMap:
        """Build a map of sections from the reconciled outline.

        Deterministic step — no LLM call. Concatenates all v0 page markdowns,
        finds heading positions, and splits the document at heading boundaries.

        Returns:
            SectionMap with ordered sections.
        """
        page_mds = result.page_markdowns["v0"]
        heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

        # Build full document from pages with page boundary markers
        # We need to track which page each line belongs to
        page_line_offsets: list[tuple[int, int]] = []  # (start_line, page_num)
        all_lines: list[str] = []
        for page_num in range(1, result.total_pages + 1):
            page_md = page_mds.get(str(page_num), "")
            page_line_offsets.append((len(all_lines), page_num))
            all_lines.extend(page_md.split("\n"))
            # Add blank line between pages (matching the \n\n join)
            if page_num < result.total_pages:
                all_lines.append("")

        full_text = "\n".join(all_lines)

        def _line_to_page(line_idx: int) -> int:
            """Map a line index to its page number."""
            result_page = 1
            for start_line, page_num in page_line_offsets:
                if start_line <= line_idx:
                    result_page = page_num
                else:
                    break
            return result_page

        # Find all headings in the full text with their positions
        heading_positions: list[tuple[int, int, str, int]] = []  # (char_pos, line_idx, text, level)
        for line_idx, line in enumerate(all_lines):
            m = heading_re.match(line)
            if m:
                level = len(m.group(1))
                text = m.group(2).strip()
                char_pos = sum(len(all_lines[j]) + 1 for j in range(line_idx))
                heading_positions.append((char_pos, line_idx, text, level))

        # Match headings to outline entries via fuzzy match
        matched_outline: list[tuple[int, int, str, int, int]] = []  # (char_pos, line_idx, text, level, outline_idx)
        used_outline_indices: set[int] = set()

        for char_pos, line_idx, text, level in heading_positions:
            best_idx = -1
            best_ratio = 0.0
            for oi, entry in enumerate(structure.outline):
                if oi in used_outline_indices:
                    continue
                ratio = difflib.SequenceMatcher(
                    None, text.lower(), entry.text.lower()
                ).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_idx = oi

            if best_idx >= 0 and best_ratio >= 0.6:
                used_outline_indices.add(best_idx)
                matched_outline.append((char_pos, line_idx, text, level, best_idx))

        # Build sections by splitting at heading boundaries
        sections: list[SectionEntry] = []
        section_idx = 0

        # Preamble: content before first heading
        if matched_outline:
            first_heading_line = matched_outline[0][1]
            if first_heading_line > 0:
                preamble_lines = all_lines[:first_heading_line]
                preamble_md = "\n".join(preamble_lines).strip()
                if preamble_md:
                    preamble_pages = sorted({
                        _line_to_page(li) for li in range(first_heading_line)
                        if all_lines[li].strip()
                    })
                    sections.append(SectionEntry(
                        index=section_idx,
                        heading_text="(Preamble)",
                        heading_level=0,
                        pages=preamble_pages or [1],
                        markdown=preamble_md,
                        outline_position=-1,
                    ))
                    section_idx += 1

            # Each heading starts a section
            for i, (_, line_idx, text, level, outline_idx) in enumerate(matched_outline):
                # Section ends at the next heading or end of document
                if i + 1 < len(matched_outline):
                    end_line = matched_outline[i + 1][1]
                else:
                    end_line = len(all_lines)

                section_lines = all_lines[line_idx:end_line]
                section_md = "\n".join(section_lines).strip()

                # Determine pages this section spans
                section_pages = sorted({
                    _line_to_page(li)
                    for li in range(line_idx, end_line)
                    if li < len(all_lines) and all_lines[li].strip()
                })
                if not section_pages:
                    section_pages = [_line_to_page(line_idx)]

                sections.append(SectionEntry(
                    index=section_idx,
                    heading_text=text,
                    heading_level=level,
                    pages=section_pages,
                    markdown=section_md,
                    outline_position=outline_idx,
                ))
                section_idx += 1
        else:
            # No headings found — entire document is one section
            full_md = full_text.strip()
            if full_md:
                sections.append(SectionEntry(
                    index=0,
                    heading_text="(Preamble)",
                    heading_level=0,
                    pages=list(range(1, result.total_pages + 1)),
                    markdown=full_md,
                    outline_position=-1,
                ))

        return SectionMap(sections=sections)

    # ------------------------------------------------------------------
    # Hint generation (programmatic edge-case detection)
    # ------------------------------------------------------------------

    def _generate_page_hints(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        section_map: SectionMap,
    ) -> dict[int, list[str]]:
        """Generate per-page context hints from programmatic edge-case detection.

        Returns:
            Mapping of page_num -> list of plain-text hint strings.
        """
        page_mds = result.page_markdowns.get("v0", {})
        hints: dict[int, list[str]] = {}

        # Determine document title (h1 from outline)
        doc_title = ""
        for entry in structure.outline:
            if entry.level == 1:
                doc_title = entry.text
                break

        # Build per-page section lookup
        page_to_sections: dict[int, list[SectionEntry]] = {}
        for section in section_map.sections:
            for p in section.pages:
                page_to_sections.setdefault(p, []).append(section)

        for page_num in range(1, result.total_pages + 1):
            page_md = page_mds.get(str(page_num), "")
            page_hints: list[str] = []
            lines = page_md.split("\n")
            non_blank = [ln for ln in lines if ln.strip()]

            if not non_blank:
                continue

            # --- Running header (page > 1 only) ---
            if page_num > 1:
                first_line = non_blank[0]
                if _is_running_header(first_line, doc_title, page_num):
                    page_hints.append(
                        f"First line '{first_line.strip()}' appears to be a "
                        f"running page header, not document content."
                    )

            # --- Running footer ---
            last_line = non_blank[-1]
            if _is_running_footer(last_line, page_num):
                page_hints.append(
                    f"Last line '{last_line.strip()}' appears to be a "
                    f"page number footer."
                )

            # --- Section context ---
            sections_on_page = page_to_sections.get(page_num, [])
            for section in sections_on_page:
                if section.heading_level == 0:
                    continue  # skip preamble
                if len(section.pages) == 1:
                    position = "only page"
                elif page_num == section.pages[0]:
                    position = "start of section"
                elif page_num == section.pages[-1]:
                    position = "end of section"
                else:
                    position = "middle of section"
                page_hints.append(
                    f"This page is in section '{section.heading_text}' "
                    f"(h{section.heading_level}, pages {section.pages[0]}-"
                    f"{section.pages[-1]}). You are on page {page_num} "
                    f"({position})."
                )

            # --- Mid-sentence start ---
            first_content = ""
            for ln in non_blank:
                if not ln.strip().startswith("#"):
                    first_content = ln.strip()
                    break
            if first_content and first_content[0].islower():
                page_hints.append(
                    "This page starts mid-sentence (continues from previous "
                    "page). Do not fix the incomplete start."
                )

            # --- Mid-sentence end ---
            last_content = non_blank[-1].strip()
            if last_content and last_content[-1] not in ".!?:":
                # Don't flag headings or list items as mid-sentence
                if not last_content.startswith("#") and not last_content.startswith("- "):
                    page_hints.append(
                        "This page ends mid-sentence. Content continues on "
                        "next page."
                    )

            # --- Expected footnotes ---
            page_footnotes = [
                fn for fn in structure.footnotes if fn.source_page == page_num
            ]
            if page_footnotes:
                markers = ", ".join(f"[{fn.number}]" for fn in page_footnotes)
                page_hints.append(
                    f"Footnotes {markers} are expected on this page."
                )

            if page_hints:
                hints[page_num] = page_hints

        return hints

    def _generate_boundary_hints(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        section_map: SectionMap,
    ) -> list[dict]:
        """Build boundary snippets with section-aware hints.

        Returns a list of boundary snippet dicts, each with:
            page_before, page_after, tail_text, head_text, hints: list[str]
        """
        source_version = "v1" if "v1" in result.page_markdowns else "v0"
        page_mds = result.page_markdowns.get(source_version, {})

        # Determine document title (h1 from outline)
        doc_title = ""
        for entry in structure.outline:
            if entry.level == 1:
                doc_title = entry.text
                break

        # Build per-page section lookup
        page_to_sections: dict[int, list[SectionEntry]] = {}
        for section in section_map.sections:
            for p in section.pages:
                page_to_sections.setdefault(p, []).append(section)

        snippets: list[dict] = []

        for page_num in range(1, result.total_pages):
            page_before = page_num
            page_after = page_num + 1

            md_before = page_mds.get(str(page_before), "")
            md_after = page_mds.get(str(page_after), "")

            lines_before = md_before.split("\n")
            lines_after = md_after.split("\n")

            tail_text = "\n".join(lines_before[-5:])
            head_text = "\n".join(lines_after[:5])

            boundary_hints: list[str] = []

            # --- Section context ---
            sections_before = page_to_sections.get(page_before, [])
            sections_after = page_to_sections.get(page_after, [])

            # Check if same section spans both pages
            shared = [s for s in sections_before if s in sections_after]
            if shared:
                sec = shared[0]
                boundary_hints.append(
                    f"SAME SECTION ('{sec.heading_text}'): content flows "
                    f"continuously. Join split words/sentences."
                )
            else:
                # Section transition
                if sections_before and sections_after:
                    sec_a = sections_before[-1]
                    sec_b = sections_after[0]
                    if sec_a.heading_level > 0 and sec_b.heading_level > 0:
                        boundary_hints.append(
                            f"SECTION TRANSITION: '{sec_a.heading_text}' ends "
                            f"→ '{sec_b.heading_text}' begins. This is a "
                            f"natural break."
                        )

            # --- Running header at start of page_after ---
            non_blank_after = [ln for ln in lines_after if ln.strip()]
            if non_blank_after:
                first_line = non_blank_after[0]
                if _is_running_header(first_line, doc_title, page_after):
                    boundary_hints.append(
                        f"Running header: '{first_line.strip()}' at start of "
                        f"page {page_after}. Remove this line."
                    )

            # --- Running footer at end of page_before ---
            non_blank_before = [ln for ln in lines_before if ln.strip()]
            if non_blank_before:
                last_line = non_blank_before[-1]
                if _is_running_footer(last_line, page_before):
                    boundary_hints.append(
                        f"Page number footer: '{last_line.strip()}' at end of "
                        f"page {page_before}. Remove this line."
                    )

            snippets.append({
                "page_before": page_before,
                "page_after": page_after,
                "tail_text": tail_text,
                "head_text": head_text,
                "hints": boundary_hints,
            })

        return snippets

    # ------------------------------------------------------------------
    # Image description subagent
    # ------------------------------------------------------------------

    async def _run_image_describer(
        self,
        figure: FigureData,
        current_markdown: str,
        page_images: dict[int, str | None],
        describer_tokens: dict[str, int],
        update_tokens: Any,
    ) -> str:
        """Spawn a vision subagent to generate alt text for a single figure.

        Returns a formatted instruction string for the section agent.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
        from ..agents.prompts.image_description import (
            IMAGE_DESCRIBER_SYSTEM_PROMPT,
            build_describer_user_message,
        )

        # Extract surrounding context (~200 chars around the figure reference)
        ref_pattern = f"figures/{figure.ref_id}"
        idx = current_markdown.find(ref_pattern)
        if idx >= 0:
            start = max(0, idx - 200)
            end = min(len(current_markdown), idx + len(ref_pattern) + 200)
            surrounding_text = current_markdown[start:end]
        else:
            surrounding_text = ""

        user_message = build_describer_user_message(
            caption=figure.caption,
            surrounding_text=surrounding_text,
            ref_id=figure.ref_id,
        )

        # Build multimodal user message: figure image + optional page image + text
        user_parts: list[Any] = []

        # Attach the extracted figure image
        if figure.image_base64:
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(figure.image_base64),
                    media_type="image/png",
                )
            )

        # Attach the full page image for broader context
        page_img = page_images.get(figure.page_number)
        if page_img:
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(page_img),
                    media_type="image/png",
                )
            )

        user_parts.append(user_message)

        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        describer_agent: Agent[None, ImageDescriptionResult] = Agent(
            model=model,
            output_type=ImageDescriptionResult,
            system_prompt=IMAGE_DESCRIBER_SYSTEM_PROMPT,
        )

        try:
            desc_result = await describer_agent.run(user_parts)
            usage = desc_result.usage()
            update_tokens(
                usage.request_tokens or 0,
                usage.response_tokens or 0,
            )

            result = desc_result.output
            if result.is_decorative:
                return (
                    f"Figure '{figure.ref_id}' is decorative. "
                    f"No alt text needed — leave the alt attribute empty."
                )
            return (
                f"Use this alt text for '{figure.ref_id}': {result.alt_text}\n"
                f"Use str_replace to update the markdown image syntax to include this alt text."
            )
        except Exception as e:
            logger.warning(f"Image describer failed for {figure.ref_id}: {e}")
            return f"ERROR: Image describer failed for '{figure.ref_id}': {e}"

    # ------------------------------------------------------------------
    # Phase 2 — Page Content Corrections (parallel, semaphore-limited)
    # ------------------------------------------------------------------

    async def _step_page_content(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        *,
        page_hints: dict[int, list[str]] | None = None,
    ) -> None:
        """Correct each page's markdown against its page image.

        Runs page agents in parallel (up to PAGE_AGENT_SEMAPHORE_LIMIT
        concurrent). Each agent receives the page image, page markdown,
        document outline, and a procedure file based on the page's
        document type.

        Uses str_replace tool calls for surgical edits. Failed edits are
        retried up to MAX_STR_REPLACE_RETRIES times with the current
        markdown state reported back to the agent.

        Produces v1: corrected per-page markdowns.
        """


        step_start = time.time()
        all_changes: list[DocumentChange] = []
        all_issues: list[str] = []
        page_mds = dict(result.page_markdowns["v0"])  # copy to mutate
        semaphore = asyncio.Semaphore(PAGE_AGENT_SEMAPHORE_LIMIT)

        # Build per-page figure lookup
        page_figures_map: dict[int, list[FigureData]] = {}
        for fig in result.figures:
            page_figures_map.setdefault(fig.page_number, []).append(fig)

        async def _process_page(page_num: int) -> PageCorrectionResult:
            pf = page_figures_map.get(page_num) or None
            async with semaphore:
                return await self._run_page_agent(
                    page_num=page_num,
                    page_markdown=page_mds[str(page_num)],
                    page_image_b64=result.page_images.get(str(page_num)),
                    structure=structure,
                    context_hints=page_hints.get(page_num) if page_hints else None,
                    page_figures=pf,
                )

        # Fan out all pages
        tasks = [_process_page(p) for p in range(1, result.total_pages + 1)]
        page_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        corrected_mds: dict[str, str] = {}
        total_input_tokens = 0
        total_output_tokens = 0
        total_describer_input = 0
        total_describer_output = 0
        for i, pr in enumerate(page_results):
            page_num = i + 1
            page_key = str(page_num)

            if isinstance(pr, Exception):
                logger.error(f"Page {page_num} agent failed: {pr}")
                corrected_mds[page_key] = page_mds[page_key]  # keep original
                all_issues.append(f"Page {page_num}: agent error — {pr}")
                continue

            corrected_mds[page_key] = pr.corrected_markdown
            all_changes.extend(pr.changes)
            all_issues.extend(pr.issues)
            total_input_tokens += pr.input_tokens
            total_output_tokens += pr.output_tokens
            total_describer_input += pr.describer_input_tokens
            total_describer_output += pr.describer_output_tokens

        # Write v1
        result.page_markdowns["v1"] = corrected_mds
        result.versions["v1"] = "\n\n".join(
            corrected_mds[str(p)] for p in range(1, result.total_pages + 1)
        )

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        combined_input = total_input_tokens + total_describer_input
        combined_output = total_output_tokens + total_describer_output
        cost_cents = calculate_estimated_cost(combined_input, combined_output)

        result.steps.append(
            StepResult(
                name="page_content",
                display_name="Page Content Corrections",
                version_before="v0",
                version_after="v1",
                elapsed_ms=elapsed_ms,
                changes=all_changes,
                metadata={
                    "pages_with_changes": len(
                        {c.page for c in all_changes}
                    ),
                    "total_changes": len(all_changes),
                    "issues": all_issues,
                    "describer_input_tokens": total_describer_input,
                    "describer_output_tokens": total_describer_output,
                },
                input_tokens=combined_input,
                output_tokens=combined_output,
                cost_cents=cost_cents,
            )
        )

    async def _run_page_agent(
        self,
        page_num: int,
        page_markdown: str,
        page_image_b64: str | None,
        structure: StructureResult,
        context_hints: list[str] | None = None,
        page_figures: list[FigureData] | None = None,
    ) -> PageCorrectionResult:
        """Run a single page correction agent.

        Sets up a PydanticAI agent with str_replace and no_changes tools,
        loads the appropriate procedure based on document type, and runs
        the agent. Failed str_replace calls are reported back to the agent
        for retry.

        When ``page_figures`` is provided and the page has images, a
        ``describe_image`` tool is registered for WCAG-compliant alt text.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier

        # Compose procedure from page attributes
        page_attrs = structure.page_attributes.get(page_num)
        procedure = _compose_page_prompt(page_attrs) if page_attrs else ""

        # Mutable state for str_replace tool
        current_markdown = page_markdown
        changes: list[DocumentChange] = []
        issues: list[str] = []

        # Build system prompt
        base_prompt = (
            "You are a document correction agent. Compare the page image "
            "(visual ground truth) against the markdown and fix discrepancies.\n\n"
            "Use the str_replace tool for each correction. Use no_changes if "
            "the page is already correct.\n\n"
            "Do NOT fix word fragments at the very start or end of the page — "
            "a later step handles cross-page joins.\n"
            "Do NOT relocate footnote bodies — a later step handles that.\n"
        )
        if procedure:
            base_prompt += f"\n{procedure}\n"

        # ------------------------------------------------------------------
        # describe_image tool: closure state
        # ------------------------------------------------------------------
        max_describe_calls = 10
        figure_lookup: dict[str, FigureData] = {}
        describer_input_tokens = 0
        describer_output_tokens = 0
        describe_call_count = 0

        enable_describe = bool(
            page_attrs and page_attrs.has_images and page_figures
        )
        if enable_describe:
            figure_lookup = {f.ref_id: f for f in (page_figures or [])}
            available_refs = sorted(figure_lookup.keys())
            base_prompt += (
                f"\n## Available figures for describe_image\n"
                f"The following figure ref_ids are available: {', '.join(available_refs)}\n"
                f"Call `describe_image` for each one, then use `str_replace` to "
                f"update the alt text.\n"
            )

        # Create agent with tools
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        agent: Agent[None, None] = Agent(
            model=model,
            output_type=None,  # output comes through tool calls
            system_prompt=base_prompt,
        )

        @agent.tool_plain
        def str_replace(old_text: str, new_text: str, reasoning: str, category: str) -> str:
            """Replace exact text in the page markdown.

            Args:
                old_text: Exact text to find. Must appear once in the page
                    region. Include surrounding context for uniqueness.
                new_text: Corrected text. Must differ from old_text.
                reasoning: Why this change is needed. Reference what the
                    image shows vs what the markdown has.
                category: Type of correction: ocr_error, formatting,
                    heading_level, list_structure, character_encoding, other.
            """
            nonlocal current_markdown

            count = current_markdown.count(old_text)
            if count == 0:
                return (
                    f"ERROR: old_text not found in page markdown.\n\n"
                    f"You searched for:\n  {old_text!r}\n\n"
                    f"Current page markdown:\n---\n{current_markdown}\n---\n\n"
                    f"Review the current markdown and try again."
                )
            if count > 1:
                return (
                    f"ERROR: old_text found {count} times. Include more "
                    f"surrounding context to make it unique."
                )

            current_markdown = current_markdown.replace(old_text, new_text, 1)
            changes.append(
                DocumentChange(
                    page=page_num,
                    old_text=old_text,
                    new_text=new_text,
                    reasoning=reasoning,
                    stage=category,
                )
            )
            return f"OK — replaced on page {page_num}."

        @agent.tool_plain
        def no_changes(confidence: str, notes: str = "") -> str:
            """Affirm that the page markdown matches the image.

            Args:
                confidence: How confident: "high", "medium", or "low".
                notes: Optional observations about uncertain areas.
            """
            if notes:
                issues.append(f"Page {page_num} ({confidence}): {notes}")
            return "Acknowledged — no changes needed."

        # ------------------------------------------------------------------
        # Conditionally register describe_image tool
        # ------------------------------------------------------------------
        if enable_describe:
            @agent.tool_plain
            async def describe_image(ref_id: str) -> str:
                """Generate WCAG-compliant alt text for a figure.

                Args:
                    ref_id: The figure reference ID (e.g. "figure-1.png").
                        Must match one of the available figure ref_ids.
                """
                nonlocal describer_input_tokens, describer_output_tokens
                nonlocal describe_call_count

                if describe_call_count >= max_describe_calls:
                    return (
                        f"ERROR: Maximum describe_image calls ({max_describe_calls}) "
                        f"reached. Skip remaining figures."
                    )

                describe_call_count += 1

                if ref_id not in figure_lookup:
                    available = sorted(figure_lookup.keys())
                    return (
                        f"ERROR: ref_id '{ref_id}' not found. "
                        f"Available ref_ids: {', '.join(available)}"
                    )

                figure = figure_lookup[ref_id]
                return await self._run_image_describer(
                    figure=figure,
                    current_markdown=current_markdown,
                    page_images={page_num: page_image_b64},
                    describer_tokens={
                        "input": describer_input_tokens,
                        "output": describer_output_tokens,
                    },
                    update_tokens=lambda inp, out: _update_page_describer_tokens(inp, out),
                )

            def _update_page_describer_tokens(inp: int, out: int) -> None:
                nonlocal describer_input_tokens, describer_output_tokens
                describer_input_tokens += inp
                describer_output_tokens += out

        # Build user message
        outline_text = "\n".join(
            f"  {'#' * e.level} {e.text} (p{e.page})"
            for e in structure.outline
        )
        user_parts: list[Any] = []
        if page_image_b64:
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(page_image_b64),
                    media_type="image/png",
                )
            )
        # Build hints section if present
        hints_block = ""
        if context_hints:
            hints_lines = "\n".join(f"- {h}" for h in context_hints)
            hints_block = f"### Context hints\n{hints_lines}\n\n"

        user_parts.append(
            f"## Page {page_num}\n\n"
            f"### Document outline\n{outline_text}\n\n"
            f"{hints_block}"
            f"### Page markdown\n```\n{page_markdown}\n```\n\n"
            f"Compare the image against the markdown. Make corrections "
            f"with str_replace, or call no_changes if the page is correct."
        )

        # Run agent
        agent_result = await agent.run(user_parts)
        usage = agent_result.usage()

        return PageCorrectionResult(
            page=page_num,
            corrected_markdown=current_markdown,
            changes=changes,
            issues=issues,
            input_tokens=usage.request_tokens or 0,
            output_tokens=usage.response_tokens or 0,
            describer_input_tokens=describer_input_tokens,
            describer_output_tokens=describer_output_tokens,
        )

    # ------------------------------------------------------------------
    # Phase 2b — Deterministic code block language tagging
    # ------------------------------------------------------------------

    async def _step_code_blocks(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> None:
        """Wrap and tag code blocks using structure analysis metadata.

        Docling often renders code as plain unfenced text. This step uses the
        ``first_line`` / ``last_line`` reported by the structure agent to
        locate the code region in the markdown and wrap it in proper fences
        with the detected language.

        Also handles already-fenced blocks that lack a language tag.

        Edits whichever per-page version is newest (v1 if exists, else v0)
        in-place.
        """
        step_start = time.time()
        changes: list[DocumentChange] = []

        source_version = "v1" if "v1" in result.page_markdowns else "v0"

        if not structure.code_blocks:
            result.steps.append(
                StepResult(
                    name="code_blocks",
                    display_name="Code Block Languages",
                    version_before=source_version,
                    version_after=source_version,
                    elapsed_ms=0,
                    changes=[],
                    metadata={"blocks_tagged": 0},
                )
            )
            return

        page_mds = result.page_markdowns[source_version]

        for cb in structure.code_blocks:
            page_key = str(cb.page)
            page_md = page_mds.get(page_key, "")
            if not page_md:
                continue

            new_md, change = _apply_code_block_fence(
                page_md, cb, page_num=cb.page
            )
            if change:
                page_mds[page_key] = new_md
                changes.append(change)

        # Rebuild full version from corrected pages
        if changes:
            result.versions[source_version] = "\n\n".join(
                page_mds[str(p)] for p in range(1, result.total_pages + 1)
            )

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="code_blocks",
                display_name="Code Block Languages",
                version_before=source_version,
                version_after=source_version,
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "blocks_tagged": len(changes),
                },
            )
        )

    # ------------------------------------------------------------------
    # Phase 3 — Cross-page fixes (on assembled document)
    # ------------------------------------------------------------------

    async def _step_boundaries(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        *,
        section_map: SectionMap | None = None,
    ) -> None:
        """Fix cross-page issues on the assembled document.

        1. Deterministic hyphen-join for residual page-join artifacts
        2. Boundary agent with section-aware hints (running headers/footers,
           same-section joins, section transitions)
        3. Footnote relocation (move footnote bodies to endnotes)

        Produces v2.
        """
        source_version = "v1" if "v1" in result.versions else "v0"

        step_start = time.time()
        changes: list[DocumentChange] = []
        issues: list[str] = []
        total_input_tokens = 0
        total_output_tokens = 0

        current_document = result.versions.get(source_version, "")

        # --- Deterministic: hyphen-join for residual page-join artifacts ---
        hyphen_re = re.compile(r"([a-z])-\n\n([a-z])")
        before = current_document
        current_document = hyphen_re.sub(r"\1\2", current_document)
        if current_document != before:
            # Count the number of joins made
            join_count = len(hyphen_re.findall(before))
            changes.append(
                DocumentChange(
                    page=0,
                    old_text="(hyphenated line breaks)",
                    new_text="(joined words)",
                    reasoning=f"Joined {join_count} hyphenated word(s) split across page boundaries",
                    stage="boundary_fix",
                )
            )

        # --- Agent: Boundary fix with section-aware hints ---
        if result.total_pages > 1:
            if section_map is not None:
                boundary_snippets = self._generate_boundary_hints(
                    result, structure, section_map
                )
            else:
                # Fallback: basic boundary snippets without hints
                page_mds = result.page_markdowns.get(source_version, {})
                boundary_snippets = []
                for p in range(1, result.total_pages):
                    md_before = page_mds.get(str(p), "")
                    md_after = page_mds.get(str(p + 1), "")
                    boundary_snippets.append({
                        "page_before": p,
                        "page_after": p + 1,
                        "tail_text": "\n".join(md_before.split("\n")[-5:]),
                        "head_text": "\n".join(md_after.split("\n")[:5]),
                        "hints": [],
                    })

            footnote_numbers = [fn.number for fn in structure.footnotes]
            current_document, bd_changes, bd_issues, bd_in, bd_out = (
                await self._run_boundary_agent(
                    current_document,
                    boundary_snippets,
                    footnote_numbers,
                )
            )
            changes.extend(bd_changes)
            issues.extend(bd_issues)
            total_input_tokens += bd_in
            total_output_tokens += bd_out

        # --- Agent: Footnote relocation (kept for orphan footnotes) ---
        if structure.footnotes:
            current_document, fn_changes, fn_issues, fn_in, fn_out = (
                await self._run_footnote_agent(
                    current_document,
                    structure.footnotes,
                )
            )
            changes.extend(fn_changes)
            issues.extend(fn_issues)
            total_input_tokens += fn_in
            total_output_tokens += fn_out

        result.versions["v2"] = current_document

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

        result.steps.append(
            StepResult(
                name="boundaries",
                display_name="Cross-Page Fixes",
                version_before=source_version,
                version_after="v2",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "footnotes_to_relocate": len(structure.footnotes),
                    "footnotes_relocated": len(
                        [c for c in changes if c.stage == "footnote"]
                    ),
                    "boundary_fixes": len(
                        [c for c in changes if c.stage == "boundary_fix"]
                    ),
                    "issues": issues,
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

    async def _run_boundary_agent(
        self,
        document: str,
        boundary_snippets: list[dict],
        footnote_numbers: list[str],
    ) -> tuple[str, list[DocumentChange], list[str], int, int]:
        """Run the boundary fix agent on the assembled document.

        Returns:
            Tuple of (updated document, changes, issues, input_tokens, output_tokens).
        """
        from pydantic_ai import Agent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
        from ..agents.prompts.boundary_fix import (
            BOUNDARY_FIX_SYSTEM_PROMPT,
            build_boundary_user_message,
        )

        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

        current_document = document
        changes: list[DocumentChange] = []
        issues: list[str] = []

        agent: Agent[None, None] = Agent(
            model=model,
            output_type=None,
            system_prompt=BOUNDARY_FIX_SYSTEM_PROMPT,
        )

        @agent.tool_plain
        def str_replace(old_text: str, new_text: str, reasoning: str, category: str) -> str:
            """Replace exact text in the document.

            Args:
                old_text: Exact text to find. Must appear once.
                new_text: Corrected text.
                reasoning: Why this change is needed.
                category: Should be "boundary_fix".
            """
            nonlocal current_document

            count = current_document.count(old_text)
            if count == 0:
                return (
                    f"ERROR: old_text not found in document.\n\n"
                    f"You searched for:\n  {old_text!r}\n\n"
                    f"Review the document and try again."
                )
            if count > 1:
                return (
                    f"ERROR: old_text found {count} times. Include more "
                    f"surrounding context to make it unique."
                )

            current_document = current_document.replace(old_text, new_text, 1)
            changes.append(
                DocumentChange(
                    page=0,
                    old_text=old_text,
                    new_text=new_text,
                    reasoning=reasoning,
                    stage="boundary_fix",
                )
            )
            return "OK — replacement applied."

        @agent.tool_plain
        def no_changes(confidence: str, notes: str = "") -> str:
            """Affirm that no boundary fixes are needed.

            Args:
                confidence: How confident: "high", "medium", or "low".
                notes: Optional observations.
            """
            if notes:
                issues.append(f"Boundary agent ({confidence}): {notes}")
            return "Acknowledged — no boundary fixes needed."

        user_msg = build_boundary_user_message(
            assembled_document=document,
            boundary_snippets=boundary_snippets,
            footnote_numbers=footnote_numbers,
        )

        input_tokens = 0
        output_tokens = 0
        try:
            agent_result = await agent.run(user_msg)
            usage = agent_result.usage()
            input_tokens = usage.request_tokens or 0
            output_tokens = usage.response_tokens or 0
        except Exception as e:
            logger.error(f"Boundary fix agent failed: {e}")
            issues.append(f"Boundary agent error: {e}")

        return current_document, changes, issues, input_tokens, output_tokens

    async def _run_footnote_agent(
        self,
        document: str,
        footnotes: list[FootnoteInfo],
    ) -> tuple[str, list[DocumentChange], list[str], int, int]:
        """Run the footnote relocation agent on the document.

        Returns:
            Tuple of (updated document, changes, issues, input_tokens, output_tokens).
        """
        from pydantic_ai import Agent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
        from ..agents.prompts.footnote_relocation import (
            FOOTNOTE_RELOCATION_SYSTEM_PROMPT,
            build_footnote_user_message,
        )

        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

        current_document = document
        changes: list[DocumentChange] = []
        issues: list[str] = []

        agent: Agent[None, None] = Agent(
            model=model,
            output_type=None,
            system_prompt=FOOTNOTE_RELOCATION_SYSTEM_PROMPT,
        )

        @agent.tool_plain
        def str_replace(old_text: str, new_text: str, reasoning: str, category: str) -> str:
            """Replace exact text in the document.

            Args:
                old_text: Exact text to find. Must appear once.
                new_text: Corrected text.
                reasoning: Why this change is needed.
                category: Should be "footnote".
            """
            nonlocal current_document

            count = current_document.count(old_text)
            if count == 0:
                return (
                    f"ERROR: old_text not found in document.\n\n"
                    f"You searched for:\n  {old_text!r}\n\n"
                    f"Review the document and try again."
                )
            if count > 1:
                return (
                    f"ERROR: old_text found {count} times. Include more "
                    f"surrounding context to make it unique."
                )

            current_document = current_document.replace(old_text, new_text, 1)
            changes.append(
                DocumentChange(
                    page=0,
                    old_text=old_text,
                    new_text=new_text,
                    reasoning=reasoning,
                    stage="footnote",
                )
            )
            return "OK — replacement applied."

        @agent.tool_plain
        def no_changes(confidence: str, notes: str = "") -> str:
            """Affirm that no footnote relocation is needed.

            Args:
                confidence: How confident: "high", "medium", or "low".
                notes: Optional observations.
            """
            if notes:
                issues.append(f"Footnote agent ({confidence}): {notes}")
            return "Acknowledged — no footnotes to relocate."

        footnote_dicts = [fn.model_dump() for fn in footnotes]
        user_msg = build_footnote_user_message(
            document=document,
            footnotes=footnote_dicts,
        )

        input_tokens = 0
        output_tokens = 0
        try:
            agent_result = await agent.run(user_msg)
            usage = agent_result.usage()
            input_tokens = usage.request_tokens or 0
            output_tokens = usage.response_tokens or 0
        except Exception as e:
            logger.error(f"Footnote relocation agent failed: {e}")
            issues.append(f"Footnote agent error: {e}")

        return current_document, changes, issues, input_tokens, output_tokens

    async def _step_cleanup(self, result: PipelineViewerResult) -> None:
        """Deterministic cleanup on the assembled document.

        - Collapse 3+ blank lines to 2
        - Strip trailing whitespace
        - Remove orphan page numbers

        Produces v3.
        """
        import re

        step_start = time.time()
        source = result.versions.get("v2", "")
        changes: list[DocumentChange] = []

        cleaned = source

        # Collapse 3+ consecutive blank lines to 2
        before = cleaned
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        if cleaned != before:
            changes.append(
                DocumentChange(
                    page=0,
                    old_text="(multiple locations)",
                    new_text="(collapsed)",
                    reasoning="Collapsed 3+ consecutive blank lines to standard paragraph breaks",
                    stage="deterministic",
                )
            )

        # Strip trailing whitespace from each line
        before = cleaned
        cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
        if cleaned != before:
            changes.append(
                DocumentChange(
                    page=0,
                    old_text="(multiple locations)",
                    new_text="(stripped)",
                    reasoning="Removed trailing whitespace from lines",
                    stage="deterministic",
                )
            )

        result.versions["v3"] = cleaned

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="cleanup",
                display_name="Final Cleanup",
                version_before="v2",
                version_after="v3",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={},
            )
        )

    # ------------------------------------------------------------------
    # Human Review — Revision
    # ------------------------------------------------------------------

    async def decompose_feedback(
        self,
        feedback: str,
        result: PipelineViewerResult,
        structure: StructureResult | None,
        feedback_history: list[str] | None = None,
    ) -> TaskDecompositionResult:
        """Decompose freeform feedback into discrete revision tasks.

        Public method so the API can call it before run_revision and emit
        SSE events between stages.

        Args:
            feedback: Freeform reviewer feedback.
            result: Current pipeline result.
            structure: Structure analysis result (for outline + page attrs).
            feedback_history: Previous rounds of feedback (if any).

        Returns:
            TaskDecompositionResult with tasks and reasoning.
        """
        return await self._decompose_feedback(
            feedback, result, structure, feedback_history
        )

    async def run_revision(
        self,
        result: PipelineViewerResult,
        structure: StructureResult | None,
        tasks: list[RevisionTask],
        feedback: str,
        revision_round: int,
    ) -> StepResult:
        """Run a revision pass using pre-decomposed tasks.

        Groups tasks by page, runs revision agents in parallel on affected
        pages with selective image attachment, and assembles a new version.

        Args:
            result: Current pipeline result (mutated in place).
            structure: Structure analysis result (for outline context).
            tasks: Pre-decomposed revision tasks.
            feedback: Original freeform feedback (for metadata).
            revision_round: 1-based revision round number.

        Returns:
            The StepResult for this revision.
        """
        step_start = time.time()

        # Determine the latest version key (highest numeric suffix)
        version_nums = [
            int(k[1:]) for k in result.versions if k.startswith("v") and k[1:].isdigit()
        ]
        latest_num = max(version_nums) if version_nums else 0
        source_version = f"v{latest_num}"
        new_version = f"v{latest_num + 1}"

        # Get per-page markdowns — fall back to splitting full doc
        source_pages = result.page_markdowns.get(source_version)
        if source_pages is None:
            for v in sorted(version_nums, reverse=True):
                vk = f"v{v}"
                if vk in result.page_markdowns:
                    source_pages = dict(result.page_markdowns[vk])
                    break
            if source_pages is None:
                source_pages = dict(result.page_markdowns.get("v0", {}))

        # Group tasks by page
        page_tasks: dict[int, list[RevisionTask]] = {}
        for task in tasks:
            for page_num in task.affected_pages:
                if 1 <= page_num <= result.total_pages:
                    page_tasks.setdefault(page_num, []).append(task)

        affected_pages = sorted(page_tasks.keys())

        # Build outline text
        outline_text = ""
        if structure:
            outline_text = "\n".join(
                f"  {'#' * e.level} {e.text} (p{e.page})"
                for e in structure.outline
            )

        # Determine which pages need images
        pages_with_image: list[int] = []
        for page_num, ptasks in page_tasks.items():
            if any(t.needs_image for t in ptasks):
                pages_with_image.append(page_num)

        # Run revision agents in parallel on affected pages
        all_changes: list[DocumentChange] = []
        semaphore = asyncio.Semaphore(PAGE_AGENT_SEMAPHORE_LIMIT)
        total_input_tokens = 0
        total_output_tokens = 0

        async def _revise_page(page_num: int) -> PageCorrectionResult:
            async with semaphore:
                page_image_b64 = (
                    result.page_images.get(str(page_num))
                    if page_num in pages_with_image
                    else None
                )
                page_attrs = (
                    structure.page_attributes.get(page_num)
                    if structure
                    else None
                )
                return await self._run_revision_agent(
                    page_num=page_num,
                    page_markdown=source_pages.get(str(page_num), ""),
                    tasks=page_tasks[page_num],
                    outline_text=outline_text,
                    page_image_b64=page_image_b64,
                    page_attrs=page_attrs,
                )

        agent_tasks = [_revise_page(p) for p in affected_pages]
        page_results = await asyncio.gather(*agent_tasks, return_exceptions=True)

        # Assemble new per-page markdowns
        new_page_mds: dict[str, str] = {}
        for page_num in range(1, result.total_pages + 1):
            page_key = str(page_num)
            new_page_mds[page_key] = source_pages.get(page_key, "")

        for i, pr in enumerate(page_results):
            page_num = affected_pages[i]
            page_key = str(page_num)
            if isinstance(pr, Exception):
                logger.error(f"Revision agent page {page_num} failed: {pr}")
                continue
            new_page_mds[page_key] = pr.corrected_markdown
            all_changes.extend(pr.changes)
            total_input_tokens += pr.input_tokens
            total_output_tokens += pr.output_tokens

        # Write new version
        result.page_markdowns[new_version] = new_page_mds
        result.versions[new_version] = "\n\n".join(
            new_page_mds[str(p)] for p in range(1, result.total_pages + 1)
        )

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(
            total_input_tokens, total_output_tokens
        )

        step_result = StepResult(
            name=f"revision_{revision_round}",
            display_name=f"Revision {revision_round}",
            version_before=source_version,
            version_after=new_version,
            elapsed_ms=elapsed_ms,
            changes=all_changes,
            metadata={
                "feedback": feedback,
                "affected_pages": affected_pages,
                "revision_round": revision_round,
                "tasks": [t.model_dump() for t in tasks],
                "task_count": len(tasks),
                "pages_with_image": pages_with_image,
            },
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cost_cents=cost_cents,
        )
        result.steps.append(step_result)
        return step_result

    # ------------------------------------------------------------------
    # Human Feedback — Selector resolution + direct edits
    # ------------------------------------------------------------------

    def resolve_selector(
        self,
        selector: TextSelector,
        markdown: str,
        page_markdowns: dict[str, str],
    ) -> tuple[int, int, int] | None:
        """Resolve a TextSelector to (page_number, start_offset, end_offset).

        Strategy:
          1. Try ``prefix + exact + suffix`` match across all pages.
          2. Fall back to exact-only match.
          3. Use ``_fuzzy_find_line`` for single-line selectors as last resort.

        Returns None if the selector cannot be resolved.
        """

        exact = selector.exact
        if not exact:
            return None

        # Build the full needle with optional prefix/suffix
        full_needle = ""
        if selector.prefix:
            full_needle += selector.prefix
        full_needle += exact
        if selector.suffix:
            full_needle += selector.suffix

        # Try full needle first, then exact-only.  Search per-page first
        # (gives accurate page number), fall back to full document for
        # cross-page or concatenation-only matches.
        for needle in ([full_needle, exact] if full_needle != exact else [exact]):
            for page_key in sorted(page_markdowns, key=lambda k: int(k)):
                page_md = page_markdowns[page_key]
                idx = page_md.find(needle)
                if idx != -1:
                    prefix_len = len(selector.prefix) if selector.prefix and needle != exact else 0
                    start = idx + prefix_len
                    end = start + len(exact)
                    return (int(page_key), start, end)

            # Fall back to full-document search (handles cross-page text)
            if markdown:
                idx = markdown.find(needle)
                if idx != -1:
                    # Determine page from offset: walk page_markdowns
                    offset = 0
                    for pk in sorted(page_markdowns, key=lambda k: int(k)):
                        page_len = len(page_markdowns[pk])
                        if idx < offset + page_len:
                            prefix_len = len(selector.prefix) if selector.prefix and needle != exact else 0
                            local_start = idx - offset + prefix_len
                            return (int(pk), local_start, local_start + len(exact))
                        offset += page_len + 2  # +2 for "\n\n" join separator
                    # If we can't map to a page, use page 1
                    prefix_len = len(selector.prefix) if selector.prefix and needle != exact else 0
                    return (1, idx + prefix_len, idx + prefix_len + len(exact))

        # Fuzzy fallback for single-line selectors
        for page_key in sorted(page_markdowns, key=lambda k: int(k)):
            page_md = page_markdowns[page_key]
            lines = page_md.split("\n")
            line_idx = _fuzzy_find_line(lines, exact)
            if line_idx >= 0:
                # Compute character offset
                offset = sum(len(ln) + 1 for ln in lines[:line_idx])
                return (int(page_key), offset, offset + len(lines[line_idx]))

        return None

    def apply_direct_edits(
        self,
        edits: list[FeedbackItem],
        result: PipelineViewerResult,
        section_map: SectionMap | None,
    ) -> list[CandidateChange]:
        """Convert EDIT-type FeedbackItems into CandidateChanges.

        Resolves each selector, creates a DocumentChange with the old/new
        text. Items whose selectors cannot be resolved are skipped with a
        warning.

        Args:
            edits: EDIT-type feedback items.
            result: Current pipeline result (for page markdowns).
            section_map: Section map (unused currently, reserved).

        Returns:
            List of CandidateChange objects.
        """
        import uuid

        # Get the latest version's page markdowns
        page_mds = self._get_latest_page_markdowns(result)
        full_md = self.get_latest_version(result)

        candidates: list[CandidateChange] = []

        for edit in edits:
            if edit.selector is None or edit.new_text is None:
                logger.warning(f"Skipping edit {edit.id}: missing selector or new_text")
                continue

            resolved = self.resolve_selector(edit.selector, full_md, page_mds)
            if resolved is None:
                logger.warning(
                    f"Skipping edit {edit.id}: could not resolve selector "
                    f"{edit.selector.exact!r}"
                )
                continue

            page_num, start, end = resolved

            candidates.append(
                CandidateChange(
                    id=uuid.uuid4().hex[:12],
                    change=DocumentChange(
                        page=page_num,
                        old_text=edit.selector.exact,
                        new_text=edit.new_text,
                        reasoning=edit.description or "Direct edit",
                        stage="revision",
                    ),
                    source_type="direct_edit",
                    source_feedback_id=edit.id,
                    source_description=edit.description or "Direct text replacement",
                )
            )

        return candidates

    def apply_candidate_changes(
        self,
        accepted_changes: list[CandidateChange],
        result: PipelineViewerResult,
        revision_round: int,
    ) -> StepResult:
        """Apply accepted candidate changes to produce a new version.

        Applies str_replace on the latest full-document markdown.  This
        ensures changes are applied against the most up-to-date content
        (including cross-page fixes and final cleanup), not against an
        older per-page version.

        The new version is stored as a full-document version only (no
        per-page markdowns).

        Args:
            accepted_changes: CandidateChanges that were accepted.
            result: Pipeline result (mutated in place).
            revision_round: 1-based revision round number.

        Returns:
            The StepResult for this revision.
        """
        step_start = time.time()

        # Determine version keys
        version_nums = [
            int(k[1:]) for k in result.versions if k.startswith("v") and k[1:].isdigit()
        ]
        latest_num = max(version_nums) if version_nums else 0
        source_version = f"v{latest_num}"
        new_version = f"v{latest_num + 1}"

        # Apply changes against the latest full-document markdown.
        # This is the version the user was viewing when they submitted
        # feedback, so text matches are most likely to succeed here.
        source_md = self.get_latest_version(result)

        # Find positions for all changes first, then apply in reverse
        # order so that earlier replacements don't shift later offsets.
        positioned: list[tuple[int, CandidateChange]] = []
        all_changes: list[DocumentChange] = []

        for candidate in accepted_changes:
            change = candidate.change
            count = source_md.count(change.old_text)
            if count == 1:
                idx = source_md.find(change.old_text)
                positioned.append((idx, candidate))
            elif count == 0:
                logger.warning(
                    f"Change {candidate.id}: old_text not found in document"
                )
            else:
                logger.warning(
                    f"Change {candidate.id}: old_text found {count} times "
                    f"in document, skipping ambiguous match"
                )

        # Sort by position descending — apply from end to start
        positioned.sort(key=lambda x: x[0], reverse=True)

        for idx, candidate in positioned:
            change = candidate.change
            source_md = (
                source_md[:idx]
                + change.new_text
                + source_md[idx + len(change.old_text) :]
            )
            all_changes.append(change)

        # Store as full-document version only (no page_markdowns entry).
        # The frontend falls back to versions[key] when page_markdowns
        # doesn't exist for a version, keeping the display consistent
        # with what the user was seeing before feedback.
        result.versions[new_version] = source_md

        elapsed_ms = int((time.time() - step_start) * 1000)

        step_result = StepResult(
            name=f"feedback_{revision_round}",
            display_name=f"Feedback Round {revision_round}",
            version_before=source_version,
            version_after=new_version,
            elapsed_ms=elapsed_ms,
            changes=all_changes,
            metadata={
                "revision_round": revision_round,
                "accepted_count": len(accepted_changes),
                "applied_count": len(all_changes),
            },
        )
        result.steps.append(step_result)
        return step_result

    async def process_feedback_batch(
        self,
        items: list[FeedbackItem],
        result: PipelineViewerResult,
        structure: StructureResult | None,
        section_map: SectionMap | None,
        feedback_history: list[list[FeedbackItem]] | None = None,
    ) -> list[CandidateChange]:
        """Orchestrate a mixed batch of feedback items.

        1. Separate items by type (EDIT vs COMMENT).
        2. Direct edits -> apply_direct_edits() -> CandidateChanges.
        3. Comments -> decompose + revision on a deep copy -> CandidateChanges.
        4. Return combined list.

        LLM revision runs on a temporary copy. Only accepted changes are
        committed later via apply_candidate_changes.

        Args:
            items: Mixed list of FeedbackItem objects.
            result: Current pipeline result.
            structure: Structure analysis result.
            section_map: Section map.
            feedback_history: Previous feedback rounds.

        Returns:
            Combined list of CandidateChange objects.
        """
        import copy
        import uuid

        edits = [i for i in items if i.type == FeedbackItemType.EDIT]
        comments = [i for i in items if i.type == FeedbackItemType.COMMENT]

        candidates: list[CandidateChange] = []

        # 1. Direct edits (no LLM)
        if edits:
            candidates.extend(self.apply_direct_edits(edits, result, section_map))

        # 2. Comments -> LLM decomposition + revision on a deep copy
        if comments:
            combined_feedback = "\n\n".join(
                f"- [{c.feedback_type.value if c.feedback_type else 'general'}] "
                f"{c.description}"
                + (f" (page {c.page})" if c.page else "")
                + (f" (section: {c.section})" if c.section else "")
                + (f' [selected text: "{c.selector.exact}"]' if c.selector else "")
                for c in comments
            )

            # Build feedback history as strings for decomposition
            history_strs: list[str] | None = None
            if feedback_history:
                history_strs = [
                    "\n".join(f"- {fi.description}" for fi in round_items)
                    for round_items in feedback_history
                ]

            # Decompose feedback
            decomposition = await self.decompose_feedback(
                feedback=combined_feedback,
                result=result,
                structure=structure,
                feedback_history=history_strs,
            )

            if decomposition.tasks:
                # Run revision on a deep copy
                result_copy = copy.deepcopy(result)
                revision_round = len(feedback_history) + 1 if feedback_history else 1

                step_result = await self.run_revision(
                    result=result_copy,
                    structure=structure,
                    tasks=decomposition.tasks,
                    feedback=combined_feedback,
                    revision_round=revision_round,
                )

                # Extract DocumentChanges as CandidateChanges
                first_comment_id = comments[0].id if comments else ""

                for change in step_result.changes:
                    # Try to find the originating comment by page
                    source_id = first_comment_id
                    source_desc = combined_feedback[:100]
                    for c in comments:
                        if c.page == change.page:
                            source_id = c.id
                            source_desc = c.description
                            break

                    candidates.append(
                        CandidateChange(
                            id=uuid.uuid4().hex[:12],
                            change=change,
                            source_type="ai_revision",
                            source_feedback_id=source_id,
                            source_description=source_desc,
                        )
                    )

        return candidates

    # ------------------------------------------------------------------
    # Helper methods for version access
    # ------------------------------------------------------------------

    @staticmethod
    def _get_latest_page_markdowns(
        result: PipelineViewerResult,
    ) -> dict[str, str]:
        """Get page markdowns from the latest version."""
        version_nums = [
            int(k[1:]) for k in result.page_markdowns
            if k.startswith("v") and k[1:].isdigit()
        ]
        if not version_nums:
            return {}
        latest = f"v{max(version_nums)}"
        return dict(result.page_markdowns.get(latest, {}))

    @staticmethod
    def get_latest_version(result: PipelineViewerResult) -> str:
        """Get the full markdown of the latest version."""
        version_nums = [
            int(k[1:]) for k in result.versions
            if k.startswith("v") and k[1:].isdigit()
        ]
        if not version_nums:
            return ""
        return result.versions.get(f"v{max(version_nums)}", "")

    async def _decompose_feedback(
        self,
        feedback: str,
        result: PipelineViewerResult,
        structure: StructureResult | None,
        feedback_history: list[str] | None = None,
    ) -> TaskDecompositionResult:
        """Decompose freeform feedback into discrete revision tasks.

        Uses a Haiku structured output call. Falls back to a single catch-all
        task targeting all pages on error.
        """
        from pydantic_ai import Agent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
        from ..agents.prompts.revision import (
            DECOMPOSITION_SYSTEM_PROMPT,
            build_decomposition_user_message,
            build_page_attributes_summary,
        )

        # Build outline text
        outline_text = ""
        if structure:
            outline_text = "\n".join(
                f"  {'#' * e.level} {e.text} (p{e.page})"
                for e in structure.outline
            )

        # Build page attributes summary
        page_attrs_summary = ""
        if structure and structure.page_attributes:
            page_attrs_summary = build_page_attributes_summary(
                structure.page_attributes
            )

        user_msg = build_decomposition_user_message(
            feedback=feedback,
            outline_text=outline_text,
            total_pages=result.total_pages,
            page_attributes_summary=page_attrs_summary,
            feedback_history=feedback_history,
        )

        all_pages = list(range(1, result.total_pages + 1))

        try:
            model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
            agent: Agent[None, TaskDecompositionResult] = Agent(
                model=model,
                output_type=TaskDecompositionResult,
                system_prompt=DECOMPOSITION_SYSTEM_PROMPT,
            )
            agent_result = await agent.run(user_msg)
            decomposition = agent_result.output

            # Filter tasks with invalid page numbers
            for task in decomposition.tasks:
                task.affected_pages = [
                    p for p in task.affected_pages
                    if 1 <= p <= result.total_pages
                ]
            # Remove tasks with no valid pages
            decomposition.tasks = [
                t for t in decomposition.tasks if t.affected_pages
            ]

            if not decomposition.tasks:
                return self._fallback_decomposition(feedback, all_pages)

            return decomposition

        except Exception as e:
            logger.warning(f"Feedback decomposition failed, using fallback: {e}")
            return self._fallback_decomposition(feedback, all_pages)

    @staticmethod
    def _fallback_decomposition(
        feedback: str,
        all_pages: list[int],
    ) -> TaskDecompositionResult:
        """Create a single catch-all task as fallback."""
        return TaskDecompositionResult(
            tasks=[
                RevisionTask(
                    id=1,
                    description=feedback,
                    affected_pages=all_pages,
                    needs_image=False,
                    category=RevisionTaskCategory.CONTENT,
                )
            ],
            reasoning="Fallback: could not decompose feedback, created single catch-all task.",
        )

    async def _run_revision_agent(
        self,
        page_num: int,
        page_markdown: str,
        tasks: list[RevisionTask],
        outline_text: str,
        page_image_b64: str | None = None,
        page_attrs: PageAttributes | None = None,
    ) -> PageCorrectionResult:
        """Run a revision agent on a single page with task-based input.

        Receives specific revision tasks instead of raw feedback. Page image
        is conditionally attached when any task has needs_image=True.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
        from ..agents.prompts.revision import (
            REVISION_SYSTEM_PROMPT,
            build_revision_user_message,
        )

        current_markdown = page_markdown
        changes: list[DocumentChange] = []
        issues: list[str] = []

        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        agent: Agent[None, None] = Agent(
            model=model,
            output_type=None,
            system_prompt=REVISION_SYSTEM_PROMPT,
        )

        @agent.tool_plain
        def str_replace(old_text: str, new_text: str, reasoning: str, category: str) -> str:
            """Replace exact text in the page markdown.

            Args:
                old_text: Exact text to find. Must appear once.
                new_text: Corrected text.
                reasoning: Why this change is needed.
                category: Should be "revision".
            """
            nonlocal current_markdown

            count = current_markdown.count(old_text)
            if count == 0:
                return (
                    f"ERROR: old_text not found in page markdown.\n\n"
                    f"You searched for:\n  {old_text!r}\n\n"
                    f"Current page markdown:\n---\n{current_markdown}\n---\n\n"
                    f"Review the current markdown and try again."
                )
            if count > 1:
                return (
                    f"ERROR: old_text found {count} times. Include more "
                    f"surrounding context to make it unique."
                )

            current_markdown = current_markdown.replace(old_text, new_text, 1)
            changes.append(
                DocumentChange(
                    page=page_num,
                    old_text=old_text,
                    new_text=new_text,
                    reasoning=reasoning,
                    stage="revision",
                )
            )
            return f"OK — replaced on page {page_num}."

        @agent.tool_plain
        def no_changes(confidence: str, notes: str = "") -> str:
            """Affirm that no revision is needed for this page.

            Args:
                confidence: How confident: "high", "medium", or "low".
                notes: Optional observations.
            """
            if notes:
                issues.append(f"Page {page_num} ({confidence}): {notes}")
            return "Acknowledged — no changes needed."

        # Build user message parts
        user_parts: list[Any] = []

        # Conditionally attach page image
        if page_image_b64:
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(page_image_b64),
                    media_type="image/png",
                )
            )

        text_msg = build_revision_user_message(
            tasks=tasks,
            page_num=page_num,
            current_markdown=page_markdown,
            outline_text=outline_text,
            page_attrs=page_attrs,
        )
        user_parts.append(text_msg)

        agent_result = await agent.run(user_parts)
        usage = agent_result.usage()

        return PageCorrectionResult(
            page=page_num,
            corrected_markdown=current_markdown,
            changes=changes,
            issues=issues,
            input_tokens=usage.request_tokens or 0,
            output_tokens=usage.response_tokens or 0,
        )
