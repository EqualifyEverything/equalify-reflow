# PRD-020: Hybrid Confidence & Glass-Box Reasoning System

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation (Quality Assurance)
**Estimated Effort**: 3 days
**Dependencies**: PRD-018 (Infrastructure Consolidation), PRD-014 (Specialized Agents)
**Source**: Agent Infrastructure Refactoring Plan (Phases 2, 5.3, 5.4)

## Problem Statement

The current confidence scoring system has limitations:

1. **Self-Assessment Bias**: LLMs asked to rate their own confidence tend to be overconfident or uncalibrated. A model saying "0.9 confident" doesn't reliably predict accuracy.

2. **Opaque Decisions**: When agents make determinations (layout type, image classification, severity), we have no visibility into *why* they chose that value.

3. **No Reasoning Corpus**: Without captured reasoning, we cannot:
   - Analyze decision patterns across jobs
   - Identify systematic errors
   - Train future models on successful reasoning

### Current State

```python
# Current: Model self-reports confidence (unreliable)
class ImageAnalysis(BaseModel):
    image_type: Literal["decorative", "informative", "complex", "text"]
    confidence: float = 0.8  # Model just guesses
```

### Target State

```python
# Target: Hybrid confidence from quality signals + reasoned determinations
class ImageAnalysis(BaseModel, ReasonedOutputMixin):
    image_type: Reasoned[Literal["decorative", "informative", "complex", "text"]]
    # reasoning: "Large chart with axis labels visible. Contains data."
    # value: "informative"

    quality_signals: QualitySignals
    # image_clarity: "clear"
    # text_legibility: "clear"
    # structure_complexity: "moderate"

# Confidence calculated programmatically:
# final_confidence = calculate_confidence(signals, model_confidence=0.8)
```

## Success Criteria

- [ ] `QualitySignals` model for programmatic confidence inputs
- [ ] `calculate_confidence()` function blending heuristics + model assessment
- [ ] `Reasoned[T]` applied to complex determinations in all specialized agents
- [ ] Reasoning corpus service logging all reasoning entries
- [ ] Analysis dashboard can query reasoning patterns

## Technical Requirements

### 1. Quality Signals Model

```python
# src/shared/models/quality_signals.py

from typing import Literal

from pydantic import BaseModel, Field


class QualitySignals(BaseModel):
    """Signals detected by model for programmatic confidence calculation.

    These are observable qualities that correlate with confidence,
    rather than the model's self-reported confidence.
    """

    image_clarity: Literal["clear", "blurry", "partial", "missing"] = Field(
        default="clear",
        description=(
            "Quality of page images. "
            "'clear'=sharp, readable; "
            "'blurry'=degraded quality affecting analysis; "
            "'partial'=cropped or incomplete page; "
            "'missing'=no image available"
        ),
    )

    text_legibility: Literal["clear", "faded", "mixed", "handwritten"] = Field(
        default="clear",
        description=(
            "Legibility of text on page. "
            "'clear'=typed, sharp contrast; "
            "'faded'=low contrast or degraded; "
            "'mixed'=varies across page; "
            "'handwritten'=manual writing present"
        ),
    )

    structure_complexity: Literal["simple", "moderate", "complex"] = Field(
        default="simple",
        description=(
            "Document structure complexity. "
            "'simple'=single column, clear headings; "
            "'moderate'=some tables/lists, multi-section; "
            "'complex'=nested tables, multi-column, merged cells"
        ),
    )

    content_ambiguity: Literal["unambiguous", "some_ambiguity", "highly_ambiguous"] = Field(
        default="unambiguous",
        description=(
            "How clear the content meaning is. "
            "'unambiguous'=obvious structure and intent; "
            "'some_ambiguity'=unclear heading levels or reading order; "
            "'highly_ambiguous'=multiple valid interpretations possible"
        ),
    )
```

### 2. Confidence Calculator

```python
# src/utils/confidence.py

import logging

from src.shared.models.quality_signals import QualitySignals

logger = logging.getLogger(__name__)


# Penalty weights for quality issues
PENALTIES = {
    # Image clarity
    "image_clarity": {
        "clear": 0.0,
        "blurry": 0.20,
        "partial": 0.30,
        "missing": 0.50,
    },
    # Text legibility
    "text_legibility": {
        "clear": 0.0,
        "faded": 0.15,
        "mixed": 0.10,
        "handwritten": 0.25,
    },
    # Structure complexity
    "structure_complexity": {
        "simple": 0.0,
        "moderate": 0.05,
        "complex": 0.15,
    },
    # Content ambiguity
    "content_ambiguity": {
        "unambiguous": 0.0,
        "some_ambiguity": 0.10,
        "highly_ambiguous": 0.25,
    },
}


def calculate_confidence(
    signals: QualitySignals,
    model_confidence: float = 0.8,
    heuristic_weight: float = 0.8,
) -> float:
    """Calculate confidence from quality signals + model self-assessment.

    Uses a weighted blend of heuristic penalties and model's self-reported
    confidence. Heuristics are weighted higher (default 80%) because
    LLM self-assessment tends to be overconfident.

    Args:
        signals: Quality signals observed during analysis
        model_confidence: Model's self-reported confidence (0.0-1.0)
        heuristic_weight: Weight for heuristic score (0.0-1.0)

    Returns:
        Final confidence score (0.0-1.0)

    Example:
        >>> signals = QualitySignals(
        ...     image_clarity="blurry",  # -0.20
        ...     text_legibility="clear",  # -0.00
        ...     structure_complexity="complex",  # -0.15
        ...     content_ambiguity="unambiguous",  # -0.00
        ... )
        >>> calculate_confidence(signals, model_confidence=0.9)
        0.692  # (0.8 * 0.65) + (0.2 * 0.9) = 0.52 + 0.18 = 0.70
    """
    # Calculate heuristic score from penalties
    penalty = 0.0
    penalty += PENALTIES["image_clarity"].get(signals.image_clarity, 0.0)
    penalty += PENALTIES["text_legibility"].get(signals.text_legibility, 0.0)
    penalty += PENALTIES["structure_complexity"].get(signals.structure_complexity, 0.0)
    penalty += PENALTIES["content_ambiguity"].get(signals.content_ambiguity, 0.0)

    heuristic_score = max(0.0, min(1.0, 1.0 - penalty))

    # Blend with model confidence
    model_weight = 1.0 - heuristic_weight
    final_confidence = (heuristic_weight * heuristic_score) + (model_weight * model_confidence)

    # Ensure bounds
    final_confidence = max(0.0, min(1.0, final_confidence))

    logger.debug(
        f"Confidence calculated: heuristic={heuristic_score:.3f}, "
        f"model={model_confidence:.3f}, final={final_confidence:.3f}"
    )

    return round(final_confidence, 3)
```

### 3. Apply Reasoned[T] to Agent Output Models

The `Reasoned[T]` wrapper already exists in `src/shared/models/reasoned.py`. Now apply it to complex determinations in specialized agent models:

```python
# src/agents/specialized_models.py (updated)

from src.shared.models.reasoned import Reasoned, ReasonedOutputMixin
from src.shared.models.quality_signals import QualitySignals


class ImageAnalysis(BaseModel, ReasonedOutputMixin):
    """Analysis of a single image with glass-box reasoning."""

    image_index: int = Field(..., ge=1, description="Image number on this page (1-indexed)")

    # Complex determinations get Reasoned[T] wrapper
    image_type: Reasoned[Literal["decorative", "informative", "complex", "text"]] = Field(
        ...,
        description="Classification with reasoning about why this type was chosen"
    )

    recommended_action: Reasoned[Literal["add_alt", "improve_alt", "mark_decorative", "add_long_desc", "none"]] = Field(
        ...,
        description="Recommended action with reasoning about why"
    )

    # Simple fields don't need reasoning
    visual_description: str = Field(..., description="What the image visually depicts")
    current_alt_status: str = Field(..., description="Current alt text status")
    suggested_alt: str | None = Field(default=None, description="Suggested alt text")

    # Quality signals for confidence calculation
    quality_signals: QualitySignals = Field(
        default_factory=QualitySignals,
        description="Observable quality signals for confidence calculation"
    )

    # Model's raw confidence (used in hybrid calculation)
    model_confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @property
    def confidence(self) -> float:
        """Calculate hybrid confidence from signals + model assessment."""
        from src.utils.confidence import calculate_confidence
        return calculate_confidence(self.quality_signals, self.model_confidence)


class StructureIssue(BaseModel, ReasonedOutputMixin):
    """A structural issue with glass-box reasoning."""

    issue_type: Reasoned[Literal["heading_skip", "heading_mismatch", "reading_order", "missing_landmark"]]
    severity: Reasoned[Literal["critical", "major", "minor"]]

    # Simple fields
    location_description: str
    visual_evidence: str
    markup_state: str
    recommended_fix: str

    quality_signals: QualitySignals = Field(default_factory=QualitySignals)
    model_confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @property
    def confidence(self) -> float:
        from src.utils.confidence import calculate_confidence
        return calculate_confidence(self.quality_signals, self.model_confidence)
```

### 4. Reasoning Corpus Service

```python
# src/services/reasoning_corpus_service.py

import logging
from datetime import UTC, datetime
from typing import Any

from src.shared.models.reasoned import ReasonedOutputMixin

logger = logging.getLogger(__name__)


class ReasoningCorpusService:
    """Service for capturing and analyzing agent reasoning.

    Provides glass-box visibility into model decision patterns
    by capturing reasoning for all Reasoned[T] fields.

    SECURITY NOTE: Reasoning may contain document content.
    See class-level security documentation for access controls.
    """

    async def log_reasoning_from_output(
        self,
        output: ReasonedOutputMixin,
        job_id: str,
        agent_name: str,
        page_num: int | None = None,
    ) -> None:
        """Extract and log all reasoning from an agent output.

        Args:
            output: Any model with ReasonedOutputMixin
            job_id: Job identifier
            agent_name: Name of the agent that produced this output
            page_num: Page number if page-specific
        """
        corpus = output.extract_reasoning_corpus()

        for entry in corpus:
            await self._log_entry(
                job_id=job_id,
                agent_name=agent_name,
                page_num=page_num,
                **entry,
            )

    async def _log_entry(
        self,
        job_id: str,
        agent_name: str,
        field: str,
        reasoning: str,
        value: Any,
        model_class: str,
        page_num: int | None = None,
    ) -> None:
        """Log a single reasoning entry via structured logging.

        In production, this would also write to a reasoning corpus
        database or S3 bucket for later analysis.
        """
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "job_id": job_id,
            "agent": agent_name,
            "page_num": page_num,
            "model_class": model_class,
            "field": field,
            "reasoning": reasoning,
            "value": str(value),
            "reasoning_length": len(reasoning),
            "sentence_count": reasoning.count(". ") + reasoning.count("! ") + 1,
        }

        logger.info(
            "reasoning_corpus_entry",
            extra={"reasoning_data": entry}
        )

    async def query_reasoning_patterns(
        self,
        agent_name: str | None = None,
        field: str | None = None,
        min_length: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query reasoning corpus for pattern analysis.

        Args:
            agent_name: Filter by agent
            field: Filter by field name
            min_length: Minimum reasoning length
            limit: Maximum results

        Returns:
            List of reasoning entries matching criteria

        Note:
            In production, this would query from a database.
            Current implementation returns empty (logging only).
        """
        # TODO: Implement when corpus storage is added
        logger.warning("Reasoning corpus query not yet implemented (logging only)")
        return []
```

### 5. Integration with Agents

```python
# src/agents/figures_agent.py (updated analyze method)

async def analyze(
    self,
    pages: list[PageData],
    manifest: DocumentManifest,
    markdown: str,
    job_id: str,
) -> tuple[list[Observation], LLMUsage]:
    """Analyze images with reasoning capture."""
    observations: list[Observation] = []
    usages: list[LLMUsage] = []
    reasoning_service = ReasoningCorpusService()

    for page in pages:
        # ... existing page processing ...

        result = await self._run_agent(...)
        output: FiguresAnalysisOutput = result.output

        # Capture reasoning from all analyses
        for analysis in output.analyses:
            await reasoning_service.log_reasoning_from_output(
                output=analysis,
                job_id=job_id,
                agent_name="figures",
                page_num=page.page_num,
            )

            # Create observation with hybrid confidence
            if analysis.recommended_action.value != "none":
                obs = Observation(
                    # ... other fields ...
                    confidence=analysis.confidence,  # Uses property with hybrid calc
                    reasoning=analysis.image_type.reasoning,  # Glass-box visibility
                )
                observations.append(obs)

    return observations, self._aggregate_usage(usages)
```

## Acceptance Criteria

### 1. Quality Signals
- [ ] `QualitySignals` model with 4 signal dimensions
- [ ] All signals have Literal type with clear descriptions
- [ ] Agents populate signals during analysis

### 2. Confidence Calculator
- [ ] `calculate_confidence()` function implemented
- [ ] Heuristic penalties configurable
- [ ] 80/20 blend of heuristics vs model confidence
- [ ] Tests cover edge cases (all good, all bad, mixed)

### 3. Reasoned[T] Integration
- [ ] `ImageAnalysis` uses `Reasoned[T]` for type and action
- [ ] `StructureIssue` uses `Reasoned[T]` for type and severity
- [ ] `TableAnalysis` uses `Reasoned[T]` for complexity assessment
- [ ] `TypographyIssue` uses `Reasoned[T]` for issue type
- [ ] All models inherit `ReasonedOutputMixin`

### 4. Reasoning Corpus
- [ ] `ReasoningCorpusService` logs all reasoning entries
- [ ] Structured logging with consistent schema
- [ ] Integration with specialized agents
- [ ] Query interface documented (future implementation)

### 5. Prompt Updates
- [ ] Agent prompts guide quality signal assessment
- [ ] Prompts explain reasoning format requirements
- [ ] Examples show proper signal/reasoning patterns

## Deliverables

### Files to Create
```
src/shared/models/
└── quality_signals.py         # QualitySignals model

src/utils/
└── confidence.py              # calculate_confidence()

src/services/
└── reasoning_corpus_service.py  # Exists, update implementation
```

### Files to Modify
```
src/agents/specialized_models.py  # Add Reasoned[T], QualitySignals
src/agents/figures_agent.py       # Integrate reasoning capture
src/agents/tables_agent.py        # Integrate reasoning capture
src/agents/structure_agent.py     # Integrate reasoning capture
src/agents/typography_agent.py    # Integrate reasoning capture

config/agents/*.yaml              # Add quality signal guidance
```

### Tests to Create
```
tests/unit/utils/
└── test_confidence.py

tests/unit/models/
└── test_quality_signals.py

tests/unit/services/
└── test_reasoning_corpus_service.py
```

## Technical Notes

### Why Hybrid Confidence?

| Approach | Pros | Cons |
|----------|------|------|
| Model self-assessment only | Simple | Uncalibrated, overconfident |
| Heuristics only | Predictable | Ignores model insight |
| **Hybrid (this PRD)** | **Calibrated, explainable** | **More complex** |

The 80/20 blend weights observable signals heavily while still incorporating model insight.

### Reasoning Corpus Use Cases

1. **Debugging**: Why did agent X make decision Y for job Z?
2. **Pattern Analysis**: Are agents overusing certain reasoning patterns?
3. **Training Data**: Export successful reasoning for fine-tuning
4. **Audit Trail**: Compliance review of automated decisions

### Cost Impact

Adding `quality_signals` and `Reasoned[T]` increases output token count:
- ~50-100 extra tokens per analysis item
- Estimated +10% cost for specialized agents
- Valuable for quality improvement and debugging

## Definition of Done

- [ ] `QualitySignals` model implemented and tested
- [ ] `calculate_confidence()` function implemented and tested
- [ ] `Reasoned[T]` applied to all complex determinations
- [ ] Reasoning corpus service capturing all entries
- [ ] Agent prompts updated with signal/reasoning guidance
- [ ] Hybrid confidence used for routing decisions
- [ ] Documentation complete
- [ ] All tests pass
