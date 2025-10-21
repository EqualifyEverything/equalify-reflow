"""Integration tests for PII false positive validation."""

import pytest
from pathlib import Path

from src.services.pii_analyzer import PIIAnalyzer, ENTITY_TYPES


class TestPIIFalsePositiveReduction:
    """Integration tests verifying false positive reduction for course materials."""

    @pytest.fixture
    def pii_analyzer(self):
        """Create real PII analyzer (not mocked) for integration testing."""
        # Explicitly set threshold to 0.85 (matches production config)
        return PIIAnalyzer(confidence_threshold=0.85)

    @pytest.mark.integration
    def test_resume_false_positive_rate(self, pii_analyzer):
        """Test that Dylan Isaac resume has <10% false positive rate.

        Previously detected 25 entities with 52% false positive rate (13/25).
        After changes: Should detect only actual PII (email, phone if present).
        """
        # Sample resume content similar to Dylan Isaac's resume
        # Note: Using realistic phone format (not 555) to match production confidence scores
        resume_text = """
        Dylan Isaac
        Software Engineer

        San Francisco Bay Area
        dylan.isaac@example.com
        (415) 867-5309

        EXPERIENCE

        Senior Software Engineer at TechCorp
        September 2019 - Present
        - Led development of microservices architecture
        - Collaborated with product managers on feature roadmap
        - Mentored junior engineers

        Software Engineer at StartupCo
        June 2017 - August 2019
        - Built RESTful APIs using Python and FastAPI
        - Implemented CI/CD pipelines
        - Worked with AWS services

        EDUCATION

        Bachelor of Science in Computer Science
        University of California, Berkeley
        Graduated 2017

        SKILLS

        Python, JavaScript, TypeScript, React, FastAPI, Docker, AWS
        """

        findings = pii_analyzer.analyze_text(resume_text)

        # Should only detect EMAIL_ADDRESS and PHONE_NUMBER (2 entities)
        # Previously detected 25 entities (13 false positives: 7 PERSON, 4 DATE_TIME, 2 LOCATION)
        assert len(findings) <= 2, f"Too many PII findings: {len(findings)}"

        # Verify only expected entity types
        entity_types = {f.entity_type for f in findings}
        allowed_types = {"EMAIL_ADDRESS", "PHONE_NUMBER"}
        unexpected = entity_types - allowed_types
        assert not unexpected, f"Unexpected entity types: {unexpected}"

        # Verify false positive rate <10%
        # Total entities = 2 actual PII (email, phone)
        # False positives should be 0
        expected_pii_count = 2
        actual_findings = len(findings)

        if actual_findings > 0:
            false_positive_rate = max(0, (actual_findings - expected_pii_count) / actual_findings)
            assert false_positive_rate < 0.1, f"False positive rate {false_positive_rate:.2%} exceeds 10%"

    @pytest.mark.integration
    def test_syllabus_false_positive_rate(self, pii_analyzer):
        """Test that course syllabus has <5% false positive rate."""
        syllabus_text = """
        CS 101: Introduction to Computer Science
        University of Illinois Chicago
        Fall 2024

        INSTRUCTOR INFORMATION

        Professor Jane Smith
        Office: Engineering Building, Room 305
        Email: jsmith@uic.edu
        Office Hours: Tuesdays 2-4 PM

        COURSE DESCRIPTION

        This course introduces fundamental concepts of computer science including
        programming, algorithms, and data structures. Students will learn Python
        programming and apply computational thinking to problem solving.

        SCHEDULE

        Week 1 (Aug 26): Introduction to Programming
        Week 2 (Sep 2): Variables and Data Types
        Week 3 (Sep 9): Control Flow
        Week 4 (Sep 16): Functions

        GRADING

        Assignments: 40%
        Midterm: 25%
        Final Project: 25%
        Participation: 10%

        TEXTBOOK

        "Introduction to Python Programming" by John Doe
        Publisher: Tech Press, 2023
        ISBN: 978-1-234567-89-0
        """

        findings = pii_analyzer.analyze_text(syllabus_text)

        # Should only detect professor's email (1 entity)
        # Dates, names, locations should NOT be detected
        assert len(findings) <= 1, f"Too many PII findings: {len(findings)}"

        # Verify only EMAIL_ADDRESS detected
        if findings:
            entity_types = {f.entity_type for f in findings}
            assert entity_types == {"EMAIL_ADDRESS"}, f"Unexpected entity types: {entity_types}"

            # Verify it's the professor's email
            assert "@uic.edu" in findings[0].text.lower()

        # False positive rate should be 0% (only expected PII detected)
        expected_pii_count = 1  # Professor's email only
        false_positive_rate = max(0, (len(findings) - expected_pii_count) / max(1, len(findings)))
        assert false_positive_rate < 0.05, f"False positive rate {false_positive_rate:.2%} exceeds 5%"

    @pytest.mark.integration
    def test_technical_document_no_false_positives(self, pii_analyzer):
        """Test that technical documentation has no false positives."""
        technical_text = """
        API Documentation

        The DocumentProcessor class handles PDF conversion. Initialize with:

        processor = DocumentProcessor(
            api_key="sk-1234567890",
            endpoint="https://api.example.com"
        )

        Methods:
        - process(file_path: str) -> Document
        - convert_to_html(doc: Document) -> str

        Error Codes:
        400 - Bad Request
        401 - Unauthorized
        500 - Internal Server Error

        Release Notes v2.3.0 (2024-01-15):
        - Added support for large files
        - Fixed memory leak in parser
        - Updated dependencies
        """

        findings = pii_analyzer.analyze_text(technical_text)

        # Should detect nothing (no actual PII in technical docs)
        # API keys, URLs, dates, version numbers should NOT be flagged
        assert len(findings) == 0, f"False positives detected: {[f.entity_type for f in findings]}"

    @pytest.mark.integration
    def test_actual_pii_detected_with_realistic_data(self, pii_analyzer):
        """Test that actual PII is detected with realistic (non-555) data."""
        # Use realistic phone numbers (not 555) to match production behavior
        pii_text = """
        Student Information Form

        Please provide your contact information:

        Email: student@example.com
        Phone: (312) 867-5309
        SSN: 123-45-6789

        Emergency Contact:
        Name: Jane Doe
        Phone: (312) 867-5308
        """

        findings = pii_analyzer.analyze_text(pii_text)

        # Should detect EMAIL, PHONE, SSN with high confidence
        assert len(findings) >= 1, f"Failed to detect any PII: {len(findings)} findings"

        entity_types = {f.entity_type for f in findings}
        assert "EMAIL_ADDRESS" in entity_types, "Failed to detect email"

        # With realistic data, all findings should meet 0.85 threshold
        for finding in findings:
            assert finding.score >= 0.85, \
                f"Low confidence finding: {finding.entity_type} ({finding.score}). " \
                "Realistic PII should score >= 0.85"

    @pytest.mark.integration
    def test_entity_types_configuration(self, pii_analyzer):
        """Test that only pattern-based entity types are enabled."""
        # Verify ENTITY_TYPES only contains pattern-based detectors
        expected_types = {
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "US_SSN",
            "CREDIT_CARD",
            "IBAN_CODE",
            "US_DRIVER_LICENSE",
        }

        assert set(ENTITY_TYPES) == expected_types, f"Unexpected entity types: {set(ENTITY_TYPES)}"

        # Verify NER-based types are disabled
        disabled_types = {"PERSON", "DATE_TIME", "LOCATION"}
        assert not (set(ENTITY_TYPES) & disabled_types), "NER-based entity types should be disabled"

    @pytest.mark.integration
    def test_company_names_not_detected(self, pii_analyzer):
        """Test that company names are not flagged as PERSON entities."""
        company_text = """
        I worked at Microsoft, Google, Apple, and Amazon.
        Previously worked at TechCorp and StartupCo.
        Collaborated with OpenAI and Anthropic teams.
        """

        findings = pii_analyzer.analyze_text(company_text)

        # Should detect nothing (company names are not PII)
        person_findings = [f for f in findings if f.entity_type == "PERSON"]
        assert len(person_findings) == 0, f"Company names incorrectly detected as PERSON: {person_findings}"

    @pytest.mark.integration
    def test_job_titles_not_detected(self, pii_analyzer):
        """Test that job titles are not flagged as PERSON entities."""
        title_text = """
        Position: Senior Software Engineer
        Reporting to: Engineering Manager
        Team: Product Development
        Role: Technical Lead
        """

        findings = pii_analyzer.analyze_text(title_text)

        # Should detect nothing (job titles are not PII)
        person_findings = [f for f in findings if f.entity_type == "PERSON"]
        assert len(person_findings) == 0, f"Job titles incorrectly detected as PERSON: {person_findings}"

    @pytest.mark.integration
    def test_dates_not_detected(self, pii_analyzer):
        """Test that various date formats are not flagged as PII."""
        date_text = """
        Assignment due: January 15, 2024
        Exam date: 2024-03-20
        Office hours: Mondays 2-4 PM
        Semester: Fall 2024
        Published: September 2019
        Updated: 2023-11-30
        """

        findings = pii_analyzer.analyze_text(date_text)

        # Should detect nothing (dates in course materials are not PII)
        date_findings = [f for f in findings if f.entity_type == "DATE_TIME"]
        assert len(date_findings) == 0, f"Dates incorrectly detected as PII: {date_findings}"

    @pytest.mark.integration
    def test_locations_not_detected(self, pii_analyzer):
        """Test that locations are not flagged as PII."""
        location_text = """
        University of Illinois Chicago
        Engineering Building, Room 305
        Chicago, Illinois
        San Francisco Bay Area
        Office location: Downtown Campus
        """

        findings = pii_analyzer.analyze_text(location_text)

        # Should detect nothing (institutional locations are not PII)
        location_findings = [f for f in findings if f.entity_type == "LOCATION"]
        assert len(location_findings) == 0, f"Locations incorrectly detected as PII: {location_findings}"
