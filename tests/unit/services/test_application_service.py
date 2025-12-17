"""Unit tests for ApplicationService.

Tests for applying auto corrections to markdown using search-replace operations.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.services.application_service import ApplicationService
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation, ObservationLocation


@pytest.fixture
def mock_remediation_storage():
    """Create mock RemediationStorageService."""
    storage = MagicMock()
    storage.load_current_markdown = AsyncMock()
    storage.load_auto_corrections = AsyncMock()
    storage.load_observations = AsyncMock()
    storage.save_auto_corrections = AsyncMock()
    storage.save_observations = AsyncMock()
    storage.save_application_log = AsyncMock()
    return storage


@pytest.fixture
def mock_storage():
    """Create mock StorageService."""
    storage = MagicMock()
    storage.upload_result = AsyncMock(return_value="job-123.md")
    return storage


@pytest.fixture
def mock_job_service():
    """Create mock JobService."""
    service = MagicMock()
    service.update_job_status = AsyncMock()
    return service


@pytest.fixture
def application_service(mock_remediation_storage, mock_storage, mock_job_service):
    """Create ApplicationService with mocked dependencies."""
    return ApplicationService(
        remediation_storage=mock_remediation_storage,
        storage=mock_storage,
        job_service=mock_job_service,
    )


@pytest.fixture
def sample_markdown():
    """Sample markdown document for testing."""
    return """# Document Title

This is a paragraph with some text.

![](images/figure-1.png)

Another paragraph here.

| Column A | Column B |
|----------|----------|
| Value 1  | Value 2  |

## Section Two

More content follows.
"""


@pytest.fixture
def sample_correction():
    """Create a sample auto correction."""
    return AutoCorrection(
        id="corr-1",
        observation_id="obs-1",
        search="![](images/figure-1.png)",
        replace="![Flowchart showing registration process](images/figure-1.png)",
        justification="Adding alt text to image",
        confidence=0.98,
        agent="figures",
        page_num=1,
        applied=False,
    )


@pytest.fixture
def sample_observation():
    """Create a sample observation."""
    return Observation(
        id="obs-1",
        job_id="job-123",
        agent="figures",
        visual_description="Flowchart with 5 steps",
        markup_description="Image has empty alt text",
        location=ObservationLocation(
            location_type="element",
            value="img[src='figure-1.png']",
            page_num=1,
        ),
        status="open",
    )


class TestApplyAutoCorrections:
    """Tests for apply_auto_corrections method."""

    async def test_no_markdown_raises_error(
        self, application_service, mock_remediation_storage
    ):
        """Should raise ValueError if no markdown found."""
        mock_remediation_storage.load_current_markdown.return_value = None
        mock_remediation_storage.load_auto_corrections.return_value = []
        mock_remediation_storage.load_observations.return_value = []

        with pytest.raises(ValueError, match="No markdown found"):
            await application_service.apply_auto_corrections("job-123")

    async def test_no_unapplied_corrections_returns_zero_counts(
        self,
        application_service,
        mock_remediation_storage,
        sample_markdown,
    ):
        """Should return zero counts when no unapplied corrections."""
        correction = AutoCorrection(
            id="corr-1",
            observation_id="obs-1",
            search="foo",
            replace="bar",
            justification="test",
            confidence=0.98,
            agent="figures",
            applied=True,  # Already applied
        )

        mock_remediation_storage.load_current_markdown.return_value = sample_markdown
        mock_remediation_storage.load_auto_corrections.return_value = [correction]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_auto_corrections("job-123")

        assert result.applied_count == 0
        assert result.failed_count == 0
        assert result.skipped_count == 1
        assert result.final_markdown_url is None

    async def test_exact_match_applies_successfully(
        self,
        application_service,
        mock_remediation_storage,
        mock_storage,
        sample_markdown,
        sample_correction,
        sample_observation,
    ):
        """Should apply correction when exact match found."""
        mock_remediation_storage.load_current_markdown.return_value = sample_markdown
        mock_remediation_storage.load_auto_corrections.return_value = [sample_correction]
        mock_remediation_storage.load_observations.return_value = [sample_observation]

        result = await application_service.apply_auto_corrections("job-123")

        assert result.applied_count == 1
        assert result.failed_count == 0
        assert result.final_markdown_url == "job-123.md"

        # Verify markdown was saved
        mock_storage.upload_result.assert_called()

        # Verify corrections were saved with applied=True
        mock_remediation_storage.save_auto_corrections.assert_called_once()
        saved_corrections = mock_remediation_storage.save_auto_corrections.call_args[0][1]
        assert saved_corrections[0].applied is True

        # Verify observations were saved with closed status
        mock_remediation_storage.save_observations.assert_called_once()
        saved_observations = mock_remediation_storage.save_observations.call_args[0][1]
        assert saved_observations[0].status == "closed"
        assert saved_observations[0].resolution == "fixed"

    async def test_multiple_matches_fails(
        self,
        application_service,
        mock_remediation_storage,
    ):
        """Should fail when search text appears multiple times."""
        markdown = "foo bar foo baz foo"
        correction = AutoCorrection(
            id="corr-1",
            observation_id="obs-1",
            search="foo",
            replace="qux",
            justification="test",
            confidence=0.98,
            agent="figures",
        )

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_auto_corrections.return_value = [correction]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_auto_corrections("job-123")

        assert result.applied_count == 0
        assert result.failed_count == 1
        assert "matches 3 locations" in result.failed_corrections[0]["error"]

    async def test_not_found_fails(
        self,
        application_service,
        mock_remediation_storage,
    ):
        """Should fail when search text not found."""
        markdown = "hello world"
        correction = AutoCorrection(
            id="corr-1",
            observation_id="obs-1",
            search="nonexistent",
            replace="replacement",
            justification="test",
            confidence=0.98,
            agent="figures",
        )

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_auto_corrections.return_value = [correction]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_auto_corrections("job-123")

        assert result.applied_count == 0
        assert result.failed_count == 1
        assert "not found" in result.failed_corrections[0]["error"]

    async def test_whitespace_normalized_match(
        self,
        application_service,
        mock_remediation_storage,
        mock_storage,
    ):
        """Should apply correction using whitespace-normalized matching."""
        # Markdown has different whitespace than search
        markdown = "hello   world\nwith  spaces"
        correction = AutoCorrection(
            id="corr-1",
            observation_id="obs-1",
            search="hello world with spaces",  # Normalized
            replace="replaced text",
            justification="test",
            confidence=0.98,
            agent="structure",
        )

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_auto_corrections.return_value = [correction]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_auto_corrections("job-123")

        assert result.applied_count == 1
        assert result.failed_count == 0

    async def test_application_log_saved(
        self,
        application_service,
        mock_remediation_storage,
        sample_markdown,
        sample_correction,
        sample_observation,
    ):
        """Should save application log after applying."""
        mock_remediation_storage.load_current_markdown.return_value = sample_markdown
        mock_remediation_storage.load_auto_corrections.return_value = [sample_correction]
        mock_remediation_storage.load_observations.return_value = [sample_observation]

        await application_service.apply_auto_corrections("job-123")

        mock_remediation_storage.save_application_log.assert_called_once()
        log_entries = mock_remediation_storage.save_application_log.call_args[0][1]
        assert len(log_entries) == 1
        assert log_entries[0]["correction_id"] == "corr-1"
        assert log_entries[0]["status"] == "applied"


class TestCountOpenObservations:
    """Tests for count_open_observations method."""

    async def test_counts_open_observations(
        self,
        application_service,
        mock_remediation_storage,
    ):
        """Should count only open observations."""
        obs1 = Observation(
            id="obs-1",
            job_id="job-123",
            agent="figures",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(value="test", page_num=1),
            status="open",
        )
        obs2 = Observation(
            id="obs-2",
            job_id="job-123",
            agent="figures",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(value="test", page_num=2),
            status="open",
        )
        obs3 = Observation(
            id="obs-3",
            job_id="job-123",
            agent="figures",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(value="test", page_num=3),
            status="closed",
            resolution="fixed",
        )

        mock_remediation_storage.load_observations.return_value = [obs1, obs2, obs3]

        count = await application_service.count_open_observations("job-123")

        assert count == 2
