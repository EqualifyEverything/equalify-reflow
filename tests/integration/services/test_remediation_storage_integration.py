"""Integration tests for RemediationStorageService with real S3 (LocalStack).

These tests verify end-to-end data flows for the Phase 4 remediation pipeline:
- DocumentManifest save/load
- Observations save/load/append
- Proposals save/load/update
- Application log persistence
- Current markdown operations

Uses real LocalStack S3 (no mocking) to test actual S3 operations.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest
from src.services.remediation_storage_service import RemediationStorageService
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.proposal import Proposal, SearchReplaceDiff
from src.shared.models.remediation import DocumentManifest, PageFeatures


@pytest.mark.integration
class TestManifestOperations:
    """Tests for DocumentManifest save/load operations with real S3."""

    @pytest.mark.asyncio
    async def test_save_and_load_manifest(self, storage_service):
        """Test saving and loading a DocumentManifest to/from S3."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Create sample heading tree data
        heading_tree_data = {
            "document_title": "Test Document",
            "title_page": 1,
            "sections": [
                {
                    "level": 1,
                    "title": "Introduction",
                    "page_num": 1
                }
            ],
            "total_pages": 3,
            "layout_type": "single_column",
            "confidence": 0.95,
            "observations": ""
        }

        manifest = DocumentManifest(
            job_id=job_id,
            document_title="Integration Test Document",
            document_type="lecture_notes",
            total_pages=3,
            heading_tree_json=json.dumps(heading_tree_data),
            page_features=[
                PageFeatures(
                    page_num=1,
                    has_images=True,
                    image_count=2,
                    has_tables=False,
                    layout_type="single_column",
                    complexity_score=0.5
                ),
                PageFeatures(
                    page_num=2,
                    has_tables=True,
                    table_count=1,
                    layout_type="two_column",
                    complexity_score=0.7
                ),
                PageFeatures(
                    page_num=3,
                    has_math=True,
                    layout_type="single_column",
                    complexity_score=0.8
                )
            ],
            required_agents=["figures", "tables"],
            skip_agents=["typography"],
            analysis_confidence=0.92,
            analysis_notes="Document has complex figures and tables",
            analysis_model="claude-sonnet-4-5"
        )

        # Act - Save manifest
        key = await remediation_storage.save_manifest(job_id, manifest)

        # Assert - Save operation
        assert key == f"{job_id}/manifest.json"

        # Act - Load manifest
        loaded_manifest = await remediation_storage.load_manifest(job_id)

        # Assert - Loaded data matches original
        assert loaded_manifest is not None
        assert loaded_manifest.job_id == job_id
        assert loaded_manifest.document_title == "Integration Test Document"
        assert loaded_manifest.document_type == "lecture_notes"
        assert loaded_manifest.total_pages == 3
        assert len(loaded_manifest.page_features) == 3
        assert loaded_manifest.page_features[0].page_num == 1
        assert loaded_manifest.page_features[0].has_images is True
        assert loaded_manifest.page_features[0].image_count == 2
        assert loaded_manifest.page_features[1].has_tables is True
        assert loaded_manifest.page_features[2].has_math is True
        assert loaded_manifest.required_agents == ["figures", "tables"]
        assert loaded_manifest.skip_agents == ["typography"]
        assert loaded_manifest.analysis_confidence == 0.92
        assert loaded_manifest.analysis_model == "claude-sonnet-4-5"

        # Verify heading tree is preserved
        loaded_tree = json.loads(loaded_manifest.heading_tree_json)
        assert loaded_tree["document_title"] == "Test Document"
        assert loaded_tree["total_pages"] == 3

    @pytest.mark.asyncio
    async def test_load_nonexistent_manifest(self, storage_service):
        """Test loading a manifest that doesn't exist returns None."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Act
        loaded_manifest = await remediation_storage.load_manifest(job_id)

        # Assert
        assert loaded_manifest is None


@pytest.mark.integration
class TestObservationOperations:
    """Tests for Observation save/load/append operations with real S3."""

    @pytest.mark.asyncio
    async def test_save_and_load_observations(self, storage_service):
        """Test saving and loading observations to/from S3."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        observations = [
            Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="figures",
                source="agent",
                visual_description="Image shows flowchart with 5 steps",
                markup_description="Image has empty alt text",
                location=ObservationLocation(
                    location_type="element",
                    value="img[src='figure-1.png']",
                    page_num=2
                ),
                confidence=0.9,
                severity="major",
                route="auto",
                status="open"
            ),
            Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="tables",
                source="agent",
                visual_description="Table has 3 columns with student grades",
                markup_description="Table rendered as plain text without structure",
                location=ObservationLocation(
                    location_type="region",
                    value="lines 45-60",
                    page_num=3
                ),
                confidence=0.85,
                severity="critical",
                route="manual",
                manual_reason="Complex table structure needs review"
            )
        ]

        # Act - Save observations
        key = await remediation_storage.save_observations(job_id, observations)

        # Assert - Save operation
        assert key == f"{job_id}/observations.json"

        # Act - Load observations
        loaded_observations = await remediation_storage.load_observations(job_id)

        # Assert - Loaded data matches original
        assert len(loaded_observations) == 2

        # Check first observation
        obs1 = loaded_observations[0]
        assert obs1.job_id == job_id
        assert obs1.agent == "figures"
        assert obs1.source == "agent"
        assert "flowchart" in obs1.visual_description
        assert "empty alt text" in obs1.markup_description
        assert obs1.location.location_type == "element"
        assert obs1.location.page_num == 2
        assert obs1.confidence == 0.9
        assert obs1.severity == "major"
        assert obs1.route == "auto"
        assert obs1.status == "open"

        # Check second observation
        obs2 = loaded_observations[1]
        assert obs2.agent == "tables"
        assert obs2.severity == "critical"
        assert obs2.route == "manual"
        assert obs2.manual_reason == "Complex table structure needs review"

    @pytest.mark.asyncio
    async def test_append_observations(self, storage_service):
        """Test appending new observations to existing file."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Initial observations
        initial_observations = [
            Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="analysis",
                visual_description="Initial observation 1",
                markup_description="Initial markup 1",
                location=ObservationLocation(
                    location_type="region",
                    value="page 1",
                    page_num=1
                )
            )
        ]

        # Save initial observations
        await remediation_storage.save_observations(job_id, initial_observations)

        # New observations to append
        new_observations = [
            Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="figures",
                visual_description="New observation 1",
                markup_description="New markup 1",
                location=ObservationLocation(
                    location_type="element",
                    value="img.test",
                    page_num=2
                )
            ),
            Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="tables",
                visual_description="New observation 2",
                markup_description="New markup 2",
                location=ObservationLocation(
                    location_type="region",
                    value="page 3",
                    page_num=3
                )
            )
        ]

        # Act - Append new observations
        await remediation_storage.append_observations(job_id, new_observations)

        # Load and verify
        all_observations = await remediation_storage.load_observations(job_id)

        # Assert - All observations present
        assert len(all_observations) == 3
        agents = [obs.agent for obs in all_observations]
        assert "analysis" in agents
        assert "figures" in agents
        assert "tables" in agents

    @pytest.mark.asyncio
    async def test_load_nonexistent_observations(self, storage_service):
        """Test loading observations that don't exist returns empty list."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Act
        loaded_observations = await remediation_storage.load_observations(job_id)

        # Assert
        assert loaded_observations == []

    @pytest.mark.asyncio
    async def test_update_observation_status(self, storage_service):
        """Test updating a single observation's status."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())
        obs_id = str(uuid.uuid4())

        observations = [
            Observation(
                id=obs_id,
                job_id=job_id,
                agent="figures",
                visual_description="Test observation",
                markup_description="Test markup",
                location=ObservationLocation(
                    location_type="region",
                    value="page 1",
                    page_num=1
                ),
                status="open"
            )
        ]

        await remediation_storage.save_observations(job_id, observations)

        # Act - Update status
        proposal_id = str(uuid.uuid4())
        success = await remediation_storage.update_observation_status(
            job_id=job_id,
            observation_id=obs_id,
            status="resolved",
            resolved_by=proposal_id
        )

        # Assert
        assert success is True

        # Verify update
        loaded = await remediation_storage.load_observations(job_id)
        assert len(loaded) == 1
        assert loaded[0].status == "resolved"
        assert loaded[0].resolved_by == proposal_id


@pytest.mark.integration
class TestProposalOperations:
    """Tests for Proposal save/load/update operations with real S3."""

    @pytest.mark.asyncio
    async def test_save_and_load_proposals(self, storage_service):
        """Test saving and loading proposals to/from S3."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        proposals = [
            Proposal(
                id=str(uuid.uuid4()),
                job_id=job_id,
                resolves=["obs-1", "obs-2"],
                diff=SearchReplaceDiff(
                    search="![](figure-1.png)",
                    replace="![Flowchart showing registration process](figure-1.png)"
                ),
                justification="Combining empty alt text with visual content analysis",
                page_nums=[2],
                estimated_impact="Adds descriptive alt text to 1 image",
                route="auto",
                status="pending"
            ),
            Proposal(
                id=str(uuid.uuid4()),
                job_id=job_id,
                resolves=["obs-3"],
                diff=SearchReplaceDiff(
                    search="| A | B | C |\n|---|---|---|",
                    replace="| Student | Grade | Comments |\n|---------|-------|----------|\n| A | B | C |"
                ),
                justification="Adding table headers for accessibility",
                page_nums=[3],
                estimated_impact="Adds semantic structure to 1 table",
                route="manual",
                status="pending"
            )
        ]

        # Act - Save proposals
        key = await remediation_storage.save_proposals(job_id, proposals)

        # Assert - Save operation
        assert key == f"{job_id}/proposals.json"

        # Act - Load proposals
        loaded_proposals = await remediation_storage.load_proposals(job_id)

        # Assert - Loaded data matches original
        assert len(loaded_proposals) == 2

        # Check first proposal
        prop1 = loaded_proposals[0]
        assert prop1.job_id == job_id
        assert prop1.resolves == ["obs-1", "obs-2"]
        assert "figure-1.png" in prop1.diff.search
        assert "Flowchart" in prop1.diff.replace
        assert "alt text" in prop1.justification
        assert prop1.page_nums == [2]
        assert prop1.route == "auto"
        assert prop1.status == "pending"

        # Check second proposal
        prop2 = loaded_proposals[1]
        assert prop2.resolves == ["obs-3"]
        assert "Student" in prop2.diff.replace
        assert prop2.route == "manual"

    @pytest.mark.asyncio
    async def test_update_proposal_status(self, storage_service):
        """Test updating a single proposal's status and review fields."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())
        proposal_id = str(uuid.uuid4())

        proposals = [
            Proposal(
                id=proposal_id,
                job_id=job_id,
                resolves=["obs-1"],
                diff=SearchReplaceDiff(
                    search="test",
                    replace="TEST"
                ),
                justification="Test justification",
                status="pending"
            )
        ]

        await remediation_storage.save_proposals(job_id, proposals)

        # Act - Update to approved with review fields
        success = await remediation_storage.update_proposal_status(
            job_id=job_id,
            proposal_id=proposal_id,
            status="approved",
            reviewed_by="reviewer@test.com",
            reviewed_at=datetime.now(UTC),
            review_notes="Looks good"
        )

        # Assert
        assert success is True

        # Verify update
        loaded = await remediation_storage.load_proposals(job_id)
        assert len(loaded) == 1
        assert loaded[0].status == "approved"
        assert loaded[0].reviewed_by == "reviewer@test.com"
        assert loaded[0].reviewed_at is not None
        assert loaded[0].review_notes == "Looks good"

    @pytest.mark.asyncio
    async def test_update_nonexistent_proposal(self, storage_service):
        """Test updating a proposal that doesn't exist returns False."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Save empty proposal list
        await remediation_storage.save_proposals(job_id, [])

        # Act - Try to update nonexistent proposal
        success = await remediation_storage.update_proposal_status(
            job_id=job_id,
            proposal_id="nonexistent-id",
            status="approved"
        )

        # Assert
        assert success is False

    @pytest.mark.asyncio
    async def test_load_nonexistent_proposals(self, storage_service):
        """Test loading proposals that don't exist returns empty list."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Act
        loaded_proposals = await remediation_storage.load_proposals(job_id)

        # Assert
        assert loaded_proposals == []


@pytest.mark.integration
class TestApplicationLogOperations:
    """Tests for application log save/load operations with real S3."""

    @pytest.mark.asyncio
    async def test_save_and_load_application_log(self, storage_service):
        """Test saving and loading application log to/from S3."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        log_entries = [
            {
                "proposal_id": "prop-1",
                "status": "applied",
                "method": "exact",
                "timestamp": datetime.now(UTC).isoformat()
            },
            {
                "proposal_id": "prop-2",
                "status": "failed",
                "error": "Search text not found in document",
                "timestamp": datetime.now(UTC).isoformat()
            },
            {
                "proposal_id": "prop-3",
                "status": "applied",
                "method": "whitespace_normalized",
                "timestamp": datetime.now(UTC).isoformat()
            }
        ]

        # Act - Save log
        key = await remediation_storage.save_application_log(job_id, log_entries)

        # Assert - Save operation
        assert key == f"{job_id}/application-log.json"

        # Act - Load log
        loaded_log = await remediation_storage.load_application_log(job_id)

        # Assert - Loaded data matches original
        assert len(loaded_log) == 3
        assert loaded_log[0]["proposal_id"] == "prop-1"
        assert loaded_log[0]["status"] == "applied"
        assert loaded_log[0]["method"] == "exact"
        assert loaded_log[1]["status"] == "failed"
        assert loaded_log[1]["error"] == "Search text not found in document"
        assert loaded_log[2]["method"] == "whitespace_normalized"

    @pytest.mark.asyncio
    async def test_load_nonexistent_application_log(self, storage_service):
        """Test loading application log that doesn't exist returns empty list."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Act
        loaded_log = await remediation_storage.load_application_log(job_id)

        # Assert
        assert loaded_log == []


@pytest.mark.integration
class TestMarkdownOperations:
    """Tests for current markdown save/load operations with real S3."""

    @pytest.mark.asyncio
    async def test_save_and_load_current_markdown(self, storage_service):
        """Test saving and loading current markdown to/from S3."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())
        markdown_content = """# Test Document

This is a test document with multiple sections.

## Section 1

Content for section 1.

![](figure-1.png)

## Section 2

More content with a table:

| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |
"""

        # Act - Save markdown
        key = await remediation_storage.save_current_markdown(job_id, markdown_content)

        # Assert - Save operation
        assert key == f"{job_id}.md"

        # Act - Load markdown
        loaded_markdown = await remediation_storage.load_current_markdown(job_id)

        # Assert - Loaded content matches original
        assert loaded_markdown is not None
        assert loaded_markdown == markdown_content
        assert "# Test Document" in loaded_markdown
        assert "figure-1.png" in loaded_markdown

    @pytest.mark.asyncio
    async def test_load_nonexistent_markdown(self, storage_service):
        """Test loading markdown that doesn't exist returns None."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Act
        loaded_markdown = await remediation_storage.load_current_markdown(job_id)

        # Assert
        assert loaded_markdown is None

    @pytest.mark.asyncio
    async def test_load_markdown_falls_back_to_v0(self, storage_service):
        """Test loading markdown falls back to v0 version if current doesn't exist."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())
        v0_content = "# Original Version\n\nThis is the v0 markdown."

        # Save only v0 version
        storage_service.s3_client.put_object(
            Bucket=storage_service.results_bucket,
            Key=f"{job_id}-v0.md",
            Body=v0_content.encode("utf-8"),
            ContentType="text/markdown"
        )

        # Act - Load current (should fall back to v0)
        loaded_markdown = await remediation_storage.load_current_markdown(job_id)

        # Assert - Got v0 content
        assert loaded_markdown is not None
        assert loaded_markdown == v0_content
        assert "Original Version" in loaded_markdown


@pytest.mark.integration
class TestEndToEndWorkflow:
    """Test complete end-to-end workflow with all remediation artifacts."""

    @pytest.mark.asyncio
    async def test_complete_remediation_workflow(self, storage_service):
        """Test complete workflow: manifest → observations → proposals → application log."""
        # Arrange
        remediation_storage = RemediationStorageService(storage_service)
        job_id = str(uuid.uuid4())

        # Phase 1: Save manifest
        heading_tree_data = {
            "document_title": "Complete Workflow Test",
            "title_page": 1,
            "sections": [],
            "total_pages": 2,
            "layout_type": "single_column",
            "confidence": 0.95,
            "observations": ""
        }

        manifest = DocumentManifest(
            job_id=job_id,
            document_title="Complete Workflow Test",
            total_pages=2,
            heading_tree_json=json.dumps(heading_tree_data),
            page_features=[
                PageFeatures(page_num=1, has_images=True, image_count=1),
                PageFeatures(page_num=2, has_tables=True, table_count=1)
            ],
            required_agents=["figures", "tables"]
        )

        await remediation_storage.save_manifest(job_id, manifest)

        # Phase 2: Save initial observations (from analysis)
        obs1_id = str(uuid.uuid4())
        obs2_id = str(uuid.uuid4())

        initial_observations = [
            Observation(
                id=obs1_id,
                job_id=job_id,
                agent="analysis",
                visual_description="Image on page 1",
                markup_description="Empty alt text",
                location=ObservationLocation(
                    location_type="element",
                    value="img.figure-1",
                    page_num=1
                )
            )
        ]

        await remediation_storage.save_observations(job_id, initial_observations)

        # Phase 3: Append specialized agent observations
        specialized_observations = [
            Observation(
                id=obs2_id,
                job_id=job_id,
                agent="figures",
                visual_description="Flowchart with 3 steps",
                markup_description="No alt text description",
                location=ObservationLocation(
                    location_type="element",
                    value="img.figure-1",
                    page_num=1
                )
            )
        ]

        await remediation_storage.append_observations(job_id, specialized_observations)

        # Phase 4: Save proposals (auto-corrections)
        proposal_id = str(uuid.uuid4())

        proposals = [
            Proposal(
                id=proposal_id,
                job_id=job_id,
                resolves=[obs1_id, obs2_id],
                diff=SearchReplaceDiff(
                    search="![](figure-1.png)",
                    replace="![Flowchart showing three-step process](figure-1.png)"
                ),
                justification="Combining observations about missing alt text",
                page_nums=[1],
                route="auto",
                status="pending"
            )
        ]

        await remediation_storage.save_proposals(job_id, proposals)

        # Phase 5: Approve proposal
        await remediation_storage.update_proposal_status(
            job_id=job_id,
            proposal_id=proposal_id,
            status="approved",
            reviewed_by="test@example.com"
        )

        # Phase 6: Save application log
        app_log = [
            {
                "proposal_id": proposal_id,
                "status": "applied",
                "method": "exact",
                "timestamp": datetime.now(UTC).isoformat()
            }
        ]

        await remediation_storage.save_application_log(job_id, app_log)

        # Phase 7: Update observation status to resolved
        await remediation_storage.update_observation_status(
            job_id=job_id,
            observation_id=obs1_id,
            status="resolved",
            resolved_by=proposal_id
        )

        # Verify entire state
        loaded_manifest = await remediation_storage.load_manifest(job_id)
        loaded_observations = await remediation_storage.load_observations(job_id)
        loaded_proposals = await remediation_storage.load_proposals(job_id)
        loaded_log = await remediation_storage.load_application_log(job_id)

        # Assert - All data persisted correctly
        assert loaded_manifest is not None
        assert loaded_manifest.document_title == "Complete Workflow Test"

        assert len(loaded_observations) == 2
        assert loaded_observations[0].status == "resolved"
        assert loaded_observations[0].resolved_by == proposal_id

        assert len(loaded_proposals) == 1
        assert loaded_proposals[0].status == "approved"
        assert loaded_proposals[0].reviewed_by == "test@example.com"

        assert len(loaded_log) == 1
        assert loaded_log[0]["status"] == "applied"
        assert loaded_log[0]["method"] == "exact"
