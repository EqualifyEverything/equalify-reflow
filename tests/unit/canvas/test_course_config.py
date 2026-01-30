"""Unit tests for CourseConfigService."""

from unittest.mock import AsyncMock

import pytest
from src.canvas.course_config import (
    ENABLED_COURSES_KEY,
    PUBLISH_RESULT_TTL,
    CourseConfig,
    CourseConfigService,
    ProcessedFile,
)

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_redis():
    """Mock Redis client for course config tests."""
    redis = AsyncMock()
    redis.hgetall.return_value = {}
    redis.hget.return_value = None
    redis.hset.return_value = 1
    redis.sadd.return_value = 1
    redis.srem.return_value = 1
    redis.smembers.return_value = set()
    redis.expire.return_value = True
    return redis


@pytest.fixture
def service(mock_redis):
    """Create CourseConfigService with mock Redis."""
    return CourseConfigService(mock_redis)


@pytest.fixture
def sample_config():
    """Sample course configuration."""
    return CourseConfig(
        enabled=True,
        auto_publish_threshold=0.85,
        canvas_api_token="test-token-123",
        created_at="2024-01-15T10:30:00Z",
        updated_at="2024-01-15T10:30:00Z",
    )


@pytest.fixture
def sample_processed_file():
    """Sample processed file record."""
    return ProcessedFile(
        canvas_file_id="12345",
        canvas_updated_at="2024-01-15T10:30:00Z",
        job_id="job-abc-123",
        status="completed",
        processed_at="2024-01-15T10:35:00Z",
        original_filename="lecture_notes.pdf",
    )


# --- CourseConfig Model ---


class TestCourseConfigModel:
    def test_defaults(self):
        config = CourseConfig()
        assert config.enabled is False
        assert config.auto_publish_threshold == 1.0
        assert config.canvas_api_token == ""
        assert config.created_at == ""
        assert config.updated_at == ""

    def test_custom_values(self, sample_config):
        assert sample_config.enabled is True
        assert sample_config.auto_publish_threshold == 0.85
        assert sample_config.canvas_api_token == "test-token-123"

    def test_threshold_validation_min(self):
        with pytest.raises(Exception):
            CourseConfig(auto_publish_threshold=-0.1)

    def test_threshold_validation_max(self):
        with pytest.raises(Exception):
            CourseConfig(auto_publish_threshold=1.1)

    def test_threshold_boundary_zero(self):
        config = CourseConfig(auto_publish_threshold=0.0)
        assert config.auto_publish_threshold == 0.0

    def test_threshold_boundary_one(self):
        config = CourseConfig(auto_publish_threshold=1.0)
        assert config.auto_publish_threshold == 1.0


# --- ProcessedFile Model ---


class TestProcessedFileModel:
    def test_serialization(self, sample_processed_file):
        data = sample_processed_file.model_dump()
        assert data["canvas_file_id"] == "12345"
        assert data["status"] == "completed"
        assert data["original_filename"] == "lecture_notes.pdf"

    def test_json_round_trip(self, sample_processed_file):
        json_str = sample_processed_file.model_dump_json()
        restored = ProcessedFile.model_validate_json(json_str)
        assert restored == sample_processed_file


# --- get_config ---


class TestGetConfig:
    async def test_returns_none_when_not_configured(self, service, mock_redis):
        mock_redis.hgetall.return_value = {}
        result = await service.get_config("course-999")
        assert result is None
        mock_redis.hgetall.assert_called_once_with("eq-pdf:course-config:course-999")

    async def test_returns_none_for_different_unconfigured_courses(self, service, mock_redis):
        mock_redis.hgetall.return_value = {}
        assert await service.get_config("course-100") is None
        assert await service.get_config("course-200") is None
        assert await service.get_config("course-300") is None

    async def test_none_result_does_not_affect_configured_course(self, service, mock_redis):
        """An unconfigured course returns None without affecting other courses."""
        mock_redis.hgetall.return_value = {}
        assert await service.get_config("unconfigured-course") is None

        mock_redis.hgetall.return_value = {
            "enabled": "true",
            "auto_publish_threshold": "0.9",
            "canvas_api_token": "tok-xyz",
            "created_at": "2024-02-01T00:00:00Z",
            "updated_at": "2024-02-01T00:00:00Z",
        }
        result = await service.get_config("configured-course")
        assert result is not None
        assert result.enabled is True

    async def test_returns_config_from_redis_hash(self, service, mock_redis):
        mock_redis.hgetall.return_value = {
            "enabled": "true",
            "auto_publish_threshold": "0.85",
            "canvas_api_token": "tok-abc",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:30:00Z",
        }
        result = await service.get_config("course-101")
        assert result is not None
        assert result.enabled is True
        assert result.auto_publish_threshold == 0.85
        assert result.canvas_api_token == "tok-abc"
        assert result.created_at == "2024-01-15T10:30:00Z"

    async def test_returns_config_disabled(self, service, mock_redis):
        mock_redis.hgetall.return_value = {
            "enabled": "false",
            "auto_publish_threshold": "1.0",
            "canvas_api_token": "",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:30:00Z",
        }
        result = await service.get_config("course-102")
        assert result is not None
        assert result.enabled is False

    async def test_handles_bytes_from_redis(self, service, mock_redis):
        mock_redis.hgetall.return_value = {
            b"enabled": b"true",
            b"auto_publish_threshold": b"0.5",
            b"canvas_api_token": b"token",
            b"created_at": b"2024-01-15T10:30:00Z",
            b"updated_at": b"2024-01-15T10:30:00Z",
        }
        result = await service.get_config("course-103")
        assert result is not None
        assert result.enabled is True
        assert result.auto_publish_threshold == 0.5


# --- set_config ---


class TestSetConfig:
    async def test_writes_config_to_redis_hash(self, service, mock_redis, sample_config):
        await service.set_config("course-101", sample_config)
        mock_redis.hset.assert_called_once_with(
            "eq-pdf:course-config:course-101",
            mapping={
                "enabled": "true",
                "auto_publish_threshold": "0.85",
                "canvas_api_token": "test-token-123",
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-15T10:30:00Z",
            },
        )

    async def test_adds_to_enabled_set_when_enabled(self, service, mock_redis, sample_config):
        await service.set_config("course-101", sample_config)
        mock_redis.sadd.assert_called_once_with(ENABLED_COURSES_KEY, "course-101")
        mock_redis.srem.assert_not_called()

    async def test_removes_from_enabled_set_when_disabled(self, service, mock_redis):
        config = CourseConfig(enabled=False)
        await service.set_config("course-101", config)
        mock_redis.srem.assert_called_once_with(ENABLED_COURSES_KEY, "course-101")
        mock_redis.sadd.assert_not_called()

    async def test_writes_default_config_values(self, service, mock_redis):
        """Default CourseConfig serializes correctly to Redis hash."""
        config = CourseConfig()
        await service.set_config("course-200", config)
        mock_redis.hset.assert_called_once_with(
            "eq-pdf:course-config:course-200",
            mapping={
                "enabled": "false",
                "auto_publish_threshold": "1.0",
                "canvas_api_token": "",
                "created_at": "",
                "updated_at": "",
            },
        )

    async def test_writes_threshold_zero(self, service, mock_redis):
        """Boundary: threshold=0.0 (auto-publish everything) serializes correctly."""
        config = CourseConfig(enabled=True, auto_publish_threshold=0.0)
        await service.set_config("course-300", config)
        call_kwargs = mock_redis.hset.call_args
        assert call_kwargs[1]["mapping"]["auto_publish_threshold"] == "0.0"

    async def test_overwrites_existing_config(self, service, mock_redis):
        """Calling set_config twice overwrites the previous value."""
        config_v1 = CourseConfig(
            enabled=True,
            auto_publish_threshold=0.9,
            canvas_api_token="token-v1",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        config_v2 = CourseConfig(
            enabled=True,
            auto_publish_threshold=0.5,
            canvas_api_token="token-v2",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-02-01T00:00:00Z",
        )
        await service.set_config("course-101", config_v1)
        await service.set_config("course-101", config_v2)

        # Second call should write the updated values
        last_call = mock_redis.hset.call_args_list[-1]
        assert last_call[1]["mapping"]["auto_publish_threshold"] == "0.5"
        assert last_call[1]["mapping"]["canvas_api_token"] == "token-v2"
        assert last_call[1]["mapping"]["updated_at"] == "2024-02-01T00:00:00Z"

    async def test_disable_after_enable_updates_set(self, service, mock_redis):
        """Toggling enabled from True to False updates the enabled-courses set."""
        enabled_config = CourseConfig(enabled=True)
        disabled_config = CourseConfig(enabled=False)

        await service.set_config("course-101", enabled_config)
        mock_redis.sadd.assert_called_with(ENABLED_COURSES_KEY, "course-101")

        await service.set_config("course-101", disabled_config)
        mock_redis.srem.assert_called_with(ENABLED_COURSES_KEY, "course-101")


# --- list_enabled_courses ---


class TestListEnabledCourses:
    async def test_returns_empty_list_when_none_enabled(self, service, mock_redis):
        mock_redis.smembers.return_value = set()
        result = await service.list_enabled_courses()
        assert result == []
        mock_redis.smembers.assert_called_once_with(ENABLED_COURSES_KEY)

    async def test_returns_enabled_course_ids(self, service, mock_redis):
        mock_redis.smembers.return_value = {"course-101", "course-202"}
        result = await service.list_enabled_courses()
        assert sorted(result) == ["course-101", "course-202"]

    async def test_handles_bytes_members(self, service, mock_redis):
        mock_redis.smembers.return_value = {b"course-101", b"course-202"}
        result = await service.list_enabled_courses()
        assert sorted(result) == ["course-101", "course-202"]


# --- get_processed_file ---


class TestGetProcessedFile:
    async def test_returns_none_when_not_found(self, service, mock_redis):
        mock_redis.hget.return_value = None
        result = await service.get_processed_file("course-101", "file-1")
        assert result is None
        mock_redis.hget.assert_called_once_with("eq-pdf:processed:course-101", "file-1")

    async def test_returns_processed_file(self, service, mock_redis, sample_processed_file):
        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        result = await service.get_processed_file("course-101", "12345")
        assert result is not None
        assert result.canvas_file_id == "12345"
        assert result.job_id == "job-abc-123"
        assert result.status == "completed"

    async def test_handles_bytes_value(self, service, mock_redis, sample_processed_file):
        mock_redis.hget.return_value = sample_processed_file.model_dump_json().encode("utf-8")
        result = await service.get_processed_file("course-101", "12345")
        assert result is not None
        assert result.canvas_file_id == "12345"

    async def test_returns_all_fields(self, service, mock_redis, sample_processed_file):
        """All ProcessedFile fields are correctly deserialized from Redis."""
        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        result = await service.get_processed_file("course-101", "12345")
        assert result is not None
        assert result.canvas_file_id == "12345"
        assert result.canvas_updated_at == "2024-01-15T10:30:00Z"
        assert result.job_id == "job-abc-123"
        assert result.status == "completed"
        assert result.processed_at == "2024-01-15T10:35:00Z"
        assert result.original_filename == "lecture_notes.pdf"

    async def test_returns_processing_status_file(self, service, mock_redis):
        """Files with 'processing' status are correctly returned."""
        record = ProcessedFile(
            canvas_file_id="99999",
            canvas_updated_at="2024-02-01T08:00:00Z",
            job_id="job-in-progress",
            status="processing",
            processed_at="2024-02-01T08:01:00Z",
            original_filename="syllabus.pdf",
        )
        mock_redis.hget.return_value = record.model_dump_json()
        result = await service.get_processed_file("course-200", "99999")
        assert result is not None
        assert result.status == "processing"
        assert result.original_filename == "syllabus.pdf"

    async def test_returns_failed_status_file(self, service, mock_redis):
        """Files with 'failed' status are correctly returned."""
        record = ProcessedFile(
            canvas_file_id="88888",
            canvas_updated_at="2024-03-01T12:00:00Z",
            job_id="job-failed-xyz",
            status="failed",
            processed_at="2024-03-01T12:05:00Z",
            original_filename="broken.pdf",
        )
        mock_redis.hget.return_value = record.model_dump_json()
        result = await service.get_processed_file("course-300", "88888")
        assert result is not None
        assert result.status == "failed"
        assert result.job_id == "job-failed-xyz"

    async def test_uses_correct_redis_key(self, service, mock_redis):
        """Verify Redis key includes both course_id and file_id correctly."""
        mock_redis.hget.return_value = None
        await service.get_processed_file("course-abc", "file-xyz")
        mock_redis.hget.assert_called_once_with("eq-pdf:processed:course-abc", "file-xyz")

    async def test_none_for_missing_file_does_not_affect_existing(self, service, mock_redis, sample_processed_file):
        """Missing file returns None without affecting lookups for existing files."""
        mock_redis.hget.return_value = None
        assert await service.get_processed_file("course-101", "nonexistent") is None

        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        result = await service.get_processed_file("course-101", "12345")
        assert result is not None
        assert result.canvas_file_id == "12345"

    async def test_different_courses_use_different_keys(self, service, mock_redis):
        """Files in different courses are stored under different Redis keys."""
        mock_redis.hget.return_value = None
        await service.get_processed_file("course-100", "file-1")
        await service.get_processed_file("course-200", "file-1")
        calls = mock_redis.hget.call_args_list
        assert calls[0].args == ("eq-pdf:processed:course-100", "file-1")
        assert calls[1].args == ("eq-pdf:processed:course-200", "file-1")


# --- set_processed_file ---


class TestSetProcessedFile:
    async def test_stores_as_json_in_hash(self, service, mock_redis, sample_processed_file):
        await service.set_processed_file("course-101", "12345", sample_processed_file)
        mock_redis.hset.assert_called_once_with(
            "eq-pdf:processed:course-101",
            "12345",
            sample_processed_file.model_dump_json(),
        )

    async def test_json_contains_all_fields(self, service, mock_redis, sample_processed_file):
        """Stored JSON contains all ProcessedFile fields."""
        await service.set_processed_file("course-101", "12345", sample_processed_file)
        stored_json = mock_redis.hset.call_args.args[2]
        restored = ProcessedFile.model_validate_json(stored_json)
        assert restored == sample_processed_file

    async def test_uses_correct_redis_key(self, service, mock_redis, sample_processed_file):
        """Redis key pattern is eq-pdf:processed:{course_id}."""
        await service.set_processed_file("course-xyz", "file-abc", sample_processed_file)
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args.args
        assert call_args[0] == "eq-pdf:processed:course-xyz"
        assert call_args[1] == "file-abc"

    async def test_overwrites_existing_record(self, service, mock_redis):
        """Calling set_processed_file twice overwrites the previous value."""
        record_v1 = ProcessedFile(
            canvas_file_id="12345",
            canvas_updated_at="2024-01-15T10:30:00Z",
            job_id="job-v1",
            status="processing",
            processed_at="2024-01-15T10:35:00Z",
            original_filename="lecture.pdf",
        )
        record_v2 = ProcessedFile(
            canvas_file_id="12345",
            canvas_updated_at="2024-01-15T10:30:00Z",
            job_id="job-v2",
            status="completed",
            processed_at="2024-01-15T10:40:00Z",
            original_filename="lecture.pdf",
        )
        await service.set_processed_file("course-101", "12345", record_v1)
        await service.set_processed_file("course-101", "12345", record_v2)

        last_call = mock_redis.hset.call_args_list[-1]
        stored_json = last_call.args[2]
        restored = ProcessedFile.model_validate_json(stored_json)
        assert restored.job_id == "job-v2"
        assert restored.status == "completed"


# --- list_processed_files ---


class TestListProcessedFiles:
    async def test_returns_empty_dict_when_none(self, service, mock_redis):
        mock_redis.hgetall.return_value = {}
        result = await service.list_processed_files("course-101")
        assert result == {}

    async def test_returns_all_processed_files(self, service, mock_redis, sample_processed_file):
        mock_redis.hgetall.return_value = {
            "12345": sample_processed_file.model_dump_json(),
            "67890": ProcessedFile(
                canvas_file_id="67890",
                canvas_updated_at="2024-01-16T10:30:00Z",
                job_id="job-def-456",
                status="processing",
                processed_at="2024-01-16T10:35:00Z",
                original_filename="syllabus.pdf",
            ).model_dump_json(),
        }
        result = await service.list_processed_files("course-101")
        assert len(result) == 2
        assert result["12345"].status == "completed"
        assert result["67890"].status == "processing"

    async def test_handles_bytes_keys_and_values(self, service, mock_redis, sample_processed_file):
        mock_redis.hgetall.return_value = {
            b"12345": sample_processed_file.model_dump_json().encode("utf-8"),
        }
        result = await service.list_processed_files("course-101")
        assert "12345" in result
        assert result["12345"].canvas_file_id == "12345"

    async def test_uses_correct_redis_key(self, service, mock_redis):
        """Redis key pattern is eq-pdf:processed:{course_id}."""
        mock_redis.hgetall.return_value = {}
        await service.list_processed_files("course-xyz")
        mock_redis.hgetall.assert_called_with("eq-pdf:processed:course-xyz")

    async def test_single_file_returns_dict_with_one_entry(self, service, mock_redis, sample_processed_file):
        """A course with exactly one processed file returns a single-entry dict."""
        mock_redis.hgetall.return_value = {
            "12345": sample_processed_file.model_dump_json(),
        }
        result = await service.list_processed_files("course-101")
        assert len(result) == 1
        assert "12345" in result
        assert result["12345"].original_filename == "lecture_notes.pdf"

    async def test_mixed_statuses(self, service, mock_redis):
        """Files with different statuses are all returned correctly."""
        mock_redis.hgetall.return_value = {
            "f1": ProcessedFile(
                canvas_file_id="f1",
                canvas_updated_at="2024-01-01T00:00:00Z",
                job_id="j1",
                status="completed",
                processed_at="2024-01-01T00:05:00Z",
                original_filename="a.pdf",
            ).model_dump_json(),
            "f2": ProcessedFile(
                canvas_file_id="f2",
                canvas_updated_at="2024-01-02T00:00:00Z",
                job_id="j2",
                status="processing",
                processed_at="2024-01-02T00:05:00Z",
                original_filename="b.pdf",
            ).model_dump_json(),
            "f3": ProcessedFile(
                canvas_file_id="f3",
                canvas_updated_at="2024-01-03T00:00:00Z",
                job_id="j3",
                status="failed",
                processed_at="2024-01-03T00:05:00Z",
                original_filename="c.pdf",
            ).model_dump_json(),
        }
        result = await service.list_processed_files("course-101")
        assert len(result) == 3
        assert result["f1"].status == "completed"
        assert result["f2"].status == "processing"
        assert result["f3"].status == "failed"


# --- is_file_new_or_updated ---


class TestIsFileNewOrUpdated:
    async def test_returns_true_for_new_file(self, service, mock_redis):
        mock_redis.hget.return_value = None
        result = await service.is_file_new_or_updated("course-101", "file-new", "2024-01-15T10:30:00Z")
        assert result is True

    async def test_returns_true_when_canvas_updated_at_is_newer(self, service, mock_redis, sample_processed_file):
        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        # sample has canvas_updated_at = "2024-01-15T10:30:00Z"
        result = await service.is_file_new_or_updated("course-101", "12345", "2024-01-16T10:30:00Z")
        assert result is True

    async def test_returns_false_when_canvas_updated_at_matches(self, service, mock_redis, sample_processed_file):
        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        result = await service.is_file_new_or_updated("course-101", "12345", "2024-01-15T10:30:00Z")
        assert result is False

    async def test_returns_false_when_canvas_updated_at_is_older(self, service, mock_redis, sample_processed_file):
        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        result = await service.is_file_new_or_updated("course-101", "12345", "2024-01-14T10:30:00Z")
        assert result is False

    async def test_iso8601_string_comparison_same_day_different_time(self, service, mock_redis):
        """ISO 8601 string comparison correctly differentiates same-day timestamps."""
        record = ProcessedFile(
            canvas_file_id="f1",
            canvas_updated_at="2024-06-15T08:00:00Z",
            job_id="j1",
            status="completed",
            processed_at="2024-06-15T08:05:00Z",
            original_filename="test.pdf",
        )
        mock_redis.hget.return_value = record.model_dump_json()
        # Same day, later time -> should be updated
        assert await service.is_file_new_or_updated("c1", "f1", "2024-06-15T09:00:00Z") is True
        # Same day, earlier time -> not updated
        assert await service.is_file_new_or_updated("c1", "f1", "2024-06-15T07:00:00Z") is False

    async def test_delegates_to_get_processed_file(self, service, mock_redis):
        """is_file_new_or_updated reads from the correct Redis key."""
        mock_redis.hget.return_value = None
        await service.is_file_new_or_updated("course-500", "file-abc", "2024-01-01T00:00:00Z")
        mock_redis.hget.assert_called_with("eq-pdf:processed:course-500", "file-abc")

    async def test_returns_true_for_multiple_unprocessed_files(self, service, mock_redis):
        """Each unprocessed file independently returns True."""
        mock_redis.hget.return_value = None
        assert await service.is_file_new_or_updated("c1", "f1", "2024-01-01T00:00:00Z") is True
        assert await service.is_file_new_or_updated("c1", "f2", "2024-02-01T00:00:00Z") is True
        assert await service.is_file_new_or_updated("c1", "f3", "2024-03-01T00:00:00Z") is True

    async def test_returns_true_for_unprocessed_file_in_course_with_other_processed(
        self, service, mock_redis, sample_processed_file
    ):
        """An unprocessed file returns True even when the same course has other processed files."""
        # First call: file exists in processed set
        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        assert await service.is_file_new_or_updated("c1", "12345", "2024-01-15T10:30:00Z") is False

        # Second call: different file_id not in processed set
        mock_redis.hget.return_value = None
        assert await service.is_file_new_or_updated("c1", "new-file", "2024-01-15T10:30:00Z") is True

    async def test_returns_true_for_file_not_in_different_course(self, service, mock_redis, sample_processed_file):
        """A file processed in one course is not considered processed in another."""
        # File exists in course-101
        mock_redis.hget.return_value = sample_processed_file.model_dump_json()
        assert await service.is_file_new_or_updated("course-101", "12345", "2024-01-15T10:30:00Z") is False

        # Same file_id not processed in course-202
        mock_redis.hget.return_value = None
        assert await service.is_file_new_or_updated("course-202", "12345", "2024-01-15T10:30:00Z") is True

        # Verify correct keys were used for each course
        calls = mock_redis.hget.call_args_list
        assert calls[-2].args == ("eq-pdf:processed:course-101", "12345")
        assert calls[-1].args == ("eq-pdf:processed:course-202", "12345")

    async def test_returns_true_for_new_file_regardless_of_timestamp(self, service, mock_redis):
        """Unprocessed files return True regardless of how old the timestamp is."""
        mock_redis.hget.return_value = None
        # Very old timestamp
        assert await service.is_file_new_or_updated("c1", "f1", "2020-01-01T00:00:00Z") is True
        # Very recent timestamp
        assert await service.is_file_new_or_updated("c1", "f2", "2025-12-31T23:59:59Z") is True


# --- get_publish_result ---


class TestGetPublishResult:
    async def test_returns_none_when_not_published(self, service, mock_redis):
        mock_redis.hgetall.return_value = {}
        result = await service.get_publish_result("course-101", "file-1")
        assert result is None
        mock_redis.hgetall.assert_called_once_with("eq-pdf:published:course-101:file-1")

    async def test_returns_publish_result(self, service, mock_redis):
        mock_redis.hgetall.return_value = {
            "page_id": "42",
            "page_url": "lecture-notes-reflow",
            "published_at": "2024-01-15T10:40:00Z",
        }
        result = await service.get_publish_result("course-101", "file-1")
        assert result is not None
        assert result["page_id"] == "42"
        assert result["page_url"] == "lecture-notes-reflow"

    async def test_handles_bytes_from_redis(self, service, mock_redis):
        mock_redis.hgetall.return_value = {
            b"page_id": b"42",
            b"published_at": b"2024-01-15T10:40:00Z",
        }
        result = await service.get_publish_result("course-101", "file-1")
        assert result is not None
        assert result["page_id"] == "42"

    async def test_uses_correct_redis_key(self, service, mock_redis):
        """Redis key includes both course_id and file_id."""
        mock_redis.hgetall.return_value = {}
        await service.get_publish_result("course-abc", "file-xyz")
        mock_redis.hgetall.assert_called_with("eq-pdf:published:course-abc:file-xyz")


# --- set_publish_result ---


class TestSetPublishResult:
    async def test_stores_result_with_ttl(self, service, mock_redis):
        result = {
            "page_id": 42,
            "page_url": "lecture-notes-reflow",
            "published_at": "2024-01-15T10:40:00Z",
        }
        await service.set_publish_result("course-101", "file-1", result)

        mock_redis.hset.assert_called_once_with(
            "eq-pdf:published:course-101:file-1",
            mapping={
                "page_id": "42",
                "page_url": "lecture-notes-reflow",
                "published_at": "2024-01-15T10:40:00Z",
            },
        )
        mock_redis.expire.assert_called_once_with(
            "eq-pdf:published:course-101:file-1",
            PUBLISH_RESULT_TTL,
        )

    async def test_ttl_is_30_days(self):
        assert PUBLISH_RESULT_TTL == 30 * 24 * 3600

    async def test_uses_correct_redis_key(self, service, mock_redis):
        """Redis key includes both course_id and file_id."""
        await service.set_publish_result("course-abc", "file-xyz", {"page_id": "1"})
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        assert call_args.args[0] == "eq-pdf:published:course-abc:file-xyz"
        mock_redis.expire.assert_called_once_with(
            "eq-pdf:published:course-abc:file-xyz",
            PUBLISH_RESULT_TTL,
        )

    async def test_converts_all_values_to_strings(self, service, mock_redis):
        """Non-string values (int, bool) are converted to strings for Redis."""
        result = {
            "page_id": 42,
            "figure_count": 5,
            "published": True,
        }
        await service.set_publish_result("course-101", "file-1", result)
        call_mapping = mock_redis.hset.call_args.kwargs["mapping"]
        assert call_mapping["page_id"] == "42"
        assert call_mapping["figure_count"] == "5"
        assert call_mapping["published"] == "True"
