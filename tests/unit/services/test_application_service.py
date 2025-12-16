"""Unit tests for ApplicationService.

Tests for applying approved proposals to markdown using search-replace operations.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.services.application_service import ApplicationService
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.proposal import Proposal, SearchReplaceDiff


@pytest.fixture
def mock_remediation_storage():
    """Create mock RemediationStorageService."""
    storage = MagicMock()
    storage.load_current_markdown = AsyncMock()
    storage.load_proposals = AsyncMock()
    storage.load_observations = AsyncMock()
    storage.save_proposals = AsyncMock()
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
def sample_proposal():
    """Create a sample approved proposal."""
    return Proposal(
        id="prop-1",
        job_id="job-123",
        resolves=["obs-1"],
        diff=SearchReplaceDiff(
            search="![](images/figure-1.png)",
            replace="![Flowchart showing registration process](images/figure-1.png)",
        ),
        justification="Adding alt text to image",
        page_nums=[1],
        status="approved",
        route="auto",
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


class TestApplyApprovedProposals:
    """Tests for apply_approved_proposals method."""

    async def test_no_markdown_raises_error(
        self, application_service, mock_remediation_storage
    ):
        """Should raise ValueError if no markdown found."""
        mock_remediation_storage.load_current_markdown.return_value = None
        mock_remediation_storage.load_proposals.return_value = []
        mock_remediation_storage.load_observations.return_value = []

        with pytest.raises(ValueError, match="No markdown found"):
            await application_service.apply_approved_proposals("job-123")

    async def test_no_approved_proposals_returns_zero_counts(
        self,
        application_service,
        mock_remediation_storage,
        sample_markdown,
    ):
        """Should return zero counts when no approved proposals."""
        mock_remediation_storage.load_current_markdown.return_value = sample_markdown
        mock_remediation_storage.load_proposals.return_value = [
            Proposal(
                id="prop-1",
                job_id="job-123",
                resolves=[],
                diff=SearchReplaceDiff(search="foo", replace="bar"),
                justification="test",
                status="pending",  # Not approved
            )
        ]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_approved_proposals("job-123")

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
        sample_proposal,
        sample_observation,
    ):
        """Should apply proposal when exact match found."""
        mock_remediation_storage.load_current_markdown.return_value = sample_markdown
        mock_remediation_storage.load_proposals.return_value = [sample_proposal]
        mock_remediation_storage.load_observations.return_value = [sample_observation]

        result = await application_service.apply_approved_proposals("job-123")

        assert result.applied_count == 1
        assert result.failed_count == 0
        assert result.final_markdown_url == "job-123.md"

        # Verify markdown was saved
        mock_storage.upload_result.assert_called()

        # Verify proposals were saved with updated status
        mock_remediation_storage.save_proposals.assert_called_once()
        saved_proposals = mock_remediation_storage.save_proposals.call_args[0][1]
        assert saved_proposals[0].status == "applied"

        # Verify observations were saved with resolved status
        mock_remediation_storage.save_observations.assert_called_once()
        saved_observations = mock_remediation_storage.save_observations.call_args[0][1]
        assert saved_observations[0].status == "resolved"
        assert saved_observations[0].resolved_by == "prop-1"

    async def test_multiple_matches_fails(
        self,
        application_service,
        mock_remediation_storage,
    ):
        """Should fail when search text appears multiple times."""
        markdown = "foo bar foo baz foo"
        proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=[],
            diff=SearchReplaceDiff(search="foo", replace="qux"),
            justification="test",
            status="approved",
        )

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_proposals.return_value = [proposal]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_approved_proposals("job-123")

        assert result.applied_count == 0
        assert result.failed_count == 1
        assert "matches 3 locations" in result.failed_proposals[0]["error"]

    async def test_not_found_fails(
        self,
        application_service,
        mock_remediation_storage,
    ):
        """Should fail when search text not found."""
        markdown = "hello world"
        proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=[],
            diff=SearchReplaceDiff(search="nonexistent", replace="replacement"),
            justification="test",
            status="approved",
        )

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_proposals.return_value = [proposal]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_approved_proposals("job-123")

        assert result.applied_count == 0
        assert result.failed_count == 1
        assert "not found" in result.failed_proposals[0]["error"]

    async def test_whitespace_normalized_match(
        self,
        application_service,
        mock_remediation_storage,
        mock_storage,
    ):
        """Should apply proposal using whitespace-normalized matching."""
        # Markdown has different whitespace than search
        markdown = "hello   world\nwith  spaces"
        proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=[],
            diff=SearchReplaceDiff(
                search="hello world with spaces",  # Normalized
                replace="replaced text",
            ),
            justification="test",
            status="approved",
        )

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_proposals.return_value = [proposal]
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_approved_proposals("job-123")

        assert result.applied_count == 1
        assert result.failed_count == 0

    async def test_application_log_saved(
        self,
        application_service,
        mock_remediation_storage,
        sample_markdown,
        sample_proposal,
    ):
        """Should save application log with entries for each proposal."""
        mock_remediation_storage.load_current_markdown.return_value = sample_markdown
        mock_remediation_storage.load_proposals.return_value = [sample_proposal]
        mock_remediation_storage.load_observations.return_value = []

        await application_service.apply_approved_proposals("job-123")

        mock_remediation_storage.save_application_log.assert_called_once()
        log_entries = mock_remediation_storage.save_application_log.call_args[0][1]
        assert len(log_entries) == 1
        assert log_entries[0]["proposal_id"] == "prop-1"
        assert log_entries[0]["status"] == "applied"
        assert "method" in log_entries[0]

    async def test_multiple_proposals_applied_in_order(
        self,
        application_service,
        mock_remediation_storage,
        mock_storage,
    ):
        """Should apply multiple proposals in page order."""
        markdown = "text A\ntext B\ntext C"
        proposals = [
            Proposal(
                id="prop-3",
                job_id="job-123",
                resolves=[],
                diff=SearchReplaceDiff(search="text C", replace="TEXT C"),
                justification="test",
                page_nums=[3],
                status="approved",
            ),
            Proposal(
                id="prop-1",
                job_id="job-123",
                resolves=[],
                diff=SearchReplaceDiff(search="text A", replace="TEXT A"),
                justification="test",
                page_nums=[1],
                status="approved",
            ),
            Proposal(
                id="prop-2",
                job_id="job-123",
                resolves=[],
                diff=SearchReplaceDiff(search="text B", replace="TEXT B"),
                justification="test",
                page_nums=[2],
                status="approved",
            ),
        ]

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_proposals.return_value = proposals
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_approved_proposals("job-123")

        assert result.applied_count == 3
        assert result.failed_count == 0

    async def test_continues_after_failure(
        self,
        application_service,
        mock_remediation_storage,
        mock_storage,
    ):
        """Should continue applying proposals after one fails."""
        markdown = "text A\ntext B"
        proposals = [
            Proposal(
                id="prop-1",
                job_id="job-123",
                resolves=[],
                diff=SearchReplaceDiff(search="nonexistent", replace="fail"),
                justification="test",
                page_nums=[1],
                status="approved",
            ),
            Proposal(
                id="prop-2",
                job_id="job-123",
                resolves=[],
                diff=SearchReplaceDiff(search="text B", replace="TEXT B"),
                justification="test",
                page_nums=[2],
                status="approved",
            ),
        ]

        mock_remediation_storage.load_current_markdown.return_value = markdown
        mock_remediation_storage.load_proposals.return_value = proposals
        mock_remediation_storage.load_observations.return_value = []

        result = await application_service.apply_approved_proposals("job-123")

        assert result.applied_count == 1
        assert result.failed_count == 1


class TestApplySingleProposal:
    """Tests for _apply_single_proposal method."""

    def test_exact_match_success(self, application_service):
        """Should succeed with exact match."""
        markdown = "hello world"
        proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=[],
            diff=SearchReplaceDiff(search="hello", replace="goodbye"),
            justification="test",
            status="approved",
        )

        result = application_service._apply_single_proposal(markdown, proposal)

        assert result["success"] is True
        assert result["new_markdown"] == "goodbye world"
        assert result["method"] == "exact"

    def test_exact_match_multiple_occurrences(self, application_service):
        """Should fail when multiple occurrences found."""
        markdown = "hello hello hello"
        proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=[],
            diff=SearchReplaceDiff(search="hello", replace="goodbye"),
            justification="test",
            status="approved",
        )

        result = application_service._apply_single_proposal(markdown, proposal)

        assert result["success"] is False
        assert "3 locations" in result["error"]

    def test_whitespace_normalized_success(self, application_service):
        """Should succeed with whitespace-normalized match."""
        markdown = "hello   world\n  test"
        proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=[],
            diff=SearchReplaceDiff(
                search="hello world test",
                replace="replaced",
            ),
            justification="test",
            status="approved",
        )

        result = application_service._apply_single_proposal(markdown, proposal)

        assert result["success"] is True
        assert result["method"] == "whitespace_normalized"

    def test_not_found(self, application_service):
        """Should fail when text not found."""
        markdown = "hello world"
        proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=[],
            diff=SearchReplaceDiff(search="nonexistent", replace="replacement"),
            justification="test",
            status="approved",
        )

        result = application_service._apply_single_proposal(markdown, proposal)

        assert result["success"] is False
        assert "not found" in result["error"]


class TestValidateMarkdown:
    """Tests for _validate_markdown method."""

    def test_valid_markdown_no_warnings(self, application_service):
        """Should return no warnings for valid markdown."""
        markdown = """# Heading

Paragraph text.

```python
code block
```

## Another heading
"""
        warnings = application_service._validate_markdown(markdown)
        assert len(warnings) == 0

    def test_unbalanced_code_fences(self, application_service):
        """Should warn about unbalanced code fences."""
        markdown = """# Heading

```python
code block without closing fence
"""
        warnings = application_service._validate_markdown(markdown)
        assert any("code fences" in w.lower() for w in warnings)

    def test_todo_placeholders(self, application_service):
        """Should warn about TODO placeholders."""
        markdown = """# Heading

![TODO: describe this image](image.png)

More text.
"""
        warnings = application_service._validate_markdown(markdown)
        assert any("todo" in w.lower() for w in warnings)

    def test_heading_level_skip(self, application_service):
        """Should warn about heading level skips."""
        markdown = """# H1

### H3 (skipped H2)

Content.
"""
        warnings = application_service._validate_markdown(markdown)
        assert any("heading level skip" in w.lower() for w in warnings)


class TestCountManualObservations:
    """Tests for count_manual_observations method."""

    async def test_counts_manual_observations(
        self, application_service, mock_remediation_storage
    ):
        """Should count observations with status='manual'."""
        observations = [
            Observation(
                id="obs-1",
                job_id="job-123",
                agent="figures",
                visual_description="test",
                markup_description="test",
                location=ObservationLocation(
                    location_type="element",
                    value="test",
                    page_num=1,
                ),
                status="open",
            ),
            Observation(
                id="obs-2",
                job_id="job-123",
                agent="tables",
                visual_description="test",
                markup_description="test",
                location=ObservationLocation(
                    location_type="element",
                    value="test",
                    page_num=2,
                ),
                status="manual",
            ),
            Observation(
                id="obs-3",
                job_id="job-123",
                agent="structure",
                visual_description="test",
                markup_description="test",
                location=ObservationLocation(
                    location_type="element",
                    value="test",
                    page_num=3,
                ),
                status="manual",
            ),
            Observation(
                id="obs-4",
                job_id="job-123",
                agent="figures",
                visual_description="test",
                markup_description="test",
                location=ObservationLocation(
                    location_type="element",
                    value="test",
                    page_num=4,
                ),
                status="resolved",
            ),
        ]
        mock_remediation_storage.load_observations.return_value = observations

        count = await application_service.count_manual_observations("job-123")

        assert count == 2


class TestWhitespaceNormalizedMatching:
    """Tests for _try_whitespace_normalized method."""

    def test_newlines_normalized(self, application_service):
        """Should match across newlines."""
        markdown = "line one\nline two"
        result = application_service._try_whitespace_normalized(
            markdown,
            "line one line two",
            "replaced",
        )

        assert result["success"] is True
        assert result["new_markdown"] == "replaced"

    def test_tabs_normalized(self, application_service):
        """Should match with tabs."""
        markdown = "word\tword"
        result = application_service._try_whitespace_normalized(
            markdown,
            "word word",
            "replaced",
        )

        assert result["success"] is True

    def test_multiple_spaces_normalized(self, application_service):
        """Should match with multiple spaces."""
        markdown = "hello     world"
        result = application_service._try_whitespace_normalized(
            markdown,
            "hello world",
            "replaced",
        )

        assert result["success"] is True

    def test_not_found_returns_false(self, application_service):
        """Should return success=False when not found."""
        markdown = "hello world"
        result = application_service._try_whitespace_normalized(
            markdown,
            "nonexistent",
            "replaced",
        )

        assert result["success"] is False

    def test_multiple_normalized_matches_fails(self, application_service):
        """Should fail when normalized search matches multiple times."""
        markdown = "foo bar\nfoo  bar\nfoo   bar"
        result = application_service._try_whitespace_normalized(
            markdown,
            "foo bar",
            "replaced",
        )

        # The normalized version "foo bar" appears 3 times when normalized
        assert result["success"] is False
        assert "error" in result
