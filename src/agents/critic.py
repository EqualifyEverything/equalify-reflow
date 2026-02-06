"""CriticAgent - Document Quality Analysis for Multi-Round Processing.

The CriticAgent analyzes merged markdown (pageless) to identify issues
and produce a CriticReport. It runs in Round N > 1 of the multi-round pipeline.

The agent uses tools to:
1. Systematically scan the document by viewing sections
2. Search for known issue patterns (placeholders, broken syntax)
3. Report issues found with specific line numbers and severity
4. Mark the document as ready when quality is acceptable

## Tool Philosophy

The CriticAgent is tool-based to enable systematic document analysis:
- view_section(): Navigate through the document systematically
- find_pattern(): Search for known issue patterns
- report_issue(): Record issues with metadata for downstream fixing
- mark_ready(): Signal that document quality is acceptable

## Quality Assessment

The agent assesses quality based on:
- Absence of critical issues (unfilled placeholders, missing alt-text)
- Low count of major issues (structural problems, accessibility gaps)
- Overall formatting and consistency

## Model Tier

Uses EFFICIENT (Haiku) for cost-effectiveness since analysis is straightforward
pattern matching and heuristic evaluation.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.metrics_service import record_critic_issue, record_llm_call

from .debug_capture import extract_raw_response, serialize_prompt
from .events import CriticAnalysisCompleteEvent, EventBus
from .models import (
    CriticIssue,
    CriticReport,
    IssueSeverity,
    LLMCallRecord,
    PageBoundaryMap,
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Critic Dependencies
# =============================================================================


@dataclass
class CriticDeps:
    """Dependencies for CriticAgent."""

    document_id: str
    merged_markdown: str
    page_boundary_map: PageBoundaryMap
    page_images: dict[int, "Image.Image"]
    round_number: int

    # Mutable state - agent adds issues here
    issues: list[CriticIssue] = field(default_factory=list)
    marked_ready: bool = False

    # Event bus for streaming
    event_bus: EventBus | None = None

    # Pipeline dossier for context injection
    dossier: Any | None = None


# =============================================================================
# Tool Result Types
# =============================================================================


class ViewSectionResult(BaseModel):
    """Result from viewing a section of markdown."""

    content: str
    start_line: int
    end_line: int
    total_lines: int


class FindPatternResult(BaseModel):
    """Result from searching for a pattern."""

    found: bool
    matches: list[dict] = Field(default_factory=list)  # [{line: int, text: str}]
    total_matches: int = 0


class ReportIssueResult(BaseModel):
    """Result from reporting an issue."""

    success: bool
    issue_id: str = ""
    message: str = ""


class MarkReadyResult(BaseModel):
    """Result from marking document ready."""

    success: bool
    message: str = ""


# =============================================================================
# System Prompt
# =============================================================================


CRITIC_SYSTEM_PROMPT = """You are a document quality critic specializing in accessibility remediation.

Your job is to analyze markdown documents converted from PDFs and identify quality issues that impact accessibility and usability.

## Issue Categories

### structure
Heading hierarchy problems, missing sections, broken outline:
- Heading levels that skip (e.g., H2 -> H4)
- Missing required sections
- Inconsistent heading structure

### accessibility
Issues that directly impact screen reader users:
- Missing alt-text for images (critical!)
- Tables without proper headers
- Unclear or inadequate image descriptions
- Missing form labels or ARIA attributes

### content
Content quality issues:
- Unfilled placeholders (e.g., [IMAGE], <!-- TODO -->, [FIGURE])
- Incomplete transcriptions
- Missing captions or labels
- Broken or incomplete text

### formatting
Markdown syntax and formatting issues:
- Invalid markdown syntax
- Broken links or references
- Inconsistent styling
- Extra whitespace or artifacts

## Severity Levels

### critical
MUST fix before output. Examples:
- Missing alt-text for informative images
- Unfilled placeholder text visible in output
- Completely broken tables
- Missing required content

### major
SHOULD fix, impacts accessibility significantly. Examples:
- Alt-text present but inadequate/unclear
- Table headers missing or incorrect
- Heading hierarchy issues
- Important formatting problems

### minor
Nice to fix, minor impact. Examples:
- Inconsistent spacing
- Minor formatting deviations
- Style inconsistencies
- Non-critical missing elements

### cosmetic
Optional polish. Examples:
- Extra blank lines
- Minor whitespace issues
- Purely aesthetic concerns

## Workflow

1. **Systematic scan**: Use view_section() to scan through the document in chunks (100-150 lines at a time). Start from line 1.

2. **Pattern search**: Use find_pattern() to look for known issues:
   - Placeholders: `\\[IMAGE\\]`, `\\[FIGURE\\]`, `<!-- TODO`, `<!-- image`
   - Missing alt-text: `!\\[\\]\\(`
   - Table issues: tables without | --- | separator rows

3. **Report issues**: For each problem found, use report_issue() with:
   - Accurate line numbers (from your scan or search)
   - Specific search_text to locate the issue
   - Clear description of what's wrong
   - Suggested fix approach

4. **Quality decision**: After scanning the full document:
   - If NO critical issues and FEW/NO major issues: use mark_ready()
   - Otherwise: finish without marking ready (issues will be fixed in next round)

## Quality Threshold for mark_ready()

Mark ready ONLY if ALL of these are true:
- Zero critical issues
- At most 1-2 minor major issues
- All images have meaningful alt-text
- All tables have proper headers
- No unfilled placeholders visible
- Markdown syntax is valid

## Important Notes

- Be thorough: scan the ENTIRE document, not just the beginning
- Be specific: include exact line numbers and text snippets
- Be realistic: not every document needs to be perfect
- Prioritize accessibility: alt-text and table headers are critical
- Don't report cosmetic issues unless the document is otherwise clean

Focus on issues that would impact a user relying on assistive technology."""


# =============================================================================
# Tool Implementations
# =============================================================================


async def view_section_tool(
    ctx: RunContext[CriticDeps],
    start_line: int,
    end_line: int,
) -> ViewSectionResult:
    """View a section of the merged markdown document.

    Use this to systematically scan through the document looking for issues.
    Start from line 1 and work through in chunks of ~100-150 lines.

    Args:
        start_line: First line to view (1-indexed)
        end_line: Last line to view (inclusive)
    """
    deps = ctx.deps
    lines = deps.merged_markdown.split("\n")
    total = len(lines)

    # Clamp to valid range
    start = max(1, start_line) - 1  # Convert to 0-indexed
    end = min(total, end_line)

    content = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))

    return ViewSectionResult(
        content=content,
        start_line=start_line,
        end_line=end_line,
        total_lines=total,
    )


async def find_pattern_tool(
    ctx: RunContext[CriticDeps],
    pattern: str,
) -> FindPatternResult:
    """Search for a text pattern or regex in the markdown.

    Use this to find known issue patterns like placeholders, missing alt-text, etc.

    Args:
        pattern: Text or regex pattern to search for (e.g., "\\[IMAGE\\]", "!\\[\\]\\(")
    """
    deps = ctx.deps
    lines = deps.merged_markdown.split("\n")
    matches: list[dict] = []

    # Try as regex first
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
        for i, line in enumerate(lines, start=1):
            if compiled.search(line):
                matches.append({
                    "line": i,
                    "text": line.strip()[:200],  # Truncate long lines
                })
    except re.error:
        # Fall back to literal string search
        for i, line in enumerate(lines, start=1):
            if pattern.lower() in line.lower():
                matches.append({
                    "line": i,
                    "text": line.strip()[:200],
                })

    return FindPatternResult(
        found=len(matches) > 0,
        matches=matches[:50],  # Limit to first 50 matches
        total_matches=len(matches),
    )


async def report_issue_tool(
    ctx: RunContext[CriticDeps],
    severity: str,
    category: str,
    description: str,
    line_start: int,
    line_end: int,
    suggested_fix: str = "",
    search_text: str = "",
    reasoning: str = "",
) -> ReportIssueResult:
    """Report a quality issue found in the document.

    Args:
        severity: Issue severity: "critical", "major", "minor", or "cosmetic"
        category: Issue category: "structure", "accessibility", "content", or "formatting"
        description: Clear description of what's wrong
        line_start: First line where the issue occurs (1-indexed)
        line_end: Last line where the issue occurs (inclusive)
        suggested_fix: How to fix this issue (optional but helpful)
        search_text: Text snippet to locate the issue (for downstream fixing)
        reasoning: Why this is an issue (optional)
    """
    deps = ctx.deps

    # Validate severity
    try:
        severity_enum = IssueSeverity(severity.lower())
    except ValueError:
        return ReportIssueResult(
            success=False,
            message=f"Invalid severity '{severity}'. Use: critical, major, minor, cosmetic",
        )

    # Validate category
    valid_categories = ["structure", "accessibility", "content", "formatting"]
    if category.lower() not in valid_categories:
        return ReportIssueResult(
            success=False,
            message=f"Invalid category '{category}'. Use: {', '.join(valid_categories)}",
        )

    # Get source pages from page boundary map
    source_pages = deps.page_boundary_map.get_pages_for_range(line_start, line_end)

    # Create the issue
    issue = CriticIssue(
        severity=severity_enum,
        category=category.lower(),
        description=description,
        suggested_fix=suggested_fix,
        line_start=line_start,
        line_end=line_end,
        search_text=search_text,
        reasoning=reasoning,
        source_pages=source_pages,
    )

    deps.issues.append(issue)

    logger.debug(
        f"CriticAgent reported {severity} {category} issue at lines {line_start}-{line_end}: {description[:50]}..."
    )

    return ReportIssueResult(
        success=True,
        issue_id=issue.issue_id,
        message=f"Issue recorded: {severity} {category} at lines {line_start}-{line_end}",
    )


async def mark_ready_tool(
    ctx: RunContext[CriticDeps],
) -> MarkReadyResult:
    """Mark the document as ready for output.

    Call this ONLY when the document passes quality checks:
    - No critical issues
    - Few or no major issues
    - All images have alt-text
    - Tables properly formatted
    - No unfilled placeholders
    """
    deps = ctx.deps

    # Check if there are critical issues
    critical_count = sum(1 for i in deps.issues if i.severity == IssueSeverity.CRITICAL)
    major_count = sum(1 for i in deps.issues if i.severity == IssueSeverity.MAJOR)

    if critical_count > 0:
        return MarkReadyResult(
            success=False,
            message=f"Cannot mark ready: {critical_count} critical issue(s) found. Fix them first.",
        )

    if major_count > 3:
        return MarkReadyResult(
            success=False,
            message=f"Cannot mark ready: {major_count} major issues found. Consider fixing them first.",
        )

    deps.marked_ready = True

    logger.info(
        f"CriticAgent marked document {deps.document_id} ready "
        f"(round {deps.round_number}, {len(deps.issues)} total issues)"
    )

    return MarkReadyResult(
        success=True,
        message=f"Document marked ready for output. {len(deps.issues)} minor/cosmetic issues noted.",
    )


# =============================================================================
# Critic Agent
# =============================================================================


# Lazy initialization to avoid AWS credential issues at import time
_critic_agent: Agent[CriticDeps, None] | None = None


def _get_critic_agent() -> Agent[CriticDeps, None]:
    """Get or create the critic agent."""
    global _critic_agent

    if _critic_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

        _critic_agent = Agent(
            model=model,
            deps_type=CriticDeps,
            system_prompt=CRITIC_SYSTEM_PROMPT,
        )

        # Register tools
        _critic_agent.tool(view_section_tool)
        _critic_agent.tool(find_pattern_tool)
        _critic_agent.tool(report_issue_tool)
        _critic_agent.tool(mark_ready_tool)

        # Dynamic instructions from dossier
        @_critic_agent.instructions
        def _inject_dossier_context(ctx: RunContext[CriticDeps]) -> str:
            from .shared_prompts import format_document_identity

            d = ctx.deps.dossier
            if d is None:
                return ""
            return format_document_identity(d)

        logger.info("Critic agent initialized")

    return _critic_agent


# Public function to get the agent (for compatibility with exports)
def get_critic_agent() -> Agent[CriticDeps, None]:
    """Get the critic agent (lazily initialized).

    This is the public API for getting the agent instance.
    The agent is lazily initialized on first call to avoid
    AWS credential issues at import time.
    """
    return _get_critic_agent()


# =============================================================================
# Main Entry Point
# =============================================================================


async def run_critic_analysis(
    document_id: str,
    merged_markdown: str,
    page_boundary_map: PageBoundaryMap,
    page_images: dict[int, "Image.Image"],
    round_number: int,
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
) -> CriticReport:
    """Run CriticAgent on merged markdown.

    Args:
        document_id: Document identifier
        merged_markdown: The merged pageless markdown to analyze
        page_boundary_map: Mapping from lines to source pages
        page_images: Page images for visual reference (not currently used by critic)
        round_number: Current round number
        event_bus: Optional event bus for streaming
        capture_debug: Whether to capture debug info for bundle

    Returns:
        CriticReport with all issues found and quality assessment
    """
    start_time = time.time()

    deps = CriticDeps(
        document_id=document_id,
        merged_markdown=merged_markdown,
        page_boundary_map=page_boundary_map,
        page_images=page_images,
        round_number=round_number,
        event_bus=event_bus,
    )

    # Build initial prompt with document stats
    total_lines = len(merged_markdown.splitlines())
    total_chars = len(merged_markdown)

    initial_prompt = f"""Analyze this markdown document for quality issues.

Document: {document_id}
Round: {round_number}
Total lines: {total_lines}
Total characters: {total_chars}

Your task is to systematically scan the document and identify any issues that would impact accessibility or document quality.

## Recommended approach:

1. **Quick pattern search first**: Use find_pattern() to quickly check for common issues:
   - `!\\[\\]\\(` - empty alt-text (critical accessibility issue)
   - `<!-- image` or `<!-- table` - unfilled placeholders
   - `\\[IMAGE\\]` or `\\[FIGURE\\]` or `\\[TABLE\\]` - placeholder text
   - `TODO` - unfinished content

2. **Systematic scan**: Use view_section() to scan through the document:
   - Start at line 1, view ~100-150 lines at a time
   - Look for structural issues, formatting problems, incomplete content
   - Continue until you've seen the entire document

3. **Report all issues**: Use report_issue() for each problem found
   - Be specific with line numbers and search text
   - Prioritize critical and major issues

4. **Quality decision**: After scanning everything:
   - If quality is acceptable (no critical issues, few major): use mark_ready()
   - Otherwise: just finish - issues will be addressed in the next round

Start your analysis now."""

    # Run agent
    agent = _get_critic_agent()
    result = await agent.run(
        initial_prompt,
        deps=deps,
    )

    duration_ms = int((time.time() - start_time) * 1000)

    # Extract usage
    usage = result.usage()
    input_tokens = usage.request_tokens or 0
    output_tokens = usage.response_tokens or 0

    # Calculate quality score based on issues
    critical_count = sum(1 for i in deps.issues if i.severity == IssueSeverity.CRITICAL)
    major_count = sum(1 for i in deps.issues if i.severity == IssueSeverity.MAJOR)
    minor_count = sum(1 for i in deps.issues if i.severity == IssueSeverity.MINOR)

    # Quality scoring heuristic
    if critical_count > 0:
        # Critical issues heavily penalize quality
        quality = max(0.0, 0.5 - (critical_count * 0.1) - (major_count * 0.05))
    elif major_count > 0:
        # Major issues moderately penalize quality
        quality = max(0.5, 0.85 - (major_count * 0.05) - (minor_count * 0.01))
    elif minor_count > 0:
        # Minor issues slightly penalize quality
        quality = max(0.8, 0.95 - (minor_count * 0.01))
    else:
        # No issues - high quality
        quality = 0.95 if not deps.marked_ready else 0.98

    # Build summary message
    issue_parts = []
    if critical_count:
        issue_parts.append(f"{critical_count} critical")
    if major_count:
        issue_parts.append(f"{major_count} major")
    if minor_count:
        issue_parts.append(f"{minor_count} minor")
    cosmetic_count = len(deps.issues) - critical_count - major_count - minor_count
    if cosmetic_count:
        issue_parts.append(f"{cosmetic_count} cosmetic")

    if issue_parts:
        summary = f"Found {len(deps.issues)} issues ({', '.join(issue_parts)})"
    else:
        summary = "No issues found"

    if deps.marked_ready:
        summary += " - document marked ready for output"

    # Build report
    report = CriticReport(
        document_id=document_id,
        round_number=round_number,
        issues=deps.issues,
        critical_count=critical_count,
        major_count=major_count,
        overall_quality=quality,
        ready_for_output=deps.marked_ready and critical_count == 0,
        summary=summary,
        analysis_duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    # Record Prometheus metrics
    record_llm_call(
        agent="critic",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cents=0.0,  # TODO: Calculate actual cost
        duration_ms=duration_ms,
    )

    for issue in deps.issues:
        record_critic_issue(issue.severity.value, issue.category)

    # Capture debug info if requested
    if capture_debug:
        report.llm_call = LLMCallRecord(
            agent="critic",
            purpose=f"round_{round_number}_analysis",
            page=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=0.0,
            timestamp=datetime.now(UTC),
            duration_ms=duration_ms,
            prompt_text=serialize_prompt(result.all_messages()[:1]),  # System + user
            response_raw=extract_raw_response(result),
        )

    # Emit event
    if event_bus:
        event_bus.emit(
            CriticAnalysisCompleteEvent(
                document_id=document_id,
                round_number=round_number,
                issues_found=len(deps.issues),
                critical_count=critical_count,
                major_count=major_count,
                quality_score=quality,
                ready_for_output=report.ready_for_output,
                summary=report.summary,
            )
        )

    logger.info(
        f"CriticAgent completed round {round_number} analysis for {document_id}: "
        f"{summary}, quality={quality:.2f}, duration={duration_ms}ms"
    )

    return report


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    "run_critic_analysis",
    "CriticDeps",
    "get_critic_agent",
    "ViewSectionResult",
    "FindPatternResult",
    "ReportIssueResult",
    "MarkReadyResult",
]
