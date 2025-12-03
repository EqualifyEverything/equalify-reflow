"""Unit tests for PIIAnalyzer.

Tests our business logic (threshold filtering, entity type configuration)
without relying on Presidio's internal scoring behavior.
"""

from unittest.mock import Mock, patch

import pytest
from src.services.pii_analyzer import ENTITY_TYPES, PIIAnalyzer, get_pii_analyzer
from src.shared.models.pii import PIIFinding

pytestmark = pytest.mark.unit


class TestPIIAnalyzerInitialization:
    """Test PIIAnalyzer initialization and configuration."""

    def test_default_threshold(self):
        """Test analyzer initializes with default threshold from settings."""
        with patch("src.services.pii_analyzer.AnalyzerEngine"):
            with patch("src.services.pii_analyzer.NlpEngineProvider"):
                with patch("src.services.pii_analyzer.settings") as mock_settings:
                    mock_settings.pii_confidence_threshold = 0.85

                    analyzer = PIIAnalyzer()

                    assert analyzer.confidence_threshold == 0.85

    def test_custom_threshold(self):
        """Test analyzer accepts custom threshold."""
        with patch("src.services.pii_analyzer.AnalyzerEngine"):
            with patch("src.services.pii_analyzer.NlpEngineProvider"):
                analyzer = PIIAnalyzer(confidence_threshold=0.75)

                assert analyzer.confidence_threshold == 0.75

    def test_entity_types_configured(self):
        """Test that only pattern-based entity types are enabled."""
        # This tests our configuration, not Presidio
        expected_types = {
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "US_SSN",
            "CREDIT_CARD",
            "IBAN_CODE",
            "US_DRIVER_LICENSE",
        }

        assert set(ENTITY_TYPES) == expected_types

    def test_ner_types_not_enabled(self):
        """Test that NER-based types are NOT in our configuration."""
        # These cause false positives in course materials
        ner_types = {"PERSON", "DATE_TIME", "LOCATION"}

        assert not (set(ENTITY_TYPES) & ner_types), \
            "NER-based entity types should not be enabled"


class TestThresholdFiltering:
    """Test our threshold filtering logic (core business logic)."""

    @pytest.fixture
    def mock_analyzer_engine(self):
        """Mock Presidio AnalyzerEngine."""
        with patch("src.services.pii_analyzer.AnalyzerEngine") as mock_engine:
            with patch("src.services.pii_analyzer.NlpEngineProvider"):
                mock_engine = mock_engine.return_value
                yield mock_engine

    def test_filters_below_threshold(self, mock_analyzer_engine):
        """Test that findings below threshold are filtered out."""
        # Use realistic text and indices for extraction
        test_text = "Contact: test@example.com, Phone: (555) 123-4567, SSN: 123-45-6789"

        # Mock Presidio to return mixed confidence scores
        mock_results = [
            Mock(entity_type="EMAIL_ADDRESS", score=0.95, start=9, end=26),  # test@example.com
            Mock(entity_type="PHONE_NUMBER", score=0.70, start=35, end=50),  # Below 0.85
            Mock(entity_type="US_SSN", score=0.88, start=57, end=68),        # 123-45-6789
        ]
        mock_analyzer_engine.analyze.return_value = mock_results

        analyzer = PIIAnalyzer(confidence_threshold=0.85)
        findings = analyzer.analyze_text(test_text)

        # Should only return 2 findings (0.95 and 0.88), not 0.70
        assert len(findings) == 2
        assert findings[0].entity_type == "EMAIL_ADDRESS"
        assert findings[0].score == 0.95
        assert findings[1].entity_type == "US_SSN"
        assert findings[1].score == 0.88

    def test_includes_exact_threshold(self, mock_analyzer_engine):
        """Test that findings exactly at threshold are included."""
        mock_results = [
            Mock(entity_type="EMAIL_ADDRESS", score=0.85, start=0, end=20),
        ]
        mock_analyzer_engine.analyze.return_value = mock_results

        analyzer = PIIAnalyzer(confidence_threshold=0.85)
        findings = analyzer.analyze_text("test@example.com")

        # 0.85 >= 0.85, should be included
        assert len(findings) == 1
        assert findings[0].score == 0.85

    def test_filters_all_below_threshold(self, mock_analyzer_engine):
        """Test that empty list returned if all scores below threshold."""
        mock_results = [
            Mock(entity_type="PHONE_NUMBER", score=0.70, start=0, end=14),
            Mock(entity_type="EMAIL_ADDRESS", score=0.60, start=20, end=40),
        ]
        mock_analyzer_engine.analyze.return_value = mock_results

        analyzer = PIIAnalyzer(confidence_threshold=0.85)
        findings = analyzer.analyze_text("low confidence text")

        assert len(findings) == 0

    def test_different_thresholds(self, mock_analyzer_engine):
        """Test filtering works with different threshold values."""
        test_text = "Email: test@example.com, Phone: (555) 123-4567, SSN: 123-45-6789"

        mock_results = [
            Mock(entity_type="EMAIL_ADDRESS", score=0.95, start=7, end=24),   # test@example.com
            Mock(entity_type="PHONE_NUMBER", score=0.75, start=33, end=48),   # (555) 123-4567
            Mock(entity_type="US_SSN", score=0.65, start=55, end=66),         # 123-45-6789
        ]
        mock_analyzer_engine.analyze.return_value = mock_results

        # Threshold 0.70 - should include 0.95 and 0.75
        analyzer_70 = PIIAnalyzer(confidence_threshold=0.70)
        findings_70 = analyzer_70.analyze_text(test_text)
        assert len(findings_70) == 2

        # Threshold 0.85 - should only include 0.95
        analyzer_85 = PIIAnalyzer(confidence_threshold=0.85)
        findings_85 = analyzer_85.analyze_text(test_text)
        assert len(findings_85) == 1


class TestAnalyzeText:
    """Test analyze_text method behavior."""

    @pytest.fixture
    def mock_analyzer_engine(self):
        """Mock Presidio AnalyzerEngine."""
        with patch("src.services.pii_analyzer.AnalyzerEngine") as mock_engine:
            with patch("src.services.pii_analyzer.NlpEngineProvider"):
                mock_engine = mock_engine.return_value
                yield mock_engine

    def test_returns_pii_finding_objects(self, mock_analyzer_engine):
        """Test that results are converted to PIIFinding objects."""
        input_text = "Contact: test@example.com for info"
        mock_results = [
            Mock(entity_type="EMAIL_ADDRESS", score=0.95, start=9, end=25),  # test@example.com
        ]
        mock_analyzer_engine.analyze.return_value = mock_results

        analyzer = PIIAnalyzer(confidence_threshold=0.85)
        findings = analyzer.analyze_text(input_text)

        assert len(findings) == 1
        assert isinstance(findings[0], PIIFinding)
        assert findings[0].entity_type == "EMAIL_ADDRESS"
        assert findings[0].score == 0.95
        assert findings[0].start == 9
        assert findings[0].end == 25
        assert findings[0].text == "test@example.com"

    def test_extracts_text_from_input(self, mock_analyzer_engine):
        """Test that detected PII text is extracted from input."""
        input_text = "Email me at contact@example.com for details"
        mock_results = [
            Mock(entity_type="EMAIL_ADDRESS", score=0.95, start=12, end=31),  # contact@example.com
        ]
        mock_analyzer_engine.analyze.return_value = mock_results

        analyzer = PIIAnalyzer(confidence_threshold=0.85)
        findings = analyzer.analyze_text(input_text)

        # Should extract the actual text using start/end indices
        assert findings[0].text == "contact@example.com"

    def test_calls_presidio_with_correct_params(self, mock_analyzer_engine):
        """Test that Presidio is called with correct parameters."""
        mock_analyzer_engine.analyze.return_value = []

        analyzer = PIIAnalyzer(confidence_threshold=0.85)
        analyzer.analyze_text("test input")

        # Verify Presidio analyze was called correctly
        mock_analyzer_engine.analyze.assert_called_once_with(
            text="test input",
            language="en",
            entities=ENTITY_TYPES
        )

    def test_error_handling(self, mock_analyzer_engine):
        """Test that Presidio errors are propagated."""
        mock_analyzer_engine.analyze.side_effect = Exception("Presidio error")

        analyzer = PIIAnalyzer(confidence_threshold=0.85)

        with pytest.raises(Exception, match="Presidio error"):
            analyzer.analyze_text("test")


class TestGetPIIAnalyzer:
    """Test singleton pattern for global analyzer instance."""

    def test_singleton_returns_same_instance(self):
        """Test that get_pii_analyzer returns singleton."""
        with patch("src.services.pii_analyzer.AnalyzerEngine"):
            with patch("src.services.pii_analyzer.NlpEngineProvider"):
                # Reset singleton
                import src.services.pii_analyzer as module
                module._analyzer_instance = None

                analyzer1 = get_pii_analyzer()
                analyzer2 = get_pii_analyzer()

                assert analyzer1 is analyzer2

                # Cleanup
                module._analyzer_instance = None

    def test_singleton_with_custom_threshold(self):
        """Test singleton uses provided threshold on first call."""
        with patch("src.services.pii_analyzer.AnalyzerEngine"):
            with patch("src.services.pii_analyzer.NlpEngineProvider"):
                # Reset singleton
                import src.services.pii_analyzer as module
                module._analyzer_instance = None

                analyzer = get_pii_analyzer(confidence_threshold=0.75)

                assert analyzer.confidence_threshold == 0.75

                # Cleanup
                module._analyzer_instance = None
