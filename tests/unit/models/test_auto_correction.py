"""Tests for AutoCorrection model."""

from datetime import datetime

import pytest
from src.shared.models.auto_correction import AutoCorrection


class TestAutoCorrection:
    """Tests for the AutoCorrection model."""

    def test_create_auto_correction(self):
        """Test creating a basic auto correction."""
        correction = AutoCorrection(
            observation_id="obs-123",
            search="## Introduction",
            replace="## 1. Introduction",
            justification="Adding section number",
            confidence=0.98,
            agent="structure",
        )

        assert correction.observation_id == "obs-123"
        assert correction.search == "## Introduction"
        assert correction.replace == "## 1. Introduction"
        assert correction.confidence == 0.98
        assert correction.agent == "structure"
        assert correction.applied is False
        assert correction.applied_at is None
        assert correction.id  # Auto-generated

    def test_mark_applied(self):
        """Test marking a correction as applied."""
        correction = AutoCorrection(
            observation_id="obs-123",
            search="old",
            replace="new",
            justification="test",
            confidence=0.95,
            agent="structure",
        )

        assert correction.applied is False
        correction.mark_applied()
        assert correction.applied is True
        assert correction.applied_at is not None
        assert isinstance(correction.applied_at, datetime)

    def test_mark_applied_twice_raises(self):
        """Test that marking applied twice raises error."""
        correction = AutoCorrection(
            observation_id="obs-123",
            search="old",
            replace="new",
            justification="test",
            confidence=0.95,
            agent="structure",
        )

        correction.mark_applied()
        with pytest.raises(ValueError, match="already applied"):
            correction.mark_applied()

    def test_empty_replace_allowed(self):
        """Test that empty replace string is allowed (for deletions)."""
        correction = AutoCorrection(
            observation_id="obs-123",
            search="delete me",
            replace="",
            justification="Removing unnecessary text",
            confidence=0.99,
            agent="typography",
        )

        assert correction.replace == ""

    def test_search_must_not_be_empty(self):
        """Test that search string cannot be empty."""
        with pytest.raises(ValueError):
            AutoCorrection(
                observation_id="obs-123",
                search="",
                replace="new",
                justification="test",
                confidence=0.95,
                agent="structure",
            )

    def test_justification_must_not_be_empty(self):
        """Test that justification cannot be empty."""
        with pytest.raises(ValueError):
            AutoCorrection(
                observation_id="obs-123",
                search="old",
                replace="new",
                justification="",
                confidence=0.95,
                agent="structure",
            )

    def test_confidence_bounds(self):
        """Test that confidence must be between 0 and 1."""
        with pytest.raises(ValueError):
            AutoCorrection(
                observation_id="obs-123",
                search="old",
                replace="new",
                justification="test",
                confidence=1.5,
                agent="structure",
            )

        with pytest.raises(ValueError):
            AutoCorrection(
                observation_id="obs-123",
                search="old",
                replace="new",
                justification="test",
                confidence=-0.1,
                agent="structure",
            )

    def test_json_serialization(self):
        """Test that AutoCorrection serializes to JSON correctly."""
        correction = AutoCorrection(
            observation_id="obs-123",
            search="old",
            replace="new",
            justification="test",
            confidence=0.95,
            agent="structure",
            page_num=3,
        )

        json_str = correction.model_dump_json()
        assert "obs-123" in json_str
        assert "structure" in json_str

        # Deserialize and verify
        restored = AutoCorrection.model_validate_json(json_str)
        assert restored.observation_id == correction.observation_id
        assert restored.confidence == correction.confidence
