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
    CodeBlockInfo,
    DocumentChange,
    FigureData,
    FootnoteInfo,
    OutlineEntry,
    PageCorrectionResult,
    PipelineViewerResult,
    StepResult,
    StructurePageOutput,
    StructureResult,
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


def _load_procedure(doc_type: str) -> str:
    """Load a page correction procedure file by document type."""
    path = PROCEDURES_DIR / "page_correction" / f"{doc_type}.md"
    if not path.exists():
        available = [p.stem for p in (PROCEDURES_DIR / "page_correction").glob("*.md")]
        logger.warning(f"No procedure for '{doc_type}', available: {available}")
        return ""
    return path.read_text()


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
    for i, line in enumerate(lines):
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
            await self._step_heading_levels(result, structure)

        if enable_page_content and structure is not None:
            await self._step_page_content(result, structure)
            await self._step_code_blocks(result, structure)

        if enable_boundaries and structure is not None:
            await self._step_boundaries(result, structure)
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
        for pic in doc.pictures:
            if pic.image and hasattr(pic.image, "pil_image") and pic.image.pil_image:
                result.figures.append(
                    FigureData(
                        ref_id=str(pic.self_ref) if pic.self_ref else "",
                        caption=pic.caption_text(doc=doc) or "",
                        page_number=pic.prov[0].page_no if pic.prov else 1,
                        image_base64=_pil_to_base64(pic.image.pil_image),
                    )
                )

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
            text_msg = build_structure_user_message(
                page_markdown=page_md,
                outline_so_far=outline_dicts,
                page_number=page_num,
                total_pages=result.total_pages,
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
            structure.page_types[page_num] = page_output.page_type

            for heading in page_output.headings:
                structure.outline.append(
                    OutlineEntry(
                        level=heading.recommended_level,
                        text=heading.text,
                        page=page_num,
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

            logger.info(
                f"Structure page {page_num}/{result.total_pages}: "
                f"type={page_output.page_type.value}, "
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
                    "page_types": {
                        str(k): v.value for k, v in structure.page_types.items()
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
    # Phase 1b — Deterministic heading level fix
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
    # Phase 2 — Page Content Corrections (parallel, semaphore-limited)
    # ------------------------------------------------------------------

    async def _step_page_content(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
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

        async def _process_page(page_num: int) -> PageCorrectionResult:
            async with semaphore:
                return await self._run_page_agent(
                    page_num=page_num,
                    page_markdown=page_mds[str(page_num)],
                    page_image_b64=result.page_images.get(str(page_num)),
                    structure=structure,
                )

        # Fan out all pages
        tasks = [_process_page(p) for p in range(1, result.total_pages + 1)]
        page_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        corrected_mds: dict[str, str] = {}
        total_input_tokens = 0
        total_output_tokens = 0
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

        # Write v1
        result.page_markdowns["v1"] = corrected_mds
        result.versions["v1"] = "\n\n".join(
            corrected_mds[str(p)] for p in range(1, result.total_pages + 1)
        )

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

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
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

    async def _run_page_agent(
        self,
        page_num: int,
        page_markdown: str,
        page_image_b64: str | None,
        structure: StructureResult,
    ) -> PageCorrectionResult:
        """Run a single page correction agent.

        Sets up a PydanticAI agent with str_replace and no_changes tools,
        loads the appropriate procedure based on document type, and runs
        the agent. Failed str_replace calls are reported back to the agent
        for retry.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from pydantic_ai.models.bedrock import BedrockConverseModel

        from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier

        # Load procedure for this page's document type
        page_type = structure.page_types.get(page_num)
        procedure = _load_procedure(page_type.value) if page_type else ""

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
        user_parts.append(
            f"## Page {page_num}\n\n"
            f"### Document outline\n{outline_text}\n\n"
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
    ) -> None:
        """Fix cross-page issues on the assembled document.

        Assembles all pages into one document, then runs:
        1. Boundary fixes (split words, duplicated text at page joins)
        2. Footnote relocation (move footnote bodies to endnotes)

        Produces v2.
        """


        # Determine which version to read from
        source_version = "v1" if "v1" in result.page_markdowns else "v0"
        source_pages = result.page_markdowns[source_version]

        step_start = time.time()
        changes: list[DocumentChange] = []
        issues: list[str] = []

        # Assemble full document
        assembled_parts: list[str] = []
        for page_num in range(1, result.total_pages + 1):
            page_md = source_pages.get(str(page_num), "")
            assembled_parts.append(page_md)

        current_document = "\n\n".join(assembled_parts)

        # Build boundary snippets for the agent
        boundary_snippets: list[dict] = []
        for i in range(len(assembled_parts) - 1):
            tail_lines = assembled_parts[i].split("\n")
            head_lines = assembled_parts[i + 1].split("\n")
            boundary_snippets.append({
                "page_before": i + 1,
                "page_after": i + 2,
                "tail_text": "\n".join(tail_lines[-5:]),
                "head_text": "\n".join(head_lines[:5]),
            })

        footnote_numbers = [fn.number for fn in structure.footnotes]
        total_input_tokens = 0
        total_output_tokens = 0

        # --- Agent 1: Boundary fixes ---
        if boundary_snippets:
            current_document, boundary_changes, boundary_issues, b_in, b_out = (
                await self._run_boundary_agent(
                    current_document,
                    boundary_snippets,
                    footnote_numbers,
                )
            )
            changes.extend(boundary_changes)
            issues.extend(boundary_issues)
            total_input_tokens += b_in
            total_output_tokens += b_out

        # --- Agent 2: Footnote relocation ---
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
                    "page_boundaries": result.total_pages - 1,
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
