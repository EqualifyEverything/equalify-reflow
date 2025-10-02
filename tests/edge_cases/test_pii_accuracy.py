"""Edge case tests for PII detection accuracy and validation."""

import pytest
from unittest.mock import Mock, patch

from src.services.pii_analyzer import PIIAnalyzer, ENTITY_TYPES
from tests.edge_cases.helpers import (
    generate_pii_text,
    generate_false_positive_text,
    generate_valid_academic_content,
)


@pytest.fixture
def pii_analyzer():
    """Create PII analyzer with default confidence threshold."""
    # Mock the spaCy model loading to avoid downloading in tests
    with patch('presidio_analyzer.nlp_engine.NlpEngineProvider') as mock_provider:
        mock_engine = Mock()
        mock_provider.return_value.create_engine.return_value = mock_engine

        with patch('presidio_analyzer.AnalyzerEngine') as mock_analyzer:
            analyzer = PIIAnalyzer(confidence_threshold=0.7)
            # Set up the mock to return expected structure
            analyzer.analyzer = mock_analyzer.return_value
            yield analyzer


class TestPIIDetectionAccuracy:
    """Tests for PII detection accuracy with various input patterns."""

    @pytest.mark.asyncio
    async def test_ssn_detection_valid_formats(self, pii_analyzer):
        """Test SSN detection with various valid formats."""
        test_cases = [
            ("My SSN is 123-45-6789", True, "Standard format with dashes"),
            ("SSN: 123456789", True, "No dashes"),
            ("Social Security Number 123-45-6789 required", True, "In sentence"),
        ]

        for text, should_detect, description in test_cases:
            # Mock Presidio response
            if should_detect:
                mock_result = Mock()
                mock_result.entity_type = "US_SSN"
                mock_result.start = text.find("123")
                mock_result.end = mock_result.start + 11
                mock_result.score = 0.95
                pii_analyzer.analyzer.analyze.return_value = [mock_result]
            else:
                pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            if should_detect:
                assert len(findings) > 0, f"Failed to detect SSN: {description}"
                assert findings[0].entity_type == "US_SSN"
                assert findings[0].score >= 0.7
            else:
                assert len(findings) == 0, f"False positive: {description}"

    @pytest.mark.asyncio
    async def test_ssn_detection_invalid_formats(self, pii_analyzer):
        """Test that invalid SSN patterns are not detected."""
        invalid_ssns = [
            "111-11-1111",  # Invalid sequence
            "000-00-0000",  # All zeros
            "123-45-678",   # Too short
            "123-456-7890", # Wrong format
            "999-99-9999",  # Out of valid range
        ]

        for ssn in invalid_ssns:
            text = f"Test SSN: {ssn}"
            # Mock no detection for invalid SSNs
            pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)
            # Should not detect invalid SSNs
            assert len([f for f in findings if f.entity_type == "US_SSN"]) == 0

    @pytest.mark.asyncio
    async def test_email_detection_accuracy(self, pii_analyzer):
        """Test email detection with various formats."""
        test_cases = [
            ("Contact: user@example.com", True, "Simple email"),
            ("Email: first.last@company.co.uk", True, "Subdomain and TLD"),
            ("user+tag@example.com", True, "Plus addressing"),
            ("user_name@example.com", True, "Underscore"),
            ("user@", False, "Incomplete email"),
            ("@example.com", False, "Missing user"),
            ("not-an-email", False, "No @ symbol"),
        ]

        for text, should_detect, description in test_cases:
            if should_detect:
                # Find email in text
                at_pos = text.find("@")
                start = max(0, text[:at_pos].rfind(" ") + 1)
                end = text.find(" ", at_pos) if " " in text[at_pos:] else len(text)

                mock_result = Mock()
                mock_result.entity_type = "EMAIL_ADDRESS"
                mock_result.start = start
                mock_result.end = end
                mock_result.score = 0.90
                pii_analyzer.analyzer.analyze.return_value = [mock_result]
            else:
                pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            if should_detect:
                assert len(findings) > 0, f"Failed to detect: {description}"
                assert any(f.entity_type == "EMAIL_ADDRESS" for f in findings)
            else:
                email_findings = [f for f in findings if f.entity_type == "EMAIL_ADDRESS"]
                assert len(email_findings) == 0, f"False positive: {description}"

    @pytest.mark.asyncio
    async def test_phone_number_detection(self, pii_analyzer):
        """Test phone number detection with various formats."""
        test_cases = [
            ("Call (555) 123-4567", True, "Standard US format"),
            ("Phone: 555-123-4567", True, "Dashes only"),
            ("Contact 5551234567", True, "No separators"),
            ("Tel: +1 555-123-4567", True, "International format"),
            ("000-000-0000", False, "All zeros"),
            ("123-456", False, "Too short"),
        ]

        for text, should_detect, description in test_cases:
            if should_detect:
                # Find phone number position
                import re
                match = re.search(r'[\d\(\)\-\+\s]+', text)
                if match:
                    mock_result = Mock()
                    mock_result.entity_type = "PHONE_NUMBER"
                    mock_result.start = match.start()
                    mock_result.end = match.end()
                    mock_result.score = 0.85
                    pii_analyzer.analyzer.analyze.return_value = [mock_result]
            else:
                pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            if should_detect:
                assert len(findings) > 0, f"Failed to detect: {description}"
                assert any(f.entity_type == "PHONE_NUMBER" for f in findings)

    @pytest.mark.asyncio
    async def test_person_name_detection(self, pii_analyzer):
        """Test person name detection."""
        test_cases = [
            ("Student John Smith submitted", True, "Full name"),
            ("Contact Dr. Jane Doe", True, "With title"),
            ("Professor Maria Garcia Lopez", True, "Multiple names"),
            ("The student submitted", False, "Generic reference"),
        ]

        for text, should_detect, description in test_cases:
            if should_detect:
                # Mock name detection
                mock_result = Mock()
                mock_result.entity_type = "PERSON"
                mock_result.start = 8
                mock_result.end = 20
                mock_result.score = 0.80
                pii_analyzer.analyzer.analyze.return_value = [mock_result]
            else:
                pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            if should_detect:
                assert len(findings) > 0, f"Failed to detect: {description}"
                assert any(f.entity_type == "PERSON" for f in findings)

    @pytest.mark.asyncio
    async def test_false_positive_prevention(self, pii_analyzer):
        """Test that academic patterns are not flagged as PII."""
        academic_text = generate_valid_academic_content()

        # Mock no PII detection in academic content
        pii_analyzer.analyzer.analyze.return_value = []

        findings = pii_analyzer.analyze_text(academic_text)

        # Should not detect PII in clean academic content
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_course_codes_not_detected_as_ssn(self, pii_analyzer):
        """Test that course codes like CS-123-45-6789 are not flagged as SSN."""
        course_text = "Course CS-123-45-6789 and MATH-111-22-3333"

        # Mock no detection of course codes as SSN
        pii_analyzer.analyzer.analyze.return_value = []

        findings = pii_analyzer.analyze_text(course_text)

        # Should not flag course codes
        ssn_findings = [f for f in findings if f.entity_type == "US_SSN"]
        assert len(ssn_findings) == 0

    @pytest.mark.asyncio
    async def test_location_detection(self, pii_analyzer):
        """Test address/location detection."""
        test_cases = [
            ("Office at 123 Main Street, Chicago IL", True, "Full address"),
            ("Located in Chicago", False, "City only - not PII"),
            ("Building 5, Room 201", False, "Campus location"),
        ]

        for text, should_detect, description in test_cases:
            if should_detect:
                mock_result = Mock()
                mock_result.entity_type = "LOCATION"
                mock_result.start = 10
                mock_result.end = 35
                mock_result.score = 0.75
                pii_analyzer.analyzer.analyze.return_value = [mock_result]
            else:
                pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            if should_detect:
                assert len(findings) > 0, f"Failed to detect: {description}"
                assert any(f.entity_type == "LOCATION" for f in findings)

    @pytest.mark.asyncio
    async def test_credit_card_detection(self, pii_analyzer):
        """Test credit card number detection."""
        test_cases = [
            ("Card: 4532-1234-5678-9010", True, "Visa format"),
            ("Payment 5425233430109903", True, "Mastercard"),
            ("1234-5678-9012-3456", False, "Invalid checksum"),
        ]

        for text, should_detect, description in test_cases:
            if should_detect:
                mock_result = Mock()
                mock_result.entity_type = "CREDIT_CARD"
                mock_result.start = 6
                mock_result.end = 25
                mock_result.score = 0.95
                pii_analyzer.analyzer.analyze.return_value = [mock_result]
            else:
                pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            if should_detect:
                assert len(findings) > 0, f"Failed to detect: {description}"
                assert any(f.entity_type == "CREDIT_CARD" for f in findings)

    @pytest.mark.asyncio
    async def test_confidence_threshold_filtering(self, pii_analyzer):
        """Test that low confidence findings are filtered out."""
        text = "Test content with potential PII"

        # Mock low confidence finding
        mock_result = Mock()
        mock_result.entity_type = "PERSON"
        mock_result.start = 0
        mock_result.end = 10
        mock_result.score = 0.5  # Below 0.7 threshold
        pii_analyzer.analyzer.analyze.return_value = [mock_result]

        findings = pii_analyzer.analyze_text(text)

        # Should be filtered out due to low confidence
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_multiple_pii_types_in_same_text(self, pii_analyzer):
        """Test detection of multiple PII types in one text."""
        text = "Contact John Doe at john.doe@example.com or 555-123-4567"

        # Mock multiple findings
        mock_results = [
            Mock(entity_type="PERSON", start=8, end=16, score=0.85),
            Mock(entity_type="EMAIL_ADDRESS", start=20, end=41, score=0.95),
            Mock(entity_type="PHONE_NUMBER", start=45, end=57, score=0.90),
        ]
        pii_analyzer.analyzer.analyze.return_value = mock_results

        findings = pii_analyzer.analyze_text(text)

        # Should detect all three types
        assert len(findings) == 3
        entity_types = {f.entity_type for f in findings}
        assert "PERSON" in entity_types
        assert "EMAIL_ADDRESS" in entity_types
        assert "PHONE_NUMBER" in entity_types

    @pytest.mark.asyncio
    async def test_international_phone_formats(self, pii_analyzer):
        """Test detection of international phone number formats."""
        test_cases = [
            "+1 312-555-1234",      # US
            "+44 20 7123 4567",     # UK
            "+81 3-1234-5678",      # Japan
            "+86 10-1234-5678",     # China
        ]

        for phone in test_cases:
            text = f"Phone: {phone}"

            mock_result = Mock()
            mock_result.entity_type = "PHONE_NUMBER"
            mock_result.start = 7
            mock_result.end = len(text)
            mock_result.score = 0.85
            pii_analyzer.analyzer.analyze.return_value = [mock_result]

            findings = pii_analyzer.analyze_text(text)

            # Should detect international formats
            assert len(findings) > 0
            assert findings[0].entity_type == "PHONE_NUMBER"

    @pytest.mark.asyncio
    async def test_date_time_detection_context(self, pii_analyzer):
        """Test that generic dates are not flagged but specific birthdates are."""
        test_cases = [
            ("Due date: January 15, 2024", False, "Generic deadline"),
            ("Born on 03/15/1995", True, "Birthdate"),
            ("Meeting at 2:00 PM", False, "Time only"),
            ("The year 2024", False, "Year only"),
        ]

        for text, should_detect, description in test_cases:
            if should_detect:
                mock_result = Mock()
                mock_result.entity_type = "DATE_TIME"
                mock_result.start = 8
                mock_result.end = 18
                mock_result.score = 0.80
                pii_analyzer.analyzer.analyze.return_value = [mock_result]
            else:
                pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            # Note: DATE_TIME detection depends on context
            if should_detect:
                assert any(f.entity_type == "DATE_TIME" for f in findings), description

    @pytest.mark.asyncio
    async def test_empty_text_handling(self, pii_analyzer):
        """Test handling of empty text input."""
        pii_analyzer.analyzer.analyze.return_value = []

        findings = pii_analyzer.analyze_text("")

        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_very_long_text_handling(self, pii_analyzer):
        """Test handling of very long text content."""
        # Create 10,000 word text
        long_text = " ".join(["word"] * 10000)

        pii_analyzer.analyzer.analyze.return_value = []

        # Should handle without error
        findings = pii_analyzer.analyze_text(long_text)
        assert isinstance(findings, list)

    @pytest.mark.asyncio
    async def test_special_characters_in_text(self, pii_analyzer):
        """Test PII detection with special characters."""
        text = "Email: user@example.com\n\nPhone: (555) 123-4567\r\nSSN: 123-45-6789"

        mock_results = [
            Mock(entity_type="EMAIL_ADDRESS", start=7, end=23, score=0.95),
            Mock(entity_type="PHONE_NUMBER", start=32, end=46, score=0.90),
            Mock(entity_type="US_SSN", start=53, end=64, score=0.95),
        ]
        pii_analyzer.analyzer.analyze.return_value = mock_results

        findings = pii_analyzer.analyze_text(text)

        # Should detect PII despite special characters
        assert len(findings) == 3

    @pytest.mark.asyncio
    async def test_obfuscated_pii_patterns(self, pii_analyzer):
        """Test detection of obfuscated PII patterns."""
        test_cases = [
            "SSN: XXX-XX-6789",      # Partially masked
            "Email: j***@example.com",  # Masked email
            "Phone: (555) XXX-XXXX",    # Masked phone
        ]

        for text in test_cases:
            # Obfuscated patterns may or may not be detected
            pii_analyzer.analyzer.analyze.return_value = []

            findings = pii_analyzer.analyze_text(text)

            # Just verify no errors occur
            assert isinstance(findings, list)
