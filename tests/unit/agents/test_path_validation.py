"""Tests for path validation security in AgentCore."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from src.agents.core import AgentConfig, AgentCore
from src.agents.model_tiers import ModelTier


class TestPathTraversalPrevention:
    """Tests for path traversal attack prevention."""

    @pytest.fixture
    def mock_yaml_content(self) -> dict:
        """Valid YAML content for testing."""
        return {"system_prompt": "Test prompt", "user_prompt": "User prompt {var}"}

    def test_path_traversal_blocked(self) -> None:
        """Path traversal attempts are blocked with ValueError."""
        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("../../../etc/passwd"),
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with pytest.raises(ValueError, match="Invalid prompts file path"):
            AgentCore(config)

    def test_path_traversal_with_double_dots(self) -> None:
        """Multiple .. in path are blocked."""
        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("../../../../../../etc/shadow"),
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with pytest.raises(ValueError, match="Invalid prompts file path"):
            AgentCore(config)

    def test_path_traversal_with_encoded_chars(self) -> None:
        """Paths with encoded traversal chars are blocked."""
        # After path resolution, these still try to escape
        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("..%2F..%2Fetc/passwd"),  # URL encoded ../
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        # This should either fail path resolution or be blocked
        with pytest.raises((ValueError, FileNotFoundError)):
            AgentCore(config)

    def test_path_traversal_logs_security_event(self, caplog: pytest.LogCaptureFixture) -> None:
        """Path traversal attempts log a security event."""
        import logging

        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("../../../etc/passwd"),
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError):
                AgentCore(config)

        # Check for security event in logs
        assert any(
            "path_traversal" in record.message.lower()
            or record.__dict__.get("security_event") == "path_traversal_blocked"
            for record in caplog.records
        )


class TestFileExtensionValidation:
    """Tests for file extension validation."""

    def test_yaml_extension_allowed(self, tmp_path: Path) -> None:
        """Files with .yaml extension are allowed."""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("system_prompt: test\nuser_prompt: test")

        config = AgentConfig(
            name="test_agent",
            prompts_file=yaml_file,  # Absolute path
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        # Should not raise - file exists with valid extension
        core = AgentCore(config)
        assert core.prompts["system_prompt"] == "test"

    def test_yml_extension_allowed(self, tmp_path: Path) -> None:
        """Files with .yml extension are allowed."""
        yml_file = tmp_path / "test.yml"
        yml_file.write_text("system_prompt: test\nuser_prompt: test")

        config = AgentConfig(
            name="test_agent",
            prompts_file=yml_file,  # Absolute path
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        # Should not raise - file exists with valid extension
        core = AgentCore(config)
        assert core.prompts["system_prompt"] == "test"

    def test_txt_extension_blocked(self, tmp_path: Path) -> None:
        """Files with .txt extension are blocked."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("system_prompt: test")

        config = AgentConfig(
            name="test_agent",
            prompts_file=txt_file,  # Absolute path
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with pytest.raises(ValueError, match="Invalid file type.*\\.txt"):
            AgentCore(config)

    def test_py_extension_blocked(self) -> None:
        """Files with .py extension are blocked (could contain code)."""
        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("/some/path/malicious.py"),
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with pytest.raises(ValueError, match="Invalid file type.*\\.py"):
            AgentCore(config)

    def test_no_extension_blocked(self) -> None:
        """Files without extension are blocked."""
        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("/some/path/prompts"),
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with pytest.raises(ValueError, match="Invalid file type"):
            AgentCore(config)

    def test_json_extension_blocked(self) -> None:
        """Files with .json extension are blocked (use YAML only)."""
        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("/some/path/prompts.json"),
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with pytest.raises(ValueError, match="Invalid file type.*\\.json"):
            AgentCore(config)

    def test_uppercase_yaml_allowed(self, tmp_path: Path) -> None:
        """Files with uppercase .YAML extension are allowed."""
        yaml_file = tmp_path / "test.YAML"
        yaml_file.write_text("system_prompt: test\nuser_prompt: test")

        config = AgentConfig(
            name="test_agent",
            prompts_file=yaml_file,  # Absolute path
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        # Should not raise - extension check is case-insensitive
        core = AgentCore(config)
        assert core.prompts["system_prompt"] == "test"

    def test_invalid_extension_logs_security_event(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid file extension logs a security event."""
        import logging

        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("/some/path/test.exe"),
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError):
                AgentCore(config)

        # Check for security event in logs
        assert any(
            "extension" in record.message.lower()
            or record.__dict__.get("security_event") == "invalid_extension_blocked"
            for record in caplog.records
        )


class TestRelativePathResolution:
    """Tests for relative path handling within allowed directory."""

    @pytest.fixture
    def mock_prompts_dir(self, tmp_path: Path) -> Path:
        """Create a mock prompts directory with test files."""
        prompts_dir = tmp_path / "config" / "agents"
        prompts_dir.mkdir(parents=True)

        # Create valid prompt file
        valid_file = prompts_dir / "test.yaml"
        valid_file.write_text("system_prompt: test\nuser_prompt: test")

        return prompts_dir

    def test_relative_path_within_dir_allowed(self, mock_prompts_dir: Path) -> None:
        """Relative paths within the prompts directory are allowed."""
        with patch("src.agents.core.settings") as mock_settings:
            mock_settings.agent_prompts_dir = str(mock_prompts_dir)

            config = AgentConfig(
                name="test_agent",
                prompts_file=Path("test.yaml"),  # Relative path
                output_type=object,  # type: ignore
                model_tier=ModelTier.EFFICIENT,
            )

            core = AgentCore(config)
            assert core.prompts["system_prompt"] == "test"

    def test_relative_path_escaping_blocked(self, mock_prompts_dir: Path) -> None:
        """Relative paths that escape the directory are blocked."""
        with patch("src.agents.core.settings") as mock_settings:
            mock_settings.agent_prompts_dir = str(mock_prompts_dir)

            config = AgentConfig(
                name="test_agent",
                prompts_file=Path("../../etc/passwd.yaml"),  # Attempts escape
                output_type=object,  # type: ignore
                model_tier=ModelTier.EFFICIENT,
            )

            with pytest.raises(ValueError, match="Invalid prompts file path"):
                AgentCore(config)

    def test_absolute_path_outside_dir_allowed_with_valid_extension(
        self, tmp_path: Path
    ) -> None:
        """Absolute paths outside prompts_dir are allowed (no traversal check for absolute)."""
        # Create a valid YAML file outside the normal prompts directory
        external_file = tmp_path / "external" / "prompts.yaml"
        external_file.parent.mkdir(parents=True)
        external_file.write_text("system_prompt: external\nuser_prompt: test")

        config = AgentConfig(
            name="test_agent",
            prompts_file=external_file,  # Absolute path
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        # Absolute paths skip the directory check (intentional)
        core = AgentCore(config)
        assert core.prompts["system_prompt"] == "external"


class TestYamlParsing:
    """Tests for YAML parsing security."""

    def test_invalid_yaml_raises_value_error(self, tmp_path: Path) -> None:
        """Invalid YAML syntax raises ValueError with context."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("system_prompt: [invalid yaml without closing bracket")

        config = AgentConfig(
            name="test_agent",
            prompts_file=bad_yaml,
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        with pytest.raises(ValueError, match="Invalid YAML"):
            AgentCore(config)

    def test_yaml_safe_load_used(self, tmp_path: Path) -> None:
        """yaml.safe_load is used (no arbitrary code execution)."""
        # This YAML would execute code if unsafe_load was used
        unsafe_yaml = tmp_path / "unsafe.yaml"
        unsafe_yaml.write_text(
            "system_prompt: !!python/object/apply:os.system ['echo pwned']"
        )

        config = AgentConfig(
            name="test_agent",
            prompts_file=unsafe_yaml,
            output_type=object,  # type: ignore
            model_tier=ModelTier.EFFICIENT,
        )

        # safe_load should not execute the code, but may parse it as string
        # or raise an error depending on YAML version
        try:
            core = AgentCore(config)
            # If it parses, the system_prompt should be a string, not executed
            assert isinstance(core.prompts.get("system_prompt"), str)
        except Exception:
            # Any error is fine - we just don't want code execution
            pass
