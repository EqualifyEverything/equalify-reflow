"""Tests for AgentDependencies dataclass.

Tests the runtime dependencies used for dynamic instruction injection.
"""

import pytest
from src.agents.dependencies import AgentDependencies


@pytest.mark.unit
class TestAgentDependencies:
    """Test AgentDependencies dataclass."""

    def test_creation_with_job_id_only(self):
        """Test creation with only required job_id."""
        deps = AgentDependencies(job_id="test-123")

        assert deps.job_id == "test-123"
        assert deps.job_service is None
        assert deps.manifest is None
        assert deps.previous_failures == []
        assert deps.attempt_number == 1
        assert deps.document_type is None
        assert deps.custom_context == {}

    def test_creation_with_all_fields(self):
        """Test creation with all optional fields."""
        deps = AgentDependencies(
            job_id="test-456",
            document_type="syllabus",
            attempt_number=2,
            previous_failures=["Error on page 3"],
            custom_context={"key": "value"},
        )

        assert deps.job_id == "test-456"
        assert deps.document_type == "syllabus"
        assert deps.attempt_number == 2
        assert deps.previous_failures == ["Error on page 3"]
        assert deps.custom_context == {"key": "value"}

    def test_is_retry_first_attempt(self):
        """Test is_retry is False for first attempt."""
        deps = AgentDependencies(job_id="test-123")

        assert deps.is_retry is False
        assert deps.attempt_number == 1

    def test_is_retry_after_failure(self):
        """Test is_retry is True after adding failure."""
        deps = AgentDependencies(job_id="test-123")
        deps.add_failure("Table parsing failed")

        assert deps.is_retry is True
        assert deps.attempt_number == 2
        assert deps.previous_failures == ["Table parsing failed"]

    def test_add_multiple_failures(self):
        """Test adding multiple failures increments attempt number."""
        deps = AgentDependencies(job_id="test-123")

        deps.add_failure("Error 1")
        assert deps.attempt_number == 2

        deps.add_failure("Error 2")
        assert deps.attempt_number == 3

        deps.add_failure("Error 3")
        assert deps.attempt_number == 4
        assert deps.previous_failures == ["Error 1", "Error 2", "Error 3"]

    def test_clear_failures(self):
        """Test clearing failures resets state."""
        deps = AgentDependencies(job_id="test-123")
        deps.add_failure("Error 1")
        deps.add_failure("Error 2")

        deps.clear_failures()

        assert deps.previous_failures == []
        assert deps.attempt_number == 1
        assert deps.is_retry is False


@pytest.mark.unit
class TestCloneForPage:
    """Test clone_for_page method for page-specific context."""

    def test_clone_preserves_base_fields(self):
        """Test that clone preserves base deps fields."""
        deps = AgentDependencies(
            job_id="test-123",
            document_type="exam",
            attempt_number=2,
            previous_failures=["Error 1"],
        )

        page_deps = deps.clone_for_page(page_num=5)

        assert page_deps.job_id == "test-123"
        assert page_deps.document_type == "exam"
        assert page_deps.attempt_number == 2
        assert page_deps.previous_failures == ["Error 1"]

    def test_clone_adds_page_context(self):
        """Test that clone adds page-specific context."""
        deps = AgentDependencies(job_id="test-123")

        page_deps = deps.clone_for_page(
            page_num=3,
            image_count=2,
            layout_type="two_column",
        )

        assert page_deps.custom_context["current_page_num"] == 3
        assert page_deps.custom_context["current_page_features"] == {
            "image_count": 2,
            "layout_type": "two_column",
        }

    def test_clone_does_not_modify_original(self):
        """Test that clone creates independent copy."""
        deps = AgentDependencies(
            job_id="test-123",
            custom_context={"original": "value"},
        )

        page_deps = deps.clone_for_page(page_num=1, new_key="new_value")

        # Modify page_deps
        page_deps.previous_failures.append("New error")
        page_deps.custom_context["modified"] = True

        # Original should be unchanged
        assert deps.previous_failures == []
        assert "modified" not in deps.custom_context
        assert deps.custom_context == {"original": "value"}

    def test_clone_merges_custom_context(self):
        """Test that clone merges existing custom_context."""
        deps = AgentDependencies(
            job_id="test-123",
            custom_context={"global_key": "global_value"},
        )

        page_deps = deps.clone_for_page(page_num=2, page_key="page_value")

        # Should have both global and page context
        assert page_deps.custom_context["global_key"] == "global_value"
        assert page_deps.custom_context["current_page_num"] == 2
        assert page_deps.custom_context["current_page_features"]["page_key"] == "page_value"

    def test_clone_with_manifest(self):
        """Test clone preserves manifest reference."""
        # Create a minimal mock manifest
        from unittest.mock import MagicMock

        mock_manifest = MagicMock()
        mock_manifest.document_title = "Test Document"

        deps = AgentDependencies(
            job_id="test-123",
            manifest=mock_manifest,
        )

        page_deps = deps.clone_for_page(page_num=1)

        # Should reference same manifest
        assert page_deps.manifest is mock_manifest
        assert page_deps.manifest.document_title == "Test Document"


@pytest.mark.unit
class TestDepsIntegration:
    """Integration-style tests for common usage patterns."""

    def test_retry_workflow(self):
        """Test typical retry workflow."""
        deps = AgentDependencies(job_id="job-abc", document_type="syllabus")

        # First attempt
        assert not deps.is_retry
        assert deps.attempt_number == 1

        # Simulate failure
        deps.add_failure("Failed to parse table on page 3")

        # Retry with guidance
        assert deps.is_retry
        assert deps.attempt_number == 2
        assert "page 3" in deps.previous_failures[0]

        # Simulate another failure
        deps.add_failure("Table structure ambiguous")

        # Second retry
        assert deps.attempt_number == 3
        assert len(deps.previous_failures) == 2

    def test_page_iteration_workflow(self):
        """Test typical page iteration workflow."""
        deps = AgentDependencies(job_id="job-xyz")

        page_results = []
        for page_num in [1, 2, 3]:
            page_deps = deps.clone_for_page(
                page_num,
                image_count=page_num,  # Different count per page
            )
            page_results.append(page_deps.custom_context)

        # Each page should have unique context
        assert page_results[0]["current_page_num"] == 1
        assert page_results[1]["current_page_num"] == 2
        assert page_results[2]["current_page_num"] == 3

        # Original deps unchanged
        assert deps.custom_context == {}
