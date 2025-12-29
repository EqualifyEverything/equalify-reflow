# PRD-024: Tables Agent Merge & Validation Loop

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: Phase 3b: Refine (Specialized Agents)
**Estimated Effort**: 2 days
**Dependencies**: PRD-021 (Data Models)
**Reference**: [PRD-020](./PRD-020-3-phase-architecture.md)

## Problem Statement

The current tables implementation has two separate agents:
- `tables/structure_agent.py` - Analyzes table structure
- `tables/accuracy_agent.py` - Validates content accuracy

These should be **merged** into a single agent with a **validation loop**:
1. Analyze and enhance table in one pass
2. Validate table structure (Python - column count, headers)
3. Loop if validation fails

## Success Criteria

- [ ] Single TablesAgent class replaces two separate agents
- [ ] Validation loop for table structure
- [ ] Auto-corrections for valid tables with high confidence
- [ ] Review items for complex or low-confidence tables
- [ ] Table placeholder markers supported

## Current vs New Architecture

### Current Flow
```
structure_agent → accuracy_agent → routing → Observations
                                                  ↓
                                            Consolidation
                                                  ↓
                                              Proposals
```

### New Flow
```
                    ┌─────────────────────┐
                    │                     │
tables_agent → validate (Python) → issues? → loop (max 2)
                    │
                    └─→ AgentResult
                           │
              auto_corrections + review_items
```

## Technical Requirements

### Table Placeholder Format

Extraction will mark tables with placeholders:

```markdown
<!-- TABLE:p3:t1 -->
| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |
<!-- /TABLE:p3:t1 -->
```

### Tables Agent Implementation

```python
# src/agents/tables/tables_agent.py

from datetime import datetime
import re
import uuid

from pydantic import BaseModel

from src.shared.models.agent_trace import AgentResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.review_checklist import ReviewItem, ReviewOption
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.remediation import DocumentManifest
from src.agents.core import create_agent, run_agent
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier


class TablePlaceholder(BaseModel):
    """Parsed table placeholder from markdown."""
    id: str  # "p3:t1"
    full_match: str  # Full <!-- TABLE:... --> block
    start_marker: str  # "<!-- TABLE:p3:t1 -->"
    end_marker: str  # "<!-- /TABLE:p3:t1 -->"
    content: str  # The table markdown between markers
    page_num: int
    table_index: int


class TableValidationIssue(BaseModel):
    """Issue found during table validation."""
    type: str  # "inconsistent_columns", "missing_header", "empty_table"
    description: str
    severity: str


class TableEnhanceOutput(BaseModel):
    """Output from table enhancement agent."""
    table_markdown: str
    reasoning: str
    confidence: float
    changes_made: list[str]


class TablesAgent:
    """Merged tables agent with validation loop."""

    MAX_ITERATIONS = 2
    CONFIDENCE_THRESHOLD = 0.95

    def __init__(self):
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                prompts_file="tables_enhance.yaml",
                output_type=TableEnhanceOutput,
                model_tier=ModelTier.EFFICIENT,
                use_deps=True,
            )
        return self._agent

    async def process(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
    ) -> AgentResult:
        """Process all tables and return unified result."""

        start_time = datetime.utcnow()
        observations: list[Observation] = []
        auto_corrections: list[AutoCorrection] = []
        review_items: list[ReviewItem] = []
        enhanced_content: dict[str, str] = {}
        total_cost = 0.0

        # Find all table placeholders
        placeholders = self._find_table_placeholders(markdown)

        if not placeholders:
            return AgentResult(
                agent_name="tables",
                observations=[],
                auto_corrections=[],
                review_items=[],
                reasoning_summary="No table placeholders found.",
                confidence=1.0,
                enhanced_content=None,
                cost_cents=0.0,
                time_seconds=0.0,
            )

        # Process each table with validation loop
        for placeholder in placeholders:
            page_image = pages[placeholder.page_num - 1].image_base64
            current_table = placeholder.content
            final_result = None
            iterations_used = 0

            # Validation loop
            for iteration in range(self.MAX_ITERATIONS):
                iterations_used = iteration + 1

                # Enhance table
                result, usage = await self._enhance_table(
                    table_markdown=current_table,
                    page_image=page_image,
                    page_num=placeholder.page_num,
                    manifest=manifest,
                    job_id=job_id,
                )
                total_cost += usage.estimated_cost_cents
                final_result = result

                enhanced_table = result.table_markdown

                # Validate table structure
                validation_issues = self._validate_table(enhanced_table)

                if not validation_issues:
                    break  # Valid - exit loop

                # Has issues - update for next iteration
                current_table = enhanced_table

            # Create observation
            obs = Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="tables",
                source="agent",
                visual_description=f"Table on page {placeholder.page_num}",
                markup_description=f"Table with {self._count_rows(placeholder.content)} rows",
                location=ObservationLocation(
                    location_type="element",
                    value=f"table[data-id='{placeholder.id}']",
                    page_num=placeholder.page_num,
                ),
                confidence=final_result.confidence if final_result else 0.5,
                severity="major",
                category="table",
            )
            observations.append(obs)

            # Route based on confidence and validation
            validation_issues = self._validate_table(final_result.table_markdown) if final_result else []

            if final_result and not validation_issues and final_result.confidence >= self.CONFIDENCE_THRESHOLD:
                # High confidence, valid structure - auto correct
                new_content = f"{placeholder.start_marker}\n{final_result.table_markdown}\n{placeholder.end_marker}"
                auto_corrections.append(AutoCorrection(
                    id=str(uuid.uuid4()),
                    observation_id=obs.id,
                    search=placeholder.full_match,
                    replace=new_content,
                    justification=f"Enhanced table: {final_result.reasoning}. Validated after {iterations_used} iteration(s).",
                    confidence=final_result.confidence,
                    agent="tables",
                    page_num=placeholder.page_num,
                ))
                enhanced_content[placeholder.id] = final_result.table_markdown
            else:
                # Low confidence or validation issues - needs review
                enhanced_table = final_result.table_markdown if final_result else placeholder.content

                review_items.append(ReviewItem(
                    id=str(uuid.uuid4()),
                    observation_id=obs.id,
                    agent="tables",
                    # NOTE: category is NOT on ReviewItem - derived from Observation at checklist level
                    question=f"Verify table on page {placeholder.page_num}",
                    options=[
                        ReviewOption(
                            id="accept",
                            label="Accept enhanced table",
                            action="replace",
                            replacement_text=f"{placeholder.start_marker}\n{enhanced_table}\n{placeholder.end_marker}",
                            is_recommended=True,
                        ),
                        ReviewOption(
                            id="keep_original",
                            label="Keep original table",
                            action="keep",
                            is_recommended=False,
                        ),
                        ReviewOption(
                            id="other",
                            label="Edit table manually",
                            action="other",
                            is_recommended=False,
                        ),
                    ],
                    search_text=placeholder.full_match,  # Text to find for replacement
                    context=self._build_table_context(placeholder, enhanced_table, validation_issues),
                    page_num=placeholder.page_num,
                    agent_recommendation=final_result.reasoning if final_result else "Could not enhance table",
                    agent_confidence=final_result.confidence if final_result else 0.5,
                ))

        end_time = datetime.utcnow()

        return AgentResult(
            agent_name="tables",
            observations=observations,
            auto_corrections=auto_corrections,
            review_items=review_items,
            reasoning_summary=self._build_summary(placeholders, auto_corrections, review_items),
            confidence=self._calculate_confidence(auto_corrections, review_items),
            enhanced_content=enhanced_content if enhanced_content else None,
            cost_cents=total_cost,
            time_seconds=(end_time - start_time).total_seconds(),
        )

    async def _enhance_table(
        self,
        table_markdown: str,
        page_image: str,
        page_num: int,
        manifest: DocumentManifest,
        job_id: str,
    ) -> tuple[TableEnhanceOutput, LLMUsage]:
        """Enhance a single table using LLM."""

        agent = self._get_agent()

        prompt = f"""
Analyze and enhance this table from page {page_num}.

Current markdown:
```
{table_markdown}
```

Compare to the visual table in the image and:
1. Verify the structure matches the visual
2. Check content accuracy
3. Ensure headers are properly identified
4. Fix any alignment issues

Return the enhanced table markdown.
"""

        deps = AgentDependencies(
            job_id=job_id,
            manifest=manifest,
            custom_context={"page_num": page_num},
        )

        # Include page image
        from pydantic_ai import BinaryContent
        import base64

        image_bytes = base64.b64decode(page_image)
        full_prompt = [
            prompt,
            BinaryContent(data=image_bytes, media_type="image/png"),
        ]

        result = await run_agent(
            agent=agent,
            prompt=full_prompt,
            deps=deps,
            job_id=job_id,
            agent_name="tables_enhance",
        )

        from src.agents.factory import extract_usage
        usage = extract_usage(result, ModelTier.EFFICIENT)

        return result.output, usage

    def _find_table_placeholders(self, markdown: str) -> list[TablePlaceholder]:
        """Find all table placeholders in markdown."""
        placeholders = []

        # Match: <!-- TABLE:pN:tM --> ... <!-- /TABLE:pN:tM -->
        pattern = r'(<!-- TABLE:p(\d+):t(\d+) -->)(.*?)(<!-- /TABLE:p\2:t\3 -->)'

        for match in re.finditer(pattern, markdown, re.DOTALL):
            start_marker = match.group(1)
            page_num = int(match.group(2))
            table_index = int(match.group(3))
            content = match.group(4).strip()
            end_marker = match.group(5)

            placeholders.append(TablePlaceholder(
                id=f"p{page_num}:t{table_index}",
                full_match=match.group(0),
                start_marker=start_marker,
                end_marker=end_marker,
                content=content,
                page_num=page_num,
                table_index=table_index,
            ))

        return placeholders

    def _validate_table(self, table_markdown: str) -> list[TableValidationIssue]:
        """Validate table structure (pure Python)."""
        issues = []

        lines = [l for l in table_markdown.strip().split('\n') if l.strip()]

        if len(lines) < 2:
            issues.append(TableValidationIssue(
                type="too_few_rows",
                description="Table has fewer than 2 rows",
                severity="critical",
            ))
            return issues

        # Check column consistency
        col_counts = []
        for line in lines:
            if '|' in line and not re.match(r'^\s*\|[-:\s|]+\|\s*$', line):
                # Count columns (pipes minus edge pipes)
                cols = line.count('|')
                if line.strip().startswith('|'):
                    cols -= 1
                if line.strip().endswith('|'):
                    cols -= 1
                col_counts.append(cols)

        if col_counts and len(set(col_counts)) > 1:
            issues.append(TableValidationIssue(
                type="inconsistent_columns",
                description=f"Column counts vary: {col_counts}",
                severity="major",
            ))

        # Check for header separator
        if len(lines) > 1:
            second_line = lines[1].strip()
            if not re.match(r'^\|[-:\s|]+\|$', second_line):
                issues.append(TableValidationIssue(
                    type="missing_header_separator",
                    description="Missing header separator row (|---|---|)",
                    severity="major",
                ))

        # Check for empty cells (all cells empty)
        cell_content = re.findall(r'\|([^|]+)\|', table_markdown)
        non_empty = [c for c in cell_content if c.strip() and c.strip() != '-']
        if not non_empty:
            issues.append(TableValidationIssue(
                type="empty_table",
                description="All cells are empty",
                severity="critical",
            ))

        return issues

    def _count_rows(self, table_markdown: str) -> int:
        """Count data rows in table."""
        lines = [l for l in table_markdown.strip().split('\n') if '|' in l]
        # Subtract header separator
        return max(0, len(lines) - 1)

    def _build_table_context(
        self,
        placeholder: TablePlaceholder,
        enhanced: str,
        issues: list[TableValidationIssue],
    ) -> str:
        """Build context for review item."""
        context = f"**Original table:**\n```\n{placeholder.content}\n```\n\n"
        context += f"**Enhanced table:**\n```\n{enhanced}\n```\n\n"

        if issues:
            context += "**Validation issues:**\n"
            for issue in issues:
                context += f"- {issue.type}: {issue.description}\n"

        return context

    def _build_summary(
        self,
        placeholders: list[TablePlaceholder],
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> str:
        """Build human-readable summary."""
        total = len(placeholders)
        auto = len(auto_corrections)
        review = len(review_items)
        return f"Processed {total} tables. {auto} auto-corrected, {review} need review."

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
```

### Agent Configuration

```yaml
# config/agents/tables_enhance.yaml

system_prompt: |
  You are a table enhancement specialist. Your job is to compare extracted
  markdown tables against the visual source and improve accuracy.

  When analyzing tables:
  1. Check if the structure matches (rows, columns, headers)
  2. Verify cell content is accurate
  3. Ensure proper alignment markers
  4. Fix any obvious extraction errors

  Output the enhanced table in valid markdown format.

user_prompt_template: |
  Analyze and enhance this table.

  {prompt}
```

## Acceptance Criteria

### Merging
- [ ] Single TablesAgent class
- [ ] Combines structure + accuracy analysis
- [ ] Uses single LLM call per table (per iteration)

### Validation Loop
- [ ] Python validation checks column consistency
- [ ] Checks for header separator
- [ ] Checks for empty tables
- [ ] Max 2 iterations

### Routing
- [ ] High confidence + valid → auto_corrections
- [ ] Low confidence or issues → review_items
- [ ] Context shows original vs enhanced

### Output
- [ ] Returns AgentResult model
- [ ] Glass box reasoning
- [ ] Cost and timing tracked

## Deliverables

### Files to Create/Modify
```
src/agents/tables/
├── tables_agent.py          # NEW: Merged agent class
├── structure_agent.py       # DEPRECATE
├── accuracy_agent.py        # DEPRECATE

config/agents/
├── tables_enhance.yaml      # NEW: Combined prompts

tests/unit/agents/tables/
├── test_tables_agent.py     # NEW: Unit tests
```

## Definition of Done

- [ ] TablesAgent class implemented
- [ ] Validation loop working
- [ ] Returns AgentResult correctly
- [ ] Unit tests passing
- [ ] Old agents deprecated
