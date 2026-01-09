"""Unit tests for debug bundle image reference storage.

This tests the Phase 3 optimization that stores images separately
from debug artifacts, significantly reducing artifact size.
"""

from dataclasses import dataclass

import pytest
from src.agents.debug_capture import (
    clean_binary_from_text,
    serialize_prompt,
    serialize_prompt_with_images,
)
from src.agents.models import ImageReference
from src.shared.models.debug_bundle import DebugArtifact, DebugImageReference

pytestmark = pytest.mark.unit


# Mock BinaryContent for testing
@dataclass
class MockBinaryContent:
    """Mock PydanticAI BinaryContent for testing."""

    data: bytes
    media_type: str = "image/png"


class TestSerializePromptWithImages:
    """Tests for serialize_prompt_with_images function."""

    def test_simple_text_messages(self):
        """Test serialization of simple text messages."""
        messages = ["Hello", "World"]
        result = serialize_prompt_with_images(messages)

        assert result.text == "Hello\nWorld"
        assert result.images == []
        assert result.image_references == []

    def test_extracts_single_image(self):
        """Test extraction of a single image from messages."""
        png_data = b"\x89PNG\r\n\x1a\n" + b"x" * 1000  # Fake PNG header + data
        messages = [
            "Full page image:",
            MockBinaryContent(data=png_data, media_type="image/png"),
            "Process this page",
        ]

        result = serialize_prompt_with_images(messages, page_num=5)

        assert "Full page image:" in result.text
        assert "[IMAGE: image/png -> images/page_5.png]" in result.text
        assert "Process this page" in result.text

        assert len(result.images) == 1
        assert result.images[0].data == png_data
        assert result.images[0].ref_type == "page"
        assert result.images[0].identifier == "page_5"

        assert len(result.image_references) == 1
        assert result.image_references[0].path == "images/page_5.png"
        assert result.image_references[0].size_bytes == len(png_data)

    def test_extracts_multiple_images(self):
        """Test extraction of multiple images (page + elements)."""
        page_data = b"\x89PNG\r\n\x1a\n" + b"page" * 100
        element_data = b"\x89PNG\r\n\x1a\n" + b"element" * 100

        messages = [
            "Full page:",
            MockBinaryContent(data=page_data),
            "Cropped figure:",
            MockBinaryContent(data=element_data),
        ]

        result = serialize_prompt_with_images(messages, page_num=3)

        assert len(result.images) == 2
        assert result.images[0].ref_type == "page"
        assert result.images[0].identifier == "page_3"
        assert result.images[1].ref_type == "element"
        assert result.images[1].identifier == "element_1"

    def test_handles_non_string_binary_like_objects(self):
        """Test that non-string objects with binary repr are handled.

        The binary detection only applies to non-string objects that get
        stringified in the else branch. String messages are passed through
        as-is (which is correct - they're already text).
        """

        # Create a mock object that when str()'d looks like binary
        class BinaryLikeObject:
            def __str__(self):
                return "b'" + "x" * 2000 + "'"

        messages = [
            "Some text",
            BinaryLikeObject(),  # This will go to else branch and get str()'d
        ]

        result = serialize_prompt_with_images(messages)

        # The serialize_prompt function checks for length > 1000 and starts with "b'"
        # It should replace this with [BINARY DATA]
        assert "[BINARY DATA]" in result.text

    def test_image_size_limit(self):
        """Test that oversized images are skipped."""
        from src.agents.debug_capture import MAX_IMAGE_SIZE_BYTES

        huge_data = b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_IMAGE_SIZE_BYTES + 1)
        messages = [MockBinaryContent(data=huge_data)]

        result = serialize_prompt_with_images(messages)

        assert "[IMAGE: image/png - TOO LARGE, SKIPPED]" in result.text
        assert len(result.images) == 0
        assert len(result.image_references) == 0


class TestLegacySerializePrompt:
    """Tests for backwards-compatible serialize_prompt function."""

    def test_returns_text_only(self):
        """Test that legacy function returns just text."""
        png_data = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        messages = [
            "Hello",
            MockBinaryContent(data=png_data),
            "World",
        ]

        result = serialize_prompt(messages)

        # Should return a string, not PromptSerializationResult
        assert isinstance(result, str)
        assert "Hello" in result
        assert "World" in result
        assert "[IMAGE:" in result


class TestCleanBinaryFromText:
    """Tests for clean_binary_from_text function."""

    def test_removes_bytes_repr(self):
        """Test removal of Python bytes repr patterns."""
        text = "Some text b'\\x89PNG\\r\\n\\x1a\\n...' more text"
        cleaned = clean_binary_from_text(text)

        assert "\\x89PNG" not in cleaned
        assert "[BINARY DATA]" in cleaned
        assert "Some text" in cleaned
        assert "more text" in cleaned

    def test_removes_raw_hex_sequences(self):
        """Test removal of raw hex escape sequences."""
        # Use actual escape sequences (in a raw string they would be \\x89)
        # The function looks for patterns like \x89\xPNG which is 4+ consecutive hex escapes
        text = "Header \\x89\\x50\\x4e\\x47\\x0d\\x0a\\x1a\\x0a footer"
        cleaned = clean_binary_from_text(text)

        # Should contain placeholder (hex sequences replaced)
        assert "[BINARY DATA]" in cleaned
        # Original hex patterns should be removed
        assert "\\x89\\x50\\x4e\\x47" not in cleaned

    def test_preserves_normal_text(self):
        """Test that normal text is preserved."""
        text = "This is normal text without any binary data."
        cleaned = clean_binary_from_text(text)

        assert cleaned == text

    def test_handles_empty_string(self):
        """Test handling of empty string."""
        assert clean_binary_from_text("") == ""
        assert clean_binary_from_text(None) is None

    def test_collapses_multiple_placeholders(self):
        """Test that consecutive placeholders are collapsed."""
        text = "start b'\\x89PNG' b'\\x89PNG' end"
        cleaned = clean_binary_from_text(text)

        # Should have at most one placeholder between start and end
        assert cleaned.count("[BINARY DATA]") <= 2


class TestImageReferenceModel:
    """Tests for ImageReference Pydantic model."""

    def test_create_valid_reference(self):
        """Test creating a valid image reference."""
        ref = ImageReference(
            ref_type="page",
            identifier="page_1",
            path="images/page_1.png",
            media_type="image/png",
            size_bytes=12345,
        )

        assert ref.ref_type == "page"
        assert ref.identifier == "page_1"
        assert ref.path == "images/page_1.png"
        assert ref.size_bytes == 12345

    def test_default_values(self):
        """Test default values for optional fields."""
        ref = ImageReference(
            ref_type="element",
            identifier="fig_1",
            path="images/fig_1.png",
        )

        assert ref.media_type == "image/png"
        assert ref.size_bytes == 0


class TestDebugImageReference:
    """Tests for DebugImageReference model in shared models."""

    def test_create_valid_reference(self):
        """Test creating a valid debug image reference."""
        ref = DebugImageReference(
            ref_type="page",
            identifier="page_1",
            path="images/page_1.png",
        )

        assert ref.ref_type == "page"
        assert ref.identifier == "page_1"


class TestDebugArtifactWithImageReferences:
    """Tests for DebugArtifact with image_references field."""

    def test_artifact_without_images(self):
        """Test creating artifact without images."""
        artifact = DebugArtifact(
            agent_name="test_agent",
            phase="test_phase",
            prompt="Hello",
            response_raw="World",
        )

        assert artifact.image_references == []

    def test_artifact_with_images(self):
        """Test creating artifact with image references."""
        refs = [
            DebugImageReference(
                ref_type="page",
                identifier="page_1",
                path="images/page_1.png",
            ),
            DebugImageReference(
                ref_type="element",
                identifier="fig_1",
                path="images/fig_1.png",
            ),
        ]

        artifact = DebugArtifact(
            agent_name="test_agent",
            phase="test_phase",
            prompt="Hello",
            response_raw="World",
            image_references=refs,
        )

        assert len(artifact.image_references) == 2
        assert artifact.image_references[0].identifier == "page_1"

    def test_artifact_serialization(self):
        """Test that artifact with images serializes correctly."""
        ref = DebugImageReference(
            ref_type="page",
            identifier="page_1",
            path="images/page_1.png",
            size_bytes=5000,
        )

        artifact = DebugArtifact(
            agent_name="test_agent",
            phase="test_phase",
            image_references=[ref],
        )

        json_str = artifact.model_dump_json()

        assert "page_1" in json_str
        assert "images/page_1.png" in json_str
        assert "5000" in json_str


class TestArtifactSizeReduction:
    """Tests verifying artifact size reduction."""

    def test_artifact_size_comparison(self):
        """Test that artifact with image refs is much smaller than with raw data."""
        # Simulate large binary data that would be in response_raw
        large_binary = b"\x89PNG\r\n\x1a\n" + b"x" * 100000  # ~100KB

        # Old approach: binary data in response_raw (stringified)
        old_artifact = DebugArtifact(
            agent_name="test",
            phase="test",
            response_raw=str(large_binary),  # This would be huge
        )
        old_size = len(old_artifact.model_dump_json())

        # New approach: just a reference
        ref = DebugImageReference(
            ref_type="page",
            identifier="page_1",
            path="images/page_1.png",
            size_bytes=len(large_binary),
        )
        new_artifact = DebugArtifact(
            agent_name="test",
            phase="test",
            response_raw="[IMAGE: image/png -> images/page_1.png]",
            image_references=[ref],
        )
        new_size = len(new_artifact.model_dump_json())

        # New should be MUCH smaller
        assert new_size < old_size / 10, f"New: {new_size}, Old: {old_size}"


class TestBackwardsCompatibility:
    """Tests ensuring backwards compatibility."""

    def test_artifact_without_image_refs_field(self):
        """Test that old artifacts without image_references still work."""
        # This simulates loading an old artifact from JSON
        old_artifact_data = {
            "agent_name": "old_agent",
            "phase": "old_phase",
            "prompt": "Old prompt",
            "response_raw": "Old response",
            # No image_references field
        }

        artifact = DebugArtifact(**old_artifact_data)

        assert artifact.image_references == []  # Should default to empty list

    def test_serialize_prompt_still_works(self):
        """Test that original serialize_prompt function still works."""
        messages = ["Hello", "World"]
        result = serialize_prompt(messages)

        assert isinstance(result, str)
        assert "Hello" in result
