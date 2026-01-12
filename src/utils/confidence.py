"""Hybrid confidence calculation utilities.

This module provides functions for calculating calibrated confidence scores
using a hybrid approach that combines:
- Heuristic penalties based on observable quality signals (80% weight)
- Model self-assessment (20% weight)

The heuristic approach provides more stable, explainable confidence
than pure model self-assessment, which tends to be overconfident.
"""

from __future__ import annotations

from src.shared.models.quality_signals import QualitySignals


def calculate_confidence(signals: QualitySignals, model_confidence: float) -> float:
    """Calculate confidence from quality signals + model self-assessment.

    Uses a hybrid approach:
    - 80% weight on heuristic score (based on observable quality signals)
    - 20% weight on model self-reported confidence

    Applies penalties for:
    - Image/text quality issues (original signals)
    - Low text density (failed extraction indicator)
    - High placeholder ratio (incomplete processing)
    - Vision extraction usage (higher uncertainty)
    - Spacing artifacts (OCR issues)
    - Missing tables/alt-text/headings (accessibility gaps)

    Args:
        signals: Quality signals observed during processing
        model_confidence: Model's self-reported confidence (0.0-1.0)

    Returns:
        Calibrated confidence score (0.0-1.0), rounded to 3 decimal places

    Example:
        >>> signals = QualitySignals(
        ...     image_clarity="blurry",
        ...     text_legibility="clear",
        ...     structure_complexity="complex",
        ...     content_ambiguity="some_ambiguity",
        ...     chars_per_page=50.0,
        ...     vision_extraction_used=True,
        ... )
        >>> calculate_confidence(signals, model_confidence=0.9)
        0.27  # Significantly reduced due to quality and extraction issues
    """
    # Start with perfect heuristic score
    score = 1.0

    # === Original quality signal penalties ===

    # Image clarity penalties
    if signals.image_clarity == "blurry":
        score -= 0.20
    elif signals.image_clarity == "partial":
        score -= 0.30
    elif signals.image_clarity == "missing":
        score -= 0.50

    # Text legibility penalties
    if signals.text_legibility == "faded":
        score -= 0.15
    elif signals.text_legibility == "mixed":
        score -= 0.10
    elif signals.text_legibility == "handwritten":
        score -= 0.25

    # Structure complexity penalties
    if signals.structure_complexity == "moderate":
        score -= 0.05
    elif signals.structure_complexity == "complex":
        score -= 0.15

    # Content ambiguity penalties
    if signals.content_ambiguity == "some_ambiguity":
        score -= 0.10
    elif signals.content_ambiguity == "highly_ambiguous":
        score -= 0.25

    # === NEW: Extraction metric penalties ===

    # Text density penalty (catches failed extractions)
    # < 100 chars/page is very low, < 50 is critical
    if signals.chars_per_page < 50:
        score -= 0.30  # Critical: likely failed extraction
    elif signals.chars_per_page < 100:
        score -= 0.15  # Warning: suspiciously low

    # Vision extraction penalty (higher uncertainty than native text)
    if signals.vision_extraction_used:
        score -= 0.10

    # Placeholder ratio penalty (incomplete processing)
    placeholder_ratio = signals.placeholder_ratio
    if placeholder_ratio > 0.3:
        score -= 0.20  # High ratio = significant incomplete work
    elif placeholder_ratio > 0.1:
        score -= 0.10  # Moderate ratio

    # Spacing artifact penalty (-0.02 each, max -0.10)
    spacing_penalty = min(0.10, signals.spacing_artifact_count * 0.02)
    score -= spacing_penalty

    # Table extraction rate penalty
    if signals.tables_planned > 0:
        table_rate = signals.table_extraction_rate
        if table_rate < 0.5:
            score -= 0.15  # Missing more than half the tables
        elif table_rate < 0.8:
            score -= 0.08  # Missing some tables

    # Alt-text coverage penalty (accessibility)
    if signals.total_images > 0:
        alt_coverage = signals.alt_text_coverage
        if alt_coverage < 0.5:
            score -= 0.20  # Critical accessibility gap
        elif alt_coverage < 0.8:
            score -= 0.10  # Moderate accessibility gap

    # Heading completeness penalty
    if signals.headings_planned > 0:
        heading_rate = signals.heading_completeness
        if heading_rate < 0.5:
            score -= 0.15  # Significant structure issues
        elif heading_rate < 0.8:
            score -= 0.05  # Minor structure issues

    # Clamp heuristic score to valid range
    heuristic_score = max(0.0, min(1.0, score))

    # Clamp model confidence to valid range
    model_confidence = max(0.0, min(1.0, model_confidence))

    # Blend: 80% heuristics, 20% model self-assessment
    final_confidence = 0.8 * heuristic_score + 0.2 * model_confidence

    return round(final_confidence, 3)


def explain_confidence(signals: QualitySignals, model_confidence: float) -> dict:
    """Explain the confidence calculation for debugging/logging.

    Args:
        signals: Quality signals observed during processing
        model_confidence: Model's self-reported confidence (0.0-1.0)

    Returns:
        Dictionary with breakdown of confidence calculation

    Example:
        >>> signals = QualitySignals(image_clarity="blurry", chars_per_page=50.0)
        >>> explain_confidence(signals, 0.9)
        {
            'heuristic_score': 0.5,
            'model_confidence': 0.9,
            'final_confidence': 0.58,
            'penalties': [
                {'signal': 'image_clarity', 'value': 'blurry', 'penalty': -0.2},
                {'signal': 'chars_per_page', 'value': 50.0, 'penalty': -0.3},
            ],
            'weight_heuristic': 0.8,
            'weight_model': 0.2,
        }
    """
    penalties: list[dict] = []
    score = 1.0

    # === Original quality signal penalties ===

    if signals.image_clarity == "blurry":
        penalties.append({"signal": "image_clarity", "value": "blurry", "penalty": -0.20})
        score -= 0.20
    elif signals.image_clarity == "partial":
        penalties.append({"signal": "image_clarity", "value": "partial", "penalty": -0.30})
        score -= 0.30
    elif signals.image_clarity == "missing":
        penalties.append({"signal": "image_clarity", "value": "missing", "penalty": -0.50})
        score -= 0.50

    if signals.text_legibility == "faded":
        penalties.append({"signal": "text_legibility", "value": "faded", "penalty": -0.15})
        score -= 0.15
    elif signals.text_legibility == "mixed":
        penalties.append({"signal": "text_legibility", "value": "mixed", "penalty": -0.10})
        score -= 0.10
    elif signals.text_legibility == "handwritten":
        penalties.append({"signal": "text_legibility", "value": "handwritten", "penalty": -0.25})
        score -= 0.25

    if signals.structure_complexity == "moderate":
        penalties.append({"signal": "structure_complexity", "value": "moderate", "penalty": -0.05})
        score -= 0.05
    elif signals.structure_complexity == "complex":
        penalties.append({"signal": "structure_complexity", "value": "complex", "penalty": -0.15})
        score -= 0.15

    if signals.content_ambiguity == "some_ambiguity":
        penalties.append({"signal": "content_ambiguity", "value": "some_ambiguity", "penalty": -0.10})
        score -= 0.10
    elif signals.content_ambiguity == "highly_ambiguous":
        penalties.append({"signal": "content_ambiguity", "value": "highly_ambiguous", "penalty": -0.25})
        score -= 0.25

    # === NEW: Extraction metric penalties ===

    if signals.chars_per_page < 50:
        penalties.append({
            "signal": "chars_per_page",
            "value": signals.chars_per_page,
            "penalty": -0.30,
        })
        score -= 0.30
    elif signals.chars_per_page < 100:
        penalties.append({
            "signal": "chars_per_page",
            "value": signals.chars_per_page,
            "penalty": -0.15,
        })
        score -= 0.15

    if signals.vision_extraction_used:
        penalties.append({
            "signal": "vision_extraction_used",
            "value": True,
            "penalty": -0.10,
        })
        score -= 0.10

    placeholder_ratio = signals.placeholder_ratio
    if placeholder_ratio > 0.3:
        penalties.append({
            "signal": "placeholder_ratio",
            "value": round(placeholder_ratio, 3),
            "penalty": -0.20,
        })
        score -= 0.20
    elif placeholder_ratio > 0.1:
        penalties.append({
            "signal": "placeholder_ratio",
            "value": round(placeholder_ratio, 3),
            "penalty": -0.10,
        })
        score -= 0.10

    if signals.spacing_artifact_count > 0:
        spacing_penalty = min(0.10, signals.spacing_artifact_count * 0.02)
        penalties.append({
            "signal": "spacing_artifact_count",
            "value": signals.spacing_artifact_count,
            "penalty": -spacing_penalty,
        })
        score -= spacing_penalty

    if signals.tables_planned > 0:
        table_rate = signals.table_extraction_rate
        if table_rate < 0.5:
            penalties.append({
                "signal": "table_extraction_rate",
                "value": round(table_rate, 3),
                "penalty": -0.15,
            })
            score -= 0.15
        elif table_rate < 0.8:
            penalties.append({
                "signal": "table_extraction_rate",
                "value": round(table_rate, 3),
                "penalty": -0.08,
            })
            score -= 0.08

    if signals.total_images > 0:
        alt_coverage = signals.alt_text_coverage
        if alt_coverage < 0.5:
            penalties.append({
                "signal": "alt_text_coverage",
                "value": round(alt_coverage, 3),
                "penalty": -0.20,
            })
            score -= 0.20
        elif alt_coverage < 0.8:
            penalties.append({
                "signal": "alt_text_coverage",
                "value": round(alt_coverage, 3),
                "penalty": -0.10,
            })
            score -= 0.10

    if signals.headings_planned > 0:
        heading_rate = signals.heading_completeness
        if heading_rate < 0.5:
            penalties.append({
                "signal": "heading_completeness",
                "value": round(heading_rate, 3),
                "penalty": -0.15,
            })
            score -= 0.15
        elif heading_rate < 0.8:
            penalties.append({
                "signal": "heading_completeness",
                "value": round(heading_rate, 3),
                "penalty": -0.05,
            })
            score -= 0.05

    heuristic_score = max(0.0, min(1.0, score))
    model_confidence_clamped = max(0.0, min(1.0, model_confidence))
    final_confidence = round(0.8 * heuristic_score + 0.2 * model_confidence_clamped, 3)

    return {
        "heuristic_score": round(heuristic_score, 3),
        "model_confidence": model_confidence,
        "final_confidence": final_confidence,
        "penalties": penalties,
        "weight_heuristic": 0.8,
        "weight_model": 0.2,
    }


def collect_quality_signals(
    page_markdowns: dict[int, str],
    vision_extraction_used: bool = False,
    tables_planned: int = 0,
    tables_found: int = 0,
    images_with_alt: int = 0,
    total_images: int = 0,
    headings_planned: int = 0,
    headings_found: int = 0,
    image_clarity: str = "clear",
    text_legibility: str = "clear",
    structure_complexity: str = "simple",
    content_ambiguity: str = "unambiguous",
) -> QualitySignals:
    """Collect quality signals from processing state.

    Analyzes the markdown output and processing metadata to build a
    QualitySignals object for confidence calculation.

    Args:
        page_markdowns: Dict mapping page numbers to markdown content
        vision_extraction_used: Whether LLM vision was used for extraction
        tables_planned: Number of tables identified in planning
        tables_found: Number of tables successfully transcribed
        images_with_alt: Number of images that have alt-text
        total_images: Total number of images in document
        headings_planned: Number of headings from planning phase
        headings_found: Number of headings in final markdown
        image_clarity: Subjective image quality assessment
        text_legibility: Subjective text legibility assessment
        structure_complexity: Document structure complexity
        content_ambiguity: Content ambiguity level

    Returns:
        QualitySignals object populated with collected metrics

    Example:
        >>> signals = collect_quality_signals(
        ...     page_markdowns={1: "# Title\\n\\nContent here...", 2: "More content..."},
        ...     vision_extraction_used=True,
        ...     tables_planned=2,
        ...     tables_found=1,
        ... )
        >>> signals.chars_per_page
        35.0
        >>> signals.table_extraction_rate
        0.5
    """
    import re

    # Calculate total characters and chars per page
    total_chars = sum(len(md) for md in page_markdowns.values())
    total_pages = len(page_markdowns) if page_markdowns else 1
    chars_per_page = total_chars / total_pages if total_pages > 0 else 0.0

    # Count placeholders across all pages
    combined_markdown = "\n".join(page_markdowns.values())
    placeholder_patterns = [
        r"<!-- image -->",
        r"<!-- table -->",
        r"\[TODO:",
        r"\[IMAGE:",
        r"!\[TODO:",
    ]
    placeholder_count = sum(
        len(re.findall(pattern, combined_markdown, re.IGNORECASE))
        for pattern in placeholder_patterns
    )

    # Count spacing artifacts (letter-spaced words like "r e q u i r e m e n t s")
    # Pattern: 4+ single letters separated by spaces
    spacing_pattern = r'\b(?:[a-zA-Z] ){3,}[a-zA-Z]\b'
    spacing_artifact_count = len(re.findall(spacing_pattern, combined_markdown))

    # Calculate total elements (rough estimate for ratio)
    # Elements include: paragraphs, headings, images, tables, list items
    paragraphs = len(re.findall(r'\n\n', combined_markdown)) + 1
    headings = len(re.findall(r'^#{1,6}\s', combined_markdown, re.MULTILINE))
    list_items = len(re.findall(r'^[-*]\s', combined_markdown, re.MULTILINE))
    total_elements = max(1, paragraphs + headings + list_items + total_images + tables_found)

    return QualitySignals(
        # Subjective quality signals
        image_clarity=image_clarity,  # type: ignore[arg-type]
        text_legibility=text_legibility,  # type: ignore[arg-type]
        structure_complexity=structure_complexity,  # type: ignore[arg-type]
        content_ambiguity=content_ambiguity,  # type: ignore[arg-type]
        # Extraction metrics
        chars_per_page=chars_per_page,
        vision_extraction_used=vision_extraction_used,
        placeholder_count=placeholder_count,
        total_elements=total_elements,
        spacing_artifact_count=spacing_artifact_count,
        tables_found=tables_found,
        tables_planned=tables_planned,
        images_with_alt=images_with_alt,
        total_images=total_images,
        headings_found=headings_found if headings_found > 0 else headings,
        headings_planned=headings_planned,
    )


__all__ = ["calculate_confidence", "explain_confidence", "collect_quality_signals"]
