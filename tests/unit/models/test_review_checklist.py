"""Tests for ReviewChecklist, ReviewItem, and ReviewOption models."""

import pytest
from datetime import datetime, UTC

from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.review_checklist import (
    ReviewChecklist,
    ReviewItem,
    ReviewOption,
)


class TestReviewOption:
    """Tests for the ReviewOption model."""

    def test_create_replace_option(self):
        """Test creating a replace option."""
        option = ReviewOption(
            label="Yes, replace with 'Enzo'",
            action="replace",
            replacement_text="Enzo",
            is_recommended=True,
        )

        assert option.action == "replace"
        assert option.replacement_text == "Enzo"
        assert option.is_recommended is True
        assert option.id  # Auto-generated

    def test_create_keep_option(self):
        """Test creating a keep option."""
        option = ReviewOption(
            label="No, keep original",
            action="keep",
        )

        assert option.action == "keep"
        assert option.replacement_text is None
        assert option.is_recommended is False


class TestReviewItem:
    """Tests for the ReviewItem model."""

    def test_create_review_item(self):
        """Test creating a review item."""
        options = [
            ReviewOption(label="Option A", action="replace", replacement_text="A"),
            ReviewOption(label="Option B", action="keep"),
        ]

        item = ReviewItem(
            observation_id="obs-123",
            agent="typography",
            question="Is this correct?",
            options=options,
            search_text="test",
            context="surrounding text",
            page_num=3,
            agent_recommendation="Use option A",
            agent_confidence=0.85,
        )

        assert item.observation_id == "obs-123"
        assert item.agent == "typography"
        assert len(item.options) == 2
        assert item.selected_option_id is None
        assert item.reviewed_at is None

    def test_submit_review_with_option(self):
        """Test submitting a review with selected option."""
        options = [
            ReviewOption(id="opt-1", label="Option A", action="replace", replacement_text="A"),
            ReviewOption(id="opt-2", label="Option B", action="keep"),
        ]

        item = ReviewItem(
            observation_id="obs-123",
            agent="typography",
            question="Is this correct?",
            options=options,
            search_text="test",
            context="context",
            page_num=1,
            agent_recommendation="Use A",
            agent_confidence=0.85,
        )

        item.submit_review(
            option_id="opt-1",
            custom_input=None,
            reviewed_by="user@example.com",
        )

        assert item.selected_option_id == "opt-1"
        assert item.custom_input is None
        assert item.reviewed_by == "user@example.com"
        assert item.reviewed_at is not None

    def test_submit_review_with_custom_input(self):
        """Test submitting a review with custom input."""
        options = [
            ReviewOption(label="Option A", action="replace", replacement_text="A"),
            ReviewOption(label="Option B", action="keep"),
        ]

        item = ReviewItem(
            observation_id="obs-123",
            agent="typography",
            question="Is this correct?",
            options=options,
            search_text="test",
            context="context",
            page_num=1,
            agent_recommendation="Use A",
            agent_confidence=0.85,
        )

        item.submit_review(
            option_id=None,
            custom_input="My custom answer",
            reviewed_by="user@example.com",
        )

        assert item.selected_option_id is None
        assert item.custom_input == "My custom answer"
        assert item.reviewed_by == "user@example.com"

    def test_submit_review_requires_option_or_input(self):
        """Test that review requires either option or custom input."""
        options = [
            ReviewOption(label="Option A", action="replace", replacement_text="A"),
            ReviewOption(label="Option B", action="keep"),
        ]

        item = ReviewItem(
            observation_id="obs-123",
            agent="typography",
            question="Is this correct?",
            options=options,
            search_text="test",
            context="context",
            page_num=1,
            agent_recommendation="Use A",
            agent_confidence=0.85,
        )

        with pytest.raises(ValueError, match="Must provide either"):
            item.submit_review(
                option_id=None,
                custom_input=None,
                reviewed_by="user@example.com",
            )

    def test_options_count_validation(self):
        """Test that options must have 2-4 items."""
        # Too few options
        with pytest.raises(ValueError):
            ReviewItem(
                observation_id="obs-123",
                agent="typography",
                question="Is this correct?",
                options=[ReviewOption(label="Only one", action="keep")],
                search_text="test",
                context="context",
                page_num=1,
                agent_recommendation="Use A",
                agent_confidence=0.85,
            )


class TestReviewChecklist:
    """Tests for the ReviewChecklist model."""

    def _create_observation(
        self,
        obs_id: str,
        category: str = "ocr",
        status: str = "open",
    ) -> Observation:
        """Helper to create an observation."""
        obs = Observation(
            id=obs_id,
            job_id="job-123",
            agent="typography",
            visual_description="Visual",
            markup_description="Markup",
            location=ObservationLocation(
                location_type="region",
                value="test region",
                page_num=1,
            ),
            category=category,
            status=status,
        )
        return obs

    def _create_review_item(
        self,
        obs_id: str,
        agent: str = "typography",
        page_num: int = 1,
    ) -> ReviewItem:
        """Helper to create a review item."""
        return ReviewItem(
            observation_id=obs_id,
            agent=agent,
            question="Is this correct?",
            options=[
                ReviewOption(label="Yes", action="replace", replacement_text="fixed"),
                ReviewOption(label="No", action="keep"),
            ],
            search_text="test",
            context="context",
            page_num=page_num,
            agent_recommendation="Use yes",
            agent_confidence=0.85,
        )

    def test_from_items_and_observations(self):
        """Test creating checklist from items and observations."""
        obs1 = self._create_observation("obs-1", category="ocr")
        obs2 = self._create_observation("obs-2", category="alt_text")

        item1 = self._create_review_item("obs-1", agent="typography", page_num=1)
        item2 = self._create_review_item("obs-2", agent="figures", page_num=3)

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs1, obs2],
        )

        assert checklist.total_items == 2
        assert "ocr" in checklist.by_category
        assert "alt_text" in checklist.by_category
        assert "typography" in checklist.by_agent
        assert "figures" in checklist.by_agent
        assert 1 in checklist.by_page
        assert 3 in checklist.by_page

    def test_excludes_closed_observations(self):
        """Test that closed observations are excluded from checklist."""
        obs_open = self._create_observation("obs-1", status="open")
        obs_closed = self._create_observation("obs-2", status="closed")
        obs_closed.resolution = "fixed"

        item1 = self._create_review_item("obs-1")
        item2 = self._create_review_item("obs-2")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs_open, obs_closed],
        )

        # Only open observation should be included
        assert checklist.total_items == 1
        assert len(checklist.items) == 1
        assert checklist.items[0].observation_id == "obs-1"

    def test_summary_generation(self):
        """Test that summary is generated correctly."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")

        item1 = self._create_review_item("obs-1", agent="typography")
        item2 = self._create_review_item("obs-2", agent="figures")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs1, obs2],
        )

        assert "2 items need review" in checklist.summary
        assert "typography" in checklist.summary or "figures" in checklist.summary

    def test_critical_items_count(self):
        """Test counting critical items (confidence < 0.7)."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")

        item1 = self._create_review_item("obs-1")
        item1.agent_confidence = 0.5  # Critical

        item2 = self._create_review_item("obs-2")
        item2.agent_confidence = 0.9  # Not critical

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs1, obs2],
        )

        assert checklist.critical_items == 1

    def test_empty_checklist(self):
        """Test creating empty checklist."""
        checklist = ReviewChecklist.from_items_and_observations(
            items=[],
            observations=[],
        )

        assert checklist.total_items == 0
        assert checklist.summary == "No items need review"
        assert checklist.items == []

    def test_all_observations_closed(self):
        """Test that when all observations are closed, checklist is empty."""
        obs1 = self._create_observation("obs-1", status="closed")
        obs2 = self._create_observation("obs-2", status="closed")
        obs1.resolution = "fixed"
        obs2.resolution = "fixed"

        item1 = self._create_review_item("obs-1")
        item2 = self._create_review_item("obs-2")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs1, obs2],
        )

        assert checklist.total_items == 0
        assert checklist.summary == "No items need review"
        assert len(checklist.items) == 0
        assert len(checklist.by_category) == 0
        assert len(checklist.by_agent) == 0
        assert len(checklist.by_page) == 0

    def test_missing_observation_id(self):
        """Test that items with missing observation_id get 'unknown' category."""
        obs1 = self._create_observation("obs-1", category="ocr")

        item1 = self._create_review_item("obs-1", agent="typography")
        item2 = self._create_review_item("obs-999", agent="figures")  # Missing obs

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs1],
        )

        # Both items included
        assert checklist.total_items == 2

        # Item with missing obs gets "unknown" category
        assert "unknown" in checklist.by_category
        assert len(checklist.by_category["unknown"]) == 1
        assert checklist.by_category["unknown"][0].observation_id == "obs-999"

        # Item with valid obs gets correct category
        assert "ocr" in checklist.by_category
        assert len(checklist.by_category["ocr"]) == 1

    def test_items_without_observation_id(self):
        """Test handling of items where observation lookup fails."""
        # Create item that references non-existent observation
        item = self._create_review_item("non-existent-obs")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item],
            observations=[],  # No observations
        )

        # Item is included but categorized as "unknown"
        assert checklist.total_items == 1
        assert "unknown" in checklist.by_category
        assert len(checklist.by_category["unknown"]) == 1

    def test_category_derivation_from_observations(self):
        """Test that categories are correctly derived from observations."""
        obs1 = self._create_observation("obs-1", category="alt_text")
        obs2 = self._create_observation("obs-2", category="table_format")
        obs3 = self._create_observation("obs-3", category="ocr")

        item1 = self._create_review_item("obs-1", agent="figures")
        item2 = self._create_review_item("obs-2", agent="tables")
        item3 = self._create_review_item("obs-3", agent="typography")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        # Verify categories derived from observations
        assert "alt_text" in checklist.by_category
        assert "table_format" in checklist.by_category
        assert "ocr" in checklist.by_category

        # Verify items are in correct category groups
        assert len(checklist.by_category["alt_text"]) == 1
        assert len(checklist.by_category["table_format"]) == 1
        assert len(checklist.by_category["ocr"]) == 1

    def test_grouping_by_category(self):
        """Test items are correctly grouped by category."""
        obs1 = self._create_observation("obs-1", category="ocr")
        obs2 = self._create_observation("obs-2", category="ocr")
        obs3 = self._create_observation("obs-3", category="alt_text")

        item1 = self._create_review_item("obs-1")
        item2 = self._create_review_item("obs-2")
        item3 = self._create_review_item("obs-3")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        # Two items in "ocr" category, one in "alt_text"
        assert len(checklist.by_category["ocr"]) == 2
        assert len(checklist.by_category["alt_text"]) == 1

    def test_grouping_by_agent(self):
        """Test items are correctly grouped by agent."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")
        obs3 = self._create_observation("obs-3")

        item1 = self._create_review_item("obs-1", agent="typography")
        item2 = self._create_review_item("obs-2", agent="typography")
        item3 = self._create_review_item("obs-3", agent="figures")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        # Two items from "typography", one from "figures"
        assert len(checklist.by_agent["typography"]) == 2
        assert len(checklist.by_agent["figures"]) == 1

    def test_grouping_by_page(self):
        """Test items are correctly grouped by page number."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")
        obs3 = self._create_observation("obs-3")

        item1 = self._create_review_item("obs-1", page_num=1)
        item2 = self._create_review_item("obs-2", page_num=1)
        item3 = self._create_review_item("obs-3", page_num=5)

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        # Two items on page 1, one on page 5
        assert len(checklist.by_page[1]) == 2
        assert len(checklist.by_page[5]) == 1

    def test_summary_with_multiple_agents(self):
        """Test summary generation with multiple agents."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")
        obs3 = self._create_observation("obs-3")

        item1 = self._create_review_item("obs-1", agent="typography")
        item2 = self._create_review_item("obs-2", agent="figures")
        item3 = self._create_review_item("obs-3", agent="tables")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        # Summary should show counts by agent
        assert "3 items need review" in checklist.summary
        assert "1 typography" in checklist.summary
        assert "1 figures" in checklist.summary
        assert "1 tables" in checklist.summary

    def test_summary_with_same_agent(self):
        """Test summary generation when all items from same agent."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")

        item1 = self._create_review_item("obs-1", agent="typography")
        item2 = self._create_review_item("obs-2", agent="typography")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs1, obs2],
        )

        assert "2 items need review" in checklist.summary
        assert "2 typography" in checklist.summary

    def test_mixed_open_and_closed_observations(self):
        """Test that only items with open observations are included."""
        obs_open1 = self._create_observation("obs-1", category="ocr", status="open")
        obs_closed = self._create_observation("obs-2", category="alt_text", status="closed")
        obs_open2 = self._create_observation("obs-3", category="table_format", status="open")

        obs_closed.resolution = "fixed"

        item1 = self._create_review_item("obs-1", agent="typography")
        item2 = self._create_review_item("obs-2", agent="figures")
        item3 = self._create_review_item("obs-3", agent="tables")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs_open1, obs_closed, obs_open2],
        )

        # Only 2 items included (closed observation excluded)
        assert checklist.total_items == 2
        assert len(checklist.items) == 2

        # Verify correct items included
        obs_ids = [item.observation_id for item in checklist.items]
        assert "obs-1" in obs_ids
        assert "obs-3" in obs_ids
        assert "obs-2" not in obs_ids

        # Verify categories
        assert "ocr" in checklist.by_category
        assert "table_format" in checklist.by_category
        assert "alt_text" not in checklist.by_category

    def test_completed_items_count(self):
        """Test counting completed items (items that have been reviewed)."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")
        obs3 = self._create_observation("obs-3")

        item1 = self._create_review_item("obs-1")
        item2 = self._create_review_item("obs-2")
        item3 = self._create_review_item("obs-3")

        # Mark one item as reviewed
        item2.submit_review(
            option_id="opt-1",
            custom_input=None,
            reviewed_by="user@example.com",
        )

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        assert checklist.total_items == 3
        assert checklist.completed_items == 1

    def test_multiple_items_same_observation_same_category(self):
        """Test multiple items can link to same observation."""
        obs1 = self._create_observation("obs-1", category="ocr")

        item1 = self._create_review_item("obs-1", agent="typography", page_num=1)
        item2 = self._create_review_item("obs-1", agent="typography", page_num=2)

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[obs1],
        )

        # Both items included
        assert checklist.total_items == 2

        # Both in same category
        assert len(checklist.by_category["ocr"]) == 2

        # Both from same agent
        assert len(checklist.by_agent["typography"]) == 2

        # Different pages
        assert len(checklist.by_page[1]) == 1
        assert len(checklist.by_page[2]) == 1

    def test_empty_items_with_observations(self):
        """Test empty items list with non-empty observations."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[],
            observations=[obs1, obs2],
        )

        assert checklist.total_items == 0
        assert checklist.summary == "No items need review"

    def test_empty_observations_with_items(self):
        """Test empty observations list with non-empty items."""
        item1 = self._create_review_item("obs-1")
        item2 = self._create_review_item("obs-2")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2],
            observations=[],
        )

        # Items included but all get "unknown" category
        assert checklist.total_items == 2
        assert "unknown" in checklist.by_category
        assert len(checklist.by_category["unknown"]) == 2

    def test_stats_calculation(self):
        """Test that stats are calculated correctly."""
        obs1 = self._create_observation("obs-1")
        obs2 = self._create_observation("obs-2")
        obs3 = self._create_observation("obs-3")

        item1 = self._create_review_item("obs-1")
        item1.agent_confidence = 0.6  # Critical (< 0.7)

        item2 = self._create_review_item("obs-2")
        item2.agent_confidence = 0.5  # Critical

        item3 = self._create_review_item("obs-3")
        item3.agent_confidence = 0.9  # Not critical

        # Mark one as reviewed
        item1.submit_review(
            option_id="opt-1",
            custom_input=None,
            reviewed_by="user@example.com",
        )

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        assert checklist.total_items == 3
        assert checklist.critical_items == 2
        assert checklist.completed_items == 1

    def test_categories_in_summary_derived_correctly(self):
        """Test that summary reflects category groupings."""
        obs1 = self._create_observation("obs-1", category="ocr")
        obs2 = self._create_observation("obs-2", category="ocr")
        obs3 = self._create_observation("obs-3", category="alt_text")

        item1 = self._create_review_item("obs-1", agent="typography")
        item2 = self._create_review_item("obs-2", agent="typography")
        item3 = self._create_review_item("obs-3", agent="figures")

        checklist = ReviewChecklist.from_items_and_observations(
            items=[item1, item2, item3],
            observations=[obs1, obs2, obs3],
        )

        # Summary shows agent counts, not category counts
        # But we verify categories are grouped correctly
        assert len(checklist.by_category["ocr"]) == 2
        assert len(checklist.by_category["alt_text"]) == 1

        # Summary should reference agents
        assert "2 typography" in checklist.summary
        assert "1 figures" in checklist.summary
