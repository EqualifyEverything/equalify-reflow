"""Unit tests for RemediationStorageService."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from src.services.remediation_storage_service import RemediationStorageService
from src.services.storage_service import StorageService
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.proposal import Proposal, SearchReplaceDiff
from src.shared.models.remediation import DocumentManifest, PageFeatures


@pytest.fixture
def mock_s3_client() -> MagicMock:
    """Create a mock S3 client."""
    return MagicMock()


@pytest.fixture
def mock_storage_service(mock_s3_client: MagicMock) -> StorageService:
    """Create a mock StorageService."""
    storage = MagicMock(spec=StorageService)
    storage.s3_client = mock_s3_client
    storage.results_bucket = "test-results-bucket"
    storage.temp_bucket = "test-temp-bucket"
    return storage


@pytest.fixture
def remediation_storage(mock_storage_service: StorageService) -> RemediationStorageService:
    """Create a RemediationStorageService with mocked dependencies."""
    return RemediationStorageService(mock_storage_service)


@pytest.fixture
def sample_manifest() -> DocumentManifest:
    """Create a sample DocumentManifest for testing."""
    return DocumentManifest(
        job_id="test-job-123",
        document_title="Test Document",
        document_type="syllabus",
        total_pages=5,
        heading_tree_json='{"sections": []}',
        page_features=[
            PageFeatures(page_num=1, has_images=True, image_count=2),
            PageFeatures(page_num=2, has_tables=True, table_count=1),
        ],
        required_agents=["figures", "tables"],
        analysis_confidence=0.9
    )


@pytest.fixture
def sample_observation() -> Observation:
    """Create a sample Observation for testing."""
    return Observation(
        id="obs-123",
        job_id="test-job-123",
        agent="figures",
        visual_description="Image shows flowchart",
        markup_description="Empty alt text",
        location=ObservationLocation(
            location_type="element",
            value="img.figure-1",
            page_num=1
        ),
        confidence=0.9,
        severity="major"
    )


@pytest.fixture
def sample_proposal() -> Proposal:
    """Create a sample Proposal for testing."""
    return Proposal(
        id="prop-123",
        job_id="test-job-123",
        resolves=["obs-123"],
        diff=SearchReplaceDiff(
            search="![](figure-1.png)",
            replace="![Flowchart description](figure-1.png)"
        ),
        justification="Adding alt text to image",
        page_nums=[1],
        estimated_impact="Adds alt text to 1 image"
    )


class TestSaveManifest:
    """Tests for save_manifest method."""

    @pytest.mark.asyncio
    async def test_save_manifest_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_manifest: DocumentManifest
    ) -> None:
        """Test successful manifest save."""
        key = await remediation_storage.save_manifest(
            "test-job-123",
            sample_manifest
        )

        assert key == "test-job-123/manifest.json"
        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-results-bucket"
        assert call_kwargs["Key"] == "test-job-123/manifest.json"
        assert call_kwargs["ContentType"] == "application/json"

    @pytest.mark.asyncio
    async def test_save_manifest_content(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_manifest: DocumentManifest
    ) -> None:
        """Test manifest content is correct JSON."""
        await remediation_storage.save_manifest("test-job-123", sample_manifest)

        call_kwargs = mock_s3_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        data = json.loads(body)

        assert data["job_id"] == "test-job-123"
        assert data["document_title"] == "Test Document"
        assert len(data["page_features"]) == 2

    @pytest.mark.asyncio
    async def test_save_manifest_error(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_manifest: DocumentManifest
    ) -> None:
        """Test save_manifest raises on S3 error."""
        mock_s3_client.put_object.side_effect = Exception("S3 error")

        with pytest.raises(Exception, match="S3 error"):
            await remediation_storage.save_manifest("test-job-123", sample_manifest)


class TestLoadManifest:
    """Tests for load_manifest method."""

    @pytest.mark.asyncio
    async def test_load_manifest_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_manifest: DocumentManifest
    ) -> None:
        """Test successful manifest load."""
        # Mock S3 response
        mock_body = MagicMock()
        mock_body.read.return_value = sample_manifest.model_dump_json().encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.load_manifest("test-job-123")

        assert result is not None
        assert result.job_id == "test-job-123"
        assert result.document_title == "Test Document"
        assert len(result.page_features) == 2

    @pytest.mark.asyncio
    async def test_load_manifest_not_found(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock
    ) -> None:
        """Test load_manifest returns None when not found."""
        error_response = {"Error": {"Code": "NoSuchKey"}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

        result = await remediation_storage.load_manifest("nonexistent-job")

        assert result is None

    @pytest.mark.asyncio
    async def test_load_manifest_invalid_json(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock
    ) -> None:
        """Test load_manifest returns None for invalid JSON."""
        mock_body = MagicMock()
        mock_body.read.return_value = b"not valid json"
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.load_manifest("test-job-123")

        assert result is None


class TestSaveObservations:
    """Tests for save_observations method."""

    @pytest.mark.asyncio
    async def test_save_observations_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_observation: Observation
    ) -> None:
        """Test successful observations save."""
        key = await remediation_storage.save_observations(
            "test-job-123",
            [sample_observation]
        )

        assert key == "test-job-123/observations.json"
        mock_s3_client.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_observations_content(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_observation: Observation
    ) -> None:
        """Test observations content is correct JSON array."""
        await remediation_storage.save_observations(
            "test-job-123",
            [sample_observation, sample_observation]
        )

        call_kwargs = mock_s3_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        data = json.loads(body)

        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["id"] == "obs-123"

    @pytest.mark.asyncio
    async def test_save_empty_observations(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock
    ) -> None:
        """Test saving empty observations list."""
        key = await remediation_storage.save_observations("test-job-123", [])

        call_kwargs = mock_s3_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        data = json.loads(body)

        assert data == []
        assert key == "test-job-123/observations.json"


class TestLoadObservations:
    """Tests for load_observations method."""

    @pytest.mark.asyncio
    async def test_load_observations_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_observation: Observation
    ) -> None:
        """Test successful observations load."""
        observations_json = json.dumps([sample_observation.model_dump()], default=str)
        mock_body = MagicMock()
        mock_body.read.return_value = observations_json.encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.load_observations("test-job-123")

        assert len(result) == 1
        assert result[0].id == "obs-123"
        assert result[0].agent == "figures"

    @pytest.mark.asyncio
    async def test_load_observations_not_found(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock
    ) -> None:
        """Test load_observations returns empty list when not found."""
        error_response = {"Error": {"Code": "NoSuchKey"}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

        result = await remediation_storage.load_observations("nonexistent-job")

        assert result == []

    @pytest.mark.asyncio
    async def test_load_observations_invalid_json(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock
    ) -> None:
        """Test load_observations returns empty list for invalid JSON."""
        mock_body = MagicMock()
        mock_body.read.return_value = b"invalid json"
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.load_observations("test-job-123")

        assert result == []


class TestAppendObservations:
    """Tests for append_observations method."""

    @pytest.mark.asyncio
    async def test_append_observations(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_observation: Observation
    ) -> None:
        """Test appending observations to existing file."""
        # Setup existing observations
        existing_obs = Observation(
            id="obs-existing",
            job_id="test-job-123",
            agent="tables",
            visual_description="Existing",
            markup_description="Existing",
            location=ObservationLocation(value="test", page_num=1)
        )
        existing_json = json.dumps([existing_obs.model_dump()], default=str)
        mock_body = MagicMock()
        mock_body.read.return_value = existing_json.encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        # Append new observation
        await remediation_storage.append_observations(
            "test-job-123",
            [sample_observation]
        )

        # Verify save was called with merged list
        call_kwargs = mock_s3_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        data = json.loads(body)

        assert len(data) == 2
        assert data[0]["id"] == "obs-existing"
        assert data[1]["id"] == "obs-123"


class TestSaveProposals:
    """Tests for save_proposals method."""

    @pytest.mark.asyncio
    async def test_save_proposals_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_proposal: Proposal
    ) -> None:
        """Test successful proposals save."""
        key = await remediation_storage.save_proposals(
            "test-job-123",
            [sample_proposal]
        )

        assert key == "test-job-123/proposals.json"
        mock_s3_client.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_proposals_content(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_proposal: Proposal
    ) -> None:
        """Test proposals content is correct JSON."""
        await remediation_storage.save_proposals(
            "test-job-123",
            [sample_proposal]
        )

        call_kwargs = mock_s3_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        data = json.loads(body)

        assert len(data) == 1
        assert data[0]["id"] == "prop-123"
        assert data[0]["diff"]["search"] == "![](figure-1.png)"


class TestLoadProposals:
    """Tests for load_proposals method."""

    @pytest.mark.asyncio
    async def test_load_proposals_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_proposal: Proposal
    ) -> None:
        """Test successful proposals load."""
        proposals_json = json.dumps([sample_proposal.model_dump()], default=str)
        mock_body = MagicMock()
        mock_body.read.return_value = proposals_json.encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.load_proposals("test-job-123")

        assert len(result) == 1
        assert result[0].id == "prop-123"
        assert result[0].diff.search == "![](figure-1.png)"

    @pytest.mark.asyncio
    async def test_load_proposals_not_found(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock
    ) -> None:
        """Test load_proposals returns empty list when not found."""
        error_response = {"Error": {"Code": "NoSuchKey"}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

        result = await remediation_storage.load_proposals("nonexistent-job")

        assert result == []


class TestUpdateProposalStatus:
    """Tests for update_proposal_status method."""

    @pytest.mark.asyncio
    async def test_update_proposal_status_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_proposal: Proposal
    ) -> None:
        """Test updating a proposal's status."""
        # Setup existing proposal
        proposals_json = json.dumps([sample_proposal.model_dump()], default=str)
        mock_body = MagicMock()
        mock_body.read.return_value = proposals_json.encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.update_proposal_status(
            "test-job-123",
            "prop-123",
            "approved",
            reviewed_by="reviewer@test.com",
            review_notes="Looks good"
        )

        assert result is True

        # Verify save was called with updated proposal
        call_kwargs = mock_s3_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        data = json.loads(body)

        assert data[0]["status"] == "approved"
        assert data[0]["reviewed_by"] == "reviewer@test.com"
        assert data[0]["review_notes"] == "Looks good"

    @pytest.mark.asyncio
    async def test_update_proposal_status_not_found(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_proposal: Proposal
    ) -> None:
        """Test updating a non-existent proposal returns False."""
        proposals_json = json.dumps([sample_proposal.model_dump()], default=str)
        mock_body = MagicMock()
        mock_body.read.return_value = proposals_json.encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.update_proposal_status(
            "test-job-123",
            "nonexistent-prop",
            "approved"
        )

        assert result is False


class TestUpdateObservationStatus:
    """Tests for update_observation_status method."""

    @pytest.mark.asyncio
    async def test_update_observation_status_success(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_observation: Observation
    ) -> None:
        """Test updating an observation's status."""
        observations_json = json.dumps([sample_observation.model_dump()], default=str)
        mock_body = MagicMock()
        mock_body.read.return_value = observations_json.encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.update_observation_status(
            "test-job-123",
            "obs-123",
            "resolved",
            resolved_by="prop-123"
        )

        assert result is True

        # Verify save was called with updated observation
        call_kwargs = mock_s3_client.put_object.call_args[1]
        body = call_kwargs["Body"].decode("utf-8")
        data = json.loads(body)

        assert data[0]["status"] == "resolved"
        assert data[0]["resolved_by"] == "prop-123"

    @pytest.mark.asyncio
    async def test_update_observation_status_not_found(
        self,
        remediation_storage: RemediationStorageService,
        mock_s3_client: MagicMock,
        sample_observation: Observation
    ) -> None:
        """Test updating a non-existent observation returns False."""
        observations_json = json.dumps([sample_observation.model_dump()], default=str)
        mock_body = MagicMock()
        mock_body.read.return_value = observations_json.encode()
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        result = await remediation_storage.update_observation_status(
            "test-job-123",
            "nonexistent-obs",
            "resolved"
        )

        assert result is False


class TestJsonSerializer:
    """Tests for the JSON serializer helper."""

    def test_datetime_serialization(
        self,
        remediation_storage: RemediationStorageService
    ) -> None:
        """Test datetime objects are serialized to ISO format."""
        now = datetime.now(UTC)
        result = remediation_storage._json_serializer(now)

        assert isinstance(result, str)
        assert "T" in result  # ISO format contains T separator

    def test_unsupported_type_raises(
        self,
        remediation_storage: RemediationStorageService
    ) -> None:
        """Test unsupported types raise TypeError."""
        with pytest.raises(TypeError, match="not JSON serializable"):
            remediation_storage._json_serializer(set([1, 2, 3]))
