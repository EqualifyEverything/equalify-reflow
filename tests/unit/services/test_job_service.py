"""Unit tests for Job Service."""

import json

import pytest
from src.config import settings
from src.services.job_service import JobService


@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client with Lua script support."""
    client = mocker.AsyncMock()

    # register_script returns a callable async mock (simulates Lua Script objects)
    def _make_script(*args, **kwargs):
        return mocker.AsyncMock()

    client.register_script = mocker.MagicMock(side_effect=_make_script)
    return client


@pytest.fixture
def job_service(mock_redis_client):
    """Create job service with mock client."""
    svc = JobService(redis_client=mock_redis_client)
    return svc


class TestCreateJob:
    """Tests for create_job method."""

    @pytest.mark.asyncio
    async def test_create_job_success(self, job_service, mock_redis_client):
        """Test successful job creation calls Lua script."""
        await job_service.create_job(
            job_id="job123",
            s3_key="temp/job123/file.pdf",
            status="pii_scanning"
        )

        # Verify the hset_expire_zadd script was called
        job_service._script_hset_expire_zadd.assert_called_once()
        call_kwargs = job_service._script_hset_expire_zadd.call_args
        keys = call_kwargs.kwargs["keys"]
        args = call_kwargs.kwargs["args"]

        # First key is the job hash, second is the index
        assert keys[0] == f"{settings.job_status_prefix}job123"
        assert keys[1] == job_service.jobs_index_key

        # args[0] = ttl, args[1] = timestamp, args[2] = job_id, rest = field/value pairs
        assert args[2] == "job123"
        # Flatten args into field/value pairs starting at index 3
        field_values = dict(zip(args[3::2], args[4::2]))
        assert field_values["job_id"] == "job123"
        assert field_values["s3_key"] == "temp/job123/file.pdf"
        assert field_values["status"] == "pii_scanning"
        assert "created_at" in field_values
        assert "updated_at" in field_values

    @pytest.mark.asyncio
    async def test_create_job_default_status(self, job_service, mock_redis_client):
        """Test job creation with default status."""
        await job_service.create_job(
            job_id="job456",
            s3_key="temp/file.pdf"
        )

        call_kwargs = job_service._script_hset_expire_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[3::2], args[4::2]))
        assert field_values["status"] == "pii_scanning"

    @pytest.mark.asyncio
    async def test_create_job_sets_ttl_for_active_status(self, job_service):
        """Test that create_job passes the correct TTL for active status."""
        await job_service.create_job(
            job_id="job789",
            s3_key="temp/file.pdf",
            status="pii_scanning",
        )

        call_kwargs = job_service._script_hset_expire_zadd.call_args
        args = call_kwargs.kwargs["args"]
        ttl = int(args[0])
        assert ttl == settings.job_ttl_active

    @pytest.mark.asyncio
    async def test_create_job_with_optional_fields(self, job_service):
        """Test job creation with all optional fields."""
        await job_service.create_job(
            job_id="job-opt",
            s3_key="temp/file.pdf",
            original_filename="doc.pdf",
            pii_skipped=True,
            pii_skip_reason="Trusted source",
            debug_bundle_requested=True,
            review_mode="auto",
            max_rounds=3,
        )

        call_kwargs = job_service._script_hset_expire_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[3::2], args[4::2]))
        assert field_values["original_filename"] == "doc.pdf"
        assert field_values["pii_skipped"] == "true"
        assert field_values["pii_skip_reason"] == "Trusted source"
        assert field_values["debug_bundle_requested"] == "true"
        assert field_values["review_mode"] == "auto"
        assert field_values["max_rounds"] == "3"


class TestGetJob:
    """Tests for get_job method."""

    @pytest.mark.asyncio
    async def test_get_job_success(self, job_service, mock_redis_client):
        """Test retrieving existing job."""
        job_data = {
            "job_id": "job123",
            "s3_key": "temp/job123/file.pdf",
            "status": "processing",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:35:00Z"
        }
        mock_redis_client.hgetall.return_value = job_data

        result = await job_service.get_job("job123")

        assert result == job_data
        mock_redis_client.hgetall.assert_called_once_with(
            f"{settings.job_status_prefix}job123"
        )

    @pytest.mark.asyncio
    async def test_get_job_with_pii_findings(self, job_service, mock_redis_client):
        """Test retrieving job with PII findings."""
        pii_findings = [
            {"entity_type": "PERSON", "score": 0.85}
        ]
        job_data = {
            "job_id": "job123",
            "status": "awaiting_approval",
            "pii_findings": json.dumps(pii_findings),
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:35:00Z"
        }
        mock_redis_client.hgetall.return_value = job_data

        result = await job_service.get_job("job123")

        # Verify PII findings were deserialized
        assert result["pii_findings"] == pii_findings

    @pytest.mark.asyncio
    async def test_get_job_with_json_array_fields(self, job_service, mock_redis_client):
        """Test retrieving job with JSON array fields (correction_results, page_image_keys)."""
        correction_results = [{"page": 1, "corrections": []}]
        page_image_keys = ["page-1.png", "page-2.png"]

        job_data = {
            "job_id": "job123",
            "correction_results": json.dumps(correction_results),
            "page_image_keys": json.dumps(page_image_keys),
            "created_at": "2024-01-15T10:30:00Z"
        }
        mock_redis_client.hgetall.return_value = job_data

        result = await job_service.get_job("job123")

        assert result["correction_results"] == correction_results
        assert result["page_image_keys"] == page_image_keys
        assert "metadata" not in result

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, job_service, mock_redis_client):
        """Test retrieving non-existent job."""
        mock_redis_client.hgetall.return_value = {}

        result = await job_service.get_job("nonexistent")

        assert result is None


class TestUpdateJobStatus:
    """Tests for update_job_status method."""

    @pytest.mark.asyncio
    async def test_update_status_simple(self, job_service):
        """Test simple status update uses Lua script."""
        await job_service.update_job_status("job123", "processing")

        job_service._script_hset_expire_zadd.assert_called_once()
        call_kwargs = job_service._script_hset_expire_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[3::2], args[4::2]))
        assert field_values["status"] == "processing"
        assert "updated_at" in field_values

    @pytest.mark.asyncio
    async def test_update_status_with_metadata(self, job_service):
        """Test status update with additional metadata."""
        await job_service.update_job_status(
            "job123",
            "completed",
            result_url="https://s3.../result.html",
            confidence_score=0.95
        )

        call_kwargs = job_service._script_hset_expire_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[3::2], args[4::2]))
        assert field_values["status"] == "completed"
        assert field_values["result_url"] == "https://s3.../result.html"
        assert field_values["confidence_score"] == "0.95"

    @pytest.mark.asyncio
    async def test_update_status_sets_correct_ttl(self, job_service):
        """Test that update sets correct TTL for completed status."""
        await job_service.update_job_status("job123", "completed")

        call_kwargs = job_service._script_hset_expire_zadd.call_args
        args = call_kwargs.kwargs["args"]
        ttl = int(args[0])
        assert ttl == settings.job_ttl_completed

    @pytest.mark.asyncio
    async def test_update_status_with_complex_fields(self, job_service):
        """Test status update with dict/list fields stored at top level."""
        correction_results = [{"page": 1, "corrections": []}]
        page_image_keys = ["page-1.png", "page-2.png"]

        await job_service.update_job_status(
            "job123",
            "processing",
            correction_results=correction_results,
            page_image_keys=page_image_keys
        )

        call_kwargs = job_service._script_hset_expire_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[3::2], args[4::2]))
        # Complex fields should be JSON serialized
        assert isinstance(field_values["correction_results"], str)
        assert json.loads(field_values["correction_results"]) == correction_results
        assert isinstance(field_values["page_image_keys"], str)
        assert json.loads(field_values["page_image_keys"]) == page_image_keys
        assert "metadata" not in field_values


class TestAddPiiFindings:
    """Tests for add_pii_findings method."""

    @pytest.mark.asyncio
    async def test_add_pii_findings(self, job_service):
        """Test adding PII findings to job uses hset_zadd script."""
        findings = [
            {"entity_type": "PERSON", "text": "John Doe", "score": 0.85},
            {"entity_type": "EMAIL", "text": "john@example.com", "score": 0.95}
        ]

        await job_service.add_pii_findings("job123", findings)

        job_service._script_hset_zadd.assert_called_once()
        call_kwargs = job_service._script_hset_zadd.call_args
        args = call_kwargs.kwargs["args"]
        # args[0] = timestamp, args[1] = job_id, rest = field/value pairs
        field_values = dict(zip(args[2::2], args[3::2]))
        assert "pii_findings" in field_values
        assert "updated_at" in field_values
        stored_findings = json.loads(field_values["pii_findings"])
        assert stored_findings == findings

    @pytest.mark.asyncio
    async def test_add_empty_pii_findings(self, job_service):
        """Test adding empty PII findings list."""
        await job_service.add_pii_findings("job123", [])

        call_kwargs = job_service._script_hset_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[2::2], args[3::2]))
        stored_findings = json.loads(field_values["pii_findings"])
        assert stored_findings == []


class TestAddProcessingResult:
    """Tests for add_processing_result method."""

    @pytest.mark.asyncio
    async def test_add_processing_result(self, job_service):
        """Test adding processing result uses hset_zadd script."""
        await job_service.add_processing_result(
            job_id="job123",
            result_url="https://s3.amazonaws.com/results/job123.html",
            confidence=0.92
        )

        job_service._script_hset_zadd.assert_called_once()
        call_kwargs = job_service._script_hset_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[2::2], args[3::2]))
        assert field_values["result_url"] == "https://s3.amazonaws.com/results/job123.html"
        assert field_values["confidence_score"] == "0.92"
        assert "completed_at" in field_values
        assert "updated_at" in field_values

    @pytest.mark.asyncio
    async def test_add_processing_result_low_confidence(self, job_service):
        """Test adding result with low confidence."""
        await job_service.add_processing_result(
            job_id="job456",
            result_url="https://s3.../result.html",
            confidence=0.45
        )

        call_kwargs = job_service._script_hset_zadd.call_args
        args = call_kwargs.kwargs["args"]
        field_values = dict(zip(args[2::2], args[3::2]))
        assert float(field_values["confidence_score"]) == 0.45


class TestJobExists:
    """Tests for job_exists method."""

    @pytest.mark.asyncio
    async def test_job_exists_true(self, job_service, mock_redis_client):
        """Test checking existing job."""
        mock_redis_client.exists.return_value = 1

        exists = await job_service.job_exists("job123")

        assert exists is True
        mock_redis_client.exists.assert_called_once_with(
            f"{settings.job_status_prefix}job123"
        )

    @pytest.mark.asyncio
    async def test_job_exists_false(self, job_service, mock_redis_client):
        """Test checking non-existent job."""
        mock_redis_client.exists.return_value = 0

        exists = await job_service.job_exists("nonexistent")

        assert exists is False

    @pytest.mark.asyncio
    async def test_job_exists_error(self, job_service, mock_redis_client):
        """Test handling of existence check error."""
        mock_redis_client.exists.side_effect = Exception("Redis error")

        exists = await job_service.job_exists("job123")

        assert exists is False


class TestDeleteJob:
    """Tests for delete_job method."""

    @pytest.mark.asyncio
    async def test_delete_job_success(self, job_service):
        """Test successful job deletion uses Lua script."""
        job_service._script_delete_job.return_value = 1

        await job_service.delete_job("job123")

        job_service._script_delete_job.assert_called_once()
        call_kwargs = job_service._script_delete_job.call_args
        keys = call_kwargs.kwargs["keys"]
        args = call_kwargs.kwargs["args"]
        assert keys[0] == f"{settings.job_status_prefix}job123"
        assert keys[1] == job_service.jobs_index_key
        assert args[0] == "job123"

    @pytest.mark.asyncio
    async def test_delete_job_failure(self, job_service):
        """Test handling of deletion failure."""
        job_service._script_delete_job.side_effect = Exception("Redis error")

        with pytest.raises(Exception) as exc:
            await job_service.delete_job("job123")

        assert "Failed to delete job" in str(exc.value)


class TestSetExpiration:
    """Tests for set_expiration method."""

    @pytest.mark.asyncio
    async def test_set_expiration_success(self, job_service, mock_redis_client):
        """Test setting job expiration."""
        mock_redis_client.expire.return_value = 1

        ttl_seconds = 86400  # 1 day
        await job_service.set_expiration("job123", ttl_seconds)

        mock_redis_client.expire.assert_called_once_with(
            f"{settings.job_status_prefix}job123",
            ttl_seconds
        )

    @pytest.mark.asyncio
    async def test_set_expiration_30_days(self, job_service, mock_redis_client):
        """Test setting 30-day expiration."""
        mock_redis_client.expire.return_value = 1

        ttl_seconds = settings.job_ttl_days * 24 * 60 * 60
        await job_service.set_expiration("job456", ttl_seconds)

        call_args = mock_redis_client.expire.call_args.args
        assert call_args[1] == ttl_seconds

    @pytest.mark.asyncio
    async def test_set_expiration_failure(self, job_service, mock_redis_client):
        """Test handling of expiration setting failure."""
        mock_redis_client.expire.side_effect = Exception("Redis error")

        with pytest.raises(Exception) as exc:
            await job_service.set_expiration("job123", 3600)

        assert "Failed to set expiration" in str(exc.value)


class TestJobLifecycle:
    """Integration tests for complete job lifecycle."""

    @pytest.mark.asyncio
    async def test_complete_job_flow(self, job_service, mock_redis_client):
        """Test complete job lifecycle from creation to completion."""
        job_id = "job123"
        mock_redis_client.expire.return_value = 1

        # 1. Create job (uses hset_expire_zadd script)
        await job_service.create_job(job_id, "temp/file.pdf", "pii_scanning")

        # 2. Add PII findings (uses hset_zadd script)
        findings = [{"entity_type": "PERSON", "score": 0.85}]
        await job_service.add_pii_findings(job_id, findings)

        # 3. Update to processing (uses hset_expire_zadd script)
        await job_service.update_job_status(job_id, "processing")

        # 4. Add processing result (uses hset_zadd script)
        await job_service.add_processing_result(
            job_id,
            "https://s3.../result.html",
            0.92
        )

        # 5. Set expiration (direct Redis call)
        await job_service.set_expiration(job_id, 86400)

        # Verify script calls: create (1) + update (1) = 2 hset_expire_zadd calls
        assert job_service._script_hset_expire_zadd.call_count == 2
        # Verify script calls: pii_findings (1) + processing_result (1) = 2 hset_zadd calls
        assert job_service._script_hset_zadd.call_count == 2
        # set_expiration uses direct Redis expire
        assert mock_redis_client.expire.call_count == 1

    @pytest.mark.asyncio
    async def test_job_no_pii_flow(self, job_service, mock_redis_client):
        """Test job flow when no PII is detected."""
        job_id = "job456"

        # Create and go straight to processing
        await job_service.create_job(job_id, "temp/file.pdf", "pii_scanning")
        await job_service.update_job_status(job_id, "processing")
        await job_service.add_processing_result(job_id, "https://s3.../result.html", 0.95)

        # Verify no hset_zadd calls for PII (only processing result uses it)
        assert job_service._script_hset_zadd.call_count == 1


class TestCleanupOldJob:
    """Tests for cleanup_old_job method."""

    @pytest.mark.asyncio
    async def test_cleanup_existing_job(self, job_service):
        """Test cleanup of existing job uses Lua script."""
        job_id = "job123"
        job_service._script_delete_job.return_value = 1

        deleted = await job_service.cleanup_old_job(job_id)

        assert deleted is True
        job_service._script_delete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_job(self, job_service):
        """Test cleanup when job doesn't exist."""
        job_service._script_delete_job.return_value = 0

        deleted = await job_service.cleanup_old_job("job456")

        assert deleted is False

    @pytest.mark.asyncio
    async def test_cleanup_handles_error(self, job_service):
        """Test error handling during cleanup."""
        job_service._script_delete_job.side_effect = Exception("Redis error")

        deleted = await job_service.cleanup_old_job("job-error")

        assert deleted is False


class TestListJobsUpdatedBefore:
    """Tests for list_jobs_updated_before method."""

    @pytest.mark.asyncio
    async def test_returns_job_ids(self, job_service, mock_redis_client):
        """Test returns job IDs from sorted set."""
        mock_redis_client.zrangebyscore.return_value = ["job-1", "job-2"]

        result = await job_service.list_jobs_updated_before(1700000000.0)

        assert result == ["job-1", "job-2"]
        mock_redis_client.zrangebyscore.assert_called_once_with(
            job_service.jobs_index_key, "-inf", "1700000000.0"
        )

    @pytest.mark.asyncio
    async def test_handles_bytes(self, job_service, mock_redis_client):
        """Test decodes bytes from Redis."""
        mock_redis_client.zrangebyscore.return_value = [b"job-1", b"job-2"]

        result = await job_service.list_jobs_updated_before(1700000000.0)

        assert result == ["job-1", "job-2"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, job_service, mock_redis_client):
        """Test returns empty list on error."""
        mock_redis_client.zrangebyscore.side_effect = Exception("Redis error")

        result = await job_service.list_jobs_updated_before(1700000000.0)

        assert result == []


class TestBackfillJobsIndex:
    """Tests for backfill_jobs_index method."""

    @pytest.mark.asyncio
    async def test_backfills_from_existing_jobs(self, job_service, mock_redis_client):
        """Test backfill populates index from existing jobs."""
        # Mock list_all_jobs via SCAN
        mock_redis_client.scan.return_value = (0, [f"{settings.job_status_prefix}job-1"])
        mock_redis_client.hgetall.return_value = {
            "updated_at": "2024-06-01T10:00:00+00:00",
        }
        mock_redis_client.zadd.return_value = 1

        count = await job_service.backfill_jobs_index()

        assert count == 1
        mock_redis_client.zadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_backfill_skips_empty_jobs(self, job_service, mock_redis_client):
        """Test backfill skips jobs with empty data."""
        mock_redis_client.scan.return_value = (0, [f"{settings.job_status_prefix}job-1"])
        mock_redis_client.hgetall.return_value = {}

        count = await job_service.backfill_jobs_index()

        assert count == 0
        mock_redis_client.zadd.assert_not_called()


class TestEmitJobAuditLog:
    """Tests for emit_job_audit_log and _build_audit_record."""

    @pytest.mark.asyncio
    async def test_emits_structured_log(self, job_service, mock_redis_client, caplog):
        """Test audit log emits structured JSON."""
        import json as json_mod

        mock_redis_client.hgetall.return_value = {
            "job_id": "job-audit-1",
            "status": "completed",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:35:00Z",
            "confidence_score": "0.92",
        }

        with caplog.at_level("INFO"):
            await job_service.emit_job_audit_log("job-audit-1", "retention_cleanup")

        # Find the JSON log line
        audit_lines = [r for r in caplog.records if "job_audit" in r.message]
        assert len(audit_lines) == 1
        record = json_mod.loads(audit_lines[0].message)
        assert record["event"] == "job_audit"
        assert record["job_id"] == "job-audit-1"
        assert record["status"] == "completed"
        assert record["reason"] == "retention_cleanup"
        assert record["confidence_score"] == "0.92"

    @pytest.mark.asyncio
    async def test_handles_missing_job(self, job_service, mock_redis_client, caplog):
        """Test audit log handles missing job gracefully."""
        mock_redis_client.hgetall.return_value = {}

        with caplog.at_level("WARNING"):
            await job_service.emit_job_audit_log("missing-job", "test")

        assert any("Cannot emit audit log" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_handles_redis_error(self, job_service, mock_redis_client, caplog):
        """Test audit log handles Redis errors gracefully."""
        mock_redis_client.hgetall.side_effect = Exception("Redis down")

        with caplog.at_level("WARNING"):
            await job_service.emit_job_audit_log("job-error", "test")

        assert any("Failed to emit audit log" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_pii_summary_includes_counts_and_types(self, job_service, mock_redis_client, caplog):
        """Test PII summary includes count and entity types but not actual PII."""
        import json as json_mod

        findings = [
            {"entity_type": "PERSON", "text": "John Doe", "score": 0.85},
            {"entity_type": "EMAIL", "text": "john@example.com", "score": 0.95},
            {"entity_type": "PERSON", "text": "Jane Doe", "score": 0.90},
        ]
        mock_redis_client.hgetall.return_value = {
            "job_id": "job-pii",
            "status": "awaiting_approval",
            "pii_findings": json_mod.dumps(findings),
        }

        with caplog.at_level("INFO"):
            await job_service.emit_job_audit_log("job-pii", "approval_timeout")

        audit_lines = [r for r in caplog.records if "job_audit" in r.message]
        record = json_mod.loads(audit_lines[0].message)
        assert record["pii_summary"]["count"] == 3
        assert sorted(record["pii_summary"]["entity_types"]) == ["EMAIL", "PERSON"]
        # Ensure no actual PII content is in the log
        raw_log = audit_lines[0].message
        assert "John Doe" not in raw_log
        assert "john@example.com" not in raw_log

    def test_build_audit_record_no_pii(self, job_service):
        """Test _build_audit_record when no PII findings exist."""
        record = job_service._build_audit_record(
            {"job_id": "j1", "status": "completed"},
            "retention_cleanup",
        )
        assert record["pii_summary"] is None
        assert record["reason"] == "retention_cleanup"


class TestStreamTokens:
    """Tests for stream token creation and validation."""

    @pytest.mark.asyncio
    async def test_create_stream_token_returns_valid_token(self, job_service, mock_redis_client):
        """Test stream token generation returns a valid token."""
        mock_redis_client.set.return_value = True

        token = await job_service.create_stream_token("job-123")

        # Token should be 43 characters (256-bit base64url)
        assert len(token) == 43
        assert isinstance(token, str)

    @pytest.mark.asyncio
    async def test_create_stream_token_stores_with_5min_ttl(self, job_service, mock_redis_client):
        """Test token is stored with 5-minute TTL."""
        mock_redis_client.set.return_value = True

        token = await job_service.create_stream_token("job-456")

        mock_redis_client.set.assert_called_once()
        call_args = mock_redis_client.set.call_args

        key = call_args.args[0]
        assert key.startswith("eq-pdf:stream-token:")
        assert token in key

        value = call_args.args[1]
        assert value == "job-456"

        assert call_args.kwargs.get("ex") == 300

    @pytest.mark.asyncio
    async def test_create_stream_token_generates_unique_tokens(self, job_service, mock_redis_client):
        """Test that each call generates a unique token."""
        mock_redis_client.set.return_value = True

        token1 = await job_service.create_stream_token("job-123")
        token2 = await job_service.create_stream_token("job-123")

        assert token1 != token2

    @pytest.mark.asyncio
    async def test_validate_and_consume_returns_job_id(self, job_service, mock_redis_client):
        """Test valid token returns job_id."""
        mock_redis_client.getdel.return_value = "job-789"

        job_id = await job_service.validate_and_consume_stream_token("valid-token-abc123")

        assert job_id == "job-789"
        mock_redis_client.getdel.assert_called_once_with("eq-pdf:stream-token:valid-token-abc123")

    @pytest.mark.asyncio
    async def test_validate_and_consume_deletes_token(self, job_service, mock_redis_client):
        """Test token is deleted after validation (single-use)."""
        mock_redis_client.getdel.return_value = "job-123"

        await job_service.validate_and_consume_stream_token("single-use-token")

        mock_redis_client.getdel.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_consume_returns_none_for_invalid(self, job_service, mock_redis_client):
        """Test invalid/expired token returns None."""
        mock_redis_client.getdel.return_value = None

        job_id = await job_service.validate_and_consume_stream_token("invalid-token")

        assert job_id is None

    @pytest.mark.asyncio
    async def test_validate_and_consume_returns_none_for_reused(self, job_service, mock_redis_client):
        """Test already-consumed token returns None (single-use enforcement)."""
        mock_redis_client.getdel.side_effect = ["job-123", None]

        job_id1 = await job_service.validate_and_consume_stream_token("one-time-token")
        assert job_id1 == "job-123"

        job_id2 = await job_service.validate_and_consume_stream_token("one-time-token")
        assert job_id2 is None

    @pytest.mark.asyncio
    async def test_validate_and_consume_decodes_bytes(self, job_service, mock_redis_client):
        """Test bytes response from Redis is decoded properly."""
        mock_redis_client.getdel.return_value = b"job-bytes-123"

        job_id = await job_service.validate_and_consume_stream_token("token-xyz")

        assert job_id == "job-bytes-123"
        assert isinstance(job_id, str)
