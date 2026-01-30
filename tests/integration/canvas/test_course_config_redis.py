"""Integration tests for CourseConfigService with real Redis.

Verifies Redis hash serialization round-trips, enabled-courses set
management, processed-file JSON storage, and publish-result TTL handling.
"""

import pytest
from src.canvas.course_config import PUBLISH_RESULT_TTL


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_set_and_get_config_round_trip(course_config_service, sample_course_config):
    """Write a config, read it back, verify all fields survive serialization."""
    config = sample_course_config(enabled=True, threshold=0.75, token="tok-abc")
    await course_config_service.set_config("course-100", config)

    result = await course_config_service.get_config("course-100")

    assert result is not None
    assert result.enabled is True
    assert result.auto_publish_threshold == 0.75
    assert result.canvas_api_token == "tok-abc"
    assert result.created_at == config.created_at
    assert result.updated_at == config.updated_at


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_get_config_returns_none_for_unconfigured(course_config_service):
    """Missing key returns None, not an empty config."""
    result = await course_config_service.get_config("course-nonexistent")
    assert result is None


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_enabled_set_membership(course_config_service, sample_course_config):
    """Enable a course → in set; disable → removed from set."""
    config_on = sample_course_config(enabled=True)
    await course_config_service.set_config("course-200", config_on)

    enabled = await course_config_service.list_enabled_courses()
    assert "course-200" in enabled

    config_off = sample_course_config(enabled=False)
    await course_config_service.set_config("course-200", config_off)

    enabled = await course_config_service.list_enabled_courses()
    assert "course-200" not in enabled


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_list_enabled_courses_multiple(course_config_service, sample_course_config):
    """Enable 3 courses, list returns all 3."""
    for cid in ["c-1", "c-2", "c-3"]:
        await course_config_service.set_config(cid, sample_course_config(enabled=True))

    enabled = await course_config_service.list_enabled_courses()
    assert set(enabled) == {"c-1", "c-2", "c-3"}


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_set_processed_file_and_get_round_trip(course_config_service, sample_processed_file):
    """JSON serialization round-trip in Redis hash."""
    pf = sample_processed_file(canvas_file_id="99", job_id="j-1", status="completed")
    await course_config_service.set_processed_file("course-300", "99", pf)

    result = await course_config_service.get_processed_file("course-300", "99")

    assert result is not None
    assert result.canvas_file_id == "99"
    assert result.job_id == "j-1"
    assert result.status == "completed"
    assert result.original_filename == pf.original_filename
    assert result.canvas_updated_at == pf.canvas_updated_at


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_list_processed_files_multiple(course_config_service, sample_processed_file):
    """Store 3 files, list returns all 3."""
    for fid in ["10", "20", "30"]:
        pf = sample_processed_file(canvas_file_id=fid, job_id=f"job-{fid}")
        await course_config_service.set_processed_file("course-400", fid, pf)

    files = await course_config_service.list_processed_files("course-400")
    assert set(files.keys()) == {"10", "20", "30"}
    assert files["10"].job_id == "job-10"
    assert files["20"].job_id == "job-20"
    assert files["30"].job_id == "job-30"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_is_file_new_returns_true_for_new(course_config_service):
    """File not in Redis → True."""
    result = await course_config_service.is_file_new_or_updated(
        "course-500", "999", "2025-06-01T00:00:00+00:00"
    )
    assert result is True


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_is_file_new_returns_false_for_same_timestamp(
    course_config_service, sample_processed_file
):
    """Same timestamp + completed → False."""
    ts = "2025-01-15T10:00:00+00:00"
    pf = sample_processed_file(canvas_file_id="50", canvas_updated_at=ts, status="completed")
    await course_config_service.set_processed_file("course-600", "50", pf)

    result = await course_config_service.is_file_new_or_updated("course-600", "50", ts)
    assert result is False


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_is_file_new_returns_true_for_newer_timestamp(
    course_config_service, sample_processed_file
):
    """Newer timestamp → True (file was updated in Canvas)."""
    old_ts = "2025-01-15T10:00:00+00:00"
    new_ts = "2025-02-01T10:00:00+00:00"
    pf = sample_processed_file(canvas_file_id="51", canvas_updated_at=old_ts, status="completed")
    await course_config_service.set_processed_file("course-700", "51", pf)

    result = await course_config_service.is_file_new_or_updated("course-700", "51", new_ts)
    assert result is True


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_is_file_new_returns_true_for_failed_status(
    course_config_service, sample_processed_file
):
    """Same timestamp + failed → True (should retry)."""
    ts = "2025-01-15T10:00:00+00:00"
    pf = sample_processed_file(canvas_file_id="52", canvas_updated_at=ts, status="failed")
    await course_config_service.set_processed_file("course-800", "52", pf)

    result = await course_config_service.is_file_new_or_updated("course-800", "52", ts)
    assert result is True


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_result_round_trip(course_config_service):
    """Write and read publish result via Redis hash."""
    result_data = {
        "job_id": "j-publish-1",
        "canvas_page_url": "test-page",
        "canvas_page_id": "42",
        "published": "true",
    }
    await course_config_service.set_publish_result("course-900", "file-1", result_data)

    result = await course_config_service.get_publish_result("course-900", "file-1")
    assert result is not None
    assert result["job_id"] == "j-publish-1"
    assert result["canvas_page_url"] == "test-page"
    assert result["canvas_page_id"] == "42"
    assert result["published"] == "true"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_result_ttl(course_config_service, real_redis_client):
    """Verify TTL is set on the publish result key."""
    await course_config_service.set_publish_result("course-1000", "file-2", {"job_id": "j-ttl"})

    key = "eq-pdf:published:course-1000:file-2"
    ttl = await real_redis_client.ttl(key)
    # TTL should be close to PUBLISH_RESULT_TTL (30 days = 2592000s)
    # Allow some tolerance for execution time
    assert ttl > PUBLISH_RESULT_TTL - 10
    assert ttl <= PUBLISH_RESULT_TTL


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_config_update_preserves_other_fields(course_config_service, sample_course_config):
    """Partial update doesn't clobber unrelated fields."""
    original = sample_course_config(enabled=True, threshold=0.9, token="secret-token")
    await course_config_service.set_config("course-1100", original)

    # Read, modify enabled only, write back
    config = await course_config_service.get_config("course-1100")
    assert config is not None
    config.enabled = False
    await course_config_service.set_config("course-1100", config)

    updated = await course_config_service.get_config("course-1100")
    assert updated is not None
    assert updated.enabled is False
    assert updated.auto_publish_threshold == 0.9
    assert updated.canvas_api_token == "secret-token"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_multi_course_isolation(course_config_service, sample_processed_file):
    """Two courses don't interfere with each other's processed files."""
    pf_a = sample_processed_file(canvas_file_id="1", job_id="job-a")
    pf_b = sample_processed_file(canvas_file_id="1", job_id="job-b")

    await course_config_service.set_processed_file("course-A", "1", pf_a)
    await course_config_service.set_processed_file("course-B", "1", pf_b)

    result_a = await course_config_service.get_processed_file("course-A", "1")
    result_b = await course_config_service.get_processed_file("course-B", "1")

    assert result_a is not None
    assert result_b is not None
    assert result_a.job_id == "job-a"
    assert result_b.job_id == "job-b"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_overwrite_processed_file(course_config_service, sample_processed_file):
    """Second write replaces first."""
    pf1 = sample_processed_file(canvas_file_id="77", job_id="old-job", status="processing")
    await course_config_service.set_processed_file("course-1200", "77", pf1)

    pf2 = sample_processed_file(canvas_file_id="77", job_id="new-job", status="completed")
    await course_config_service.set_processed_file("course-1200", "77", pf2)

    result = await course_config_service.get_processed_file("course-1200", "77")
    assert result is not None
    assert result.job_id == "new-job"
    assert result.status == "completed"
