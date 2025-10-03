"""Presidio PII detection analyzer wrapper."""

import logging
from typing import List

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

from ..config import settings
from ..shared.models.pii import PIIFinding

logger = logging.getLogger(__name__)

# PII entity types to detect
# NOTE: Only pattern-based detectors are enabled to reduce false positives
# Removed NER-based detectors (PERSON, DATE_TIME, LOCATION) which flagged
# technical terms, company names, and job titles as PII in course materials
ENTITY_TYPES = [
    "EMAIL_ADDRESS",       # Email addresses (pattern-based)
    "PHONE_NUMBER",        # Phone numbers (pattern-based)
    "US_SSN",              # Social Security Numbers (pattern-based)
    "CREDIT_CARD",         # Credit card numbers (pattern-based, Luhn check)
    "IBAN_CODE",           # Bank account numbers (pattern-based)
    "US_DRIVER_LICENSE",   # Driver's license numbers (pattern-based)
]

# Confidence threshold for PII detection
# Configurable via settings.pii_confidence_threshold (default 0.85)
DEFAULT_CONFIDENCE_THRESHOLD = settings.pii_confidence_threshold


class PIIAnalyzer:
    """Wrapper for Microsoft Presidio PII detection.

    Configures and manages Presidio AnalyzerEngine for detecting
    personally identifiable information in text content.

    Uses pattern-based detectors only (EMAIL, PHONE, SSN, CREDIT_CARD,
    IBAN, DRIVER_LICENSE) to minimize false positives in course materials.
    NER-based detectors (PERSON, DATE_TIME, LOCATION) are disabled as they
    frequently misidentify technical terms, company names, and job titles.

    Attributes:
        analyzer: Presidio AnalyzerEngine instance
        confidence_threshold: Minimum confidence score (0.0-1.0, default 0.85)
    """

    def __init__(self, confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD):
        """Initialize Presidio analyzer with spaCy NLP engine.

        Args:
            confidence_threshold: Minimum confidence score (0.0-1.0)
        """
        self.confidence_threshold = confidence_threshold

        # Configure spaCy NLP engine
        nlp_configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]
        }

        # Create NLP engine
        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()

        # Initialize Presidio analyzer
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine)

        logger.info(f"Initialized PIIAnalyzer with threshold {confidence_threshold}")

    def analyze_text(self, text: str) -> List[PIIFinding]:
        """Analyze text for PII using Presidio.

        Args:
            text: Plain text to scan for PII

        Returns:
            List[PIIFinding]: Detected PII entities above confidence threshold

        Example:
            >>> analyzer = PIIAnalyzer()
            >>> findings = analyzer.analyze_text("Contact John Doe at john@example.com")
            >>> len(findings) > 0
            True
            >>> findings[0].entity_type in ["PERSON", "EMAIL_ADDRESS"]
            True
        """
        try:
            # Run Presidio analysis
            results = self.analyzer.analyze(
                text=text,
                language="en",
                entities=ENTITY_TYPES
            )

            # Convert to PIIFinding models, filtering by confidence
            findings = [
                PIIFinding(
                    entity_type=result.entity_type,
                    start=result.start,
                    end=result.end,
                    score=result.score,
                    text=text[result.start:result.end]
                )
                for result in results
                if result.score >= self.confidence_threshold
            ]

            logger.info(f"Found {len(findings)} PII entities above threshold {self.confidence_threshold}")
            return findings

        except Exception as e:
            logger.error(f"PII analysis failed: {e}")
            raise


# Global analyzer instance (lazy-loaded)
_analyzer_instance: PIIAnalyzer | None = None


def get_pii_analyzer(confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> PIIAnalyzer:
    """Get or create global PIIAnalyzer instance.

    Lazy-loads the analyzer to avoid initialization overhead.

    Args:
        confidence_threshold: Minimum confidence score (0.0-1.0)

    Returns:
        PIIAnalyzer: Shared analyzer instance
    """
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = PIIAnalyzer(confidence_threshold=confidence_threshold)
    return _analyzer_instance
