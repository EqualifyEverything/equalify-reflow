"""Presidio PII detection analyzer wrapper."""

import logging
from typing import List

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

from ..shared.models.pii import PIIFinding

logger = logging.getLogger(__name__)

# PII entity types to detect
ENTITY_TYPES = [
    "PERSON",              # Names
    "EMAIL_ADDRESS",       # Email addresses
    "PHONE_NUMBER",        # Phone numbers
    "US_SSN",              # Social Security Numbers
    "CREDIT_CARD",         # Credit card numbers
    "IBAN_CODE",           # Bank account numbers
    "US_DRIVER_LICENSE",   # Driver's license numbers
    "DATE_TIME",           # Specific dates
    "LOCATION",            # Addresses
]

# Confidence threshold for PII detection
DEFAULT_CONFIDENCE_THRESHOLD = 0.7


class PIIAnalyzer:
    """Wrapper for Microsoft Presidio PII detection.

    Configures and manages Presidio AnalyzerEngine for detecting
    personally identifiable information in text content.

    Attributes:
        analyzer: Presidio AnalyzerEngine instance
        confidence_threshold: Minimum confidence score (0.0-1.0)
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
