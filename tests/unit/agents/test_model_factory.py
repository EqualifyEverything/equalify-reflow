"""Tests for the backend-agnostic model factory."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr
from pydantic_ai.models.anthropic import AnthropicModel

from src.agents.model_factory import _resolve_provider, get_model_for_tier
from src.agents.model_tiers import ANTHROPIC_TIER_MAP, BEDROCK_TIER_MAP, ModelTier


@pytest.mark.unit
class TestResolveProvider:
    """_resolve_provider implements the three-mode selection logic."""

    def test_explicit_bedrock_overrides_auto_detect(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings:
            mock_settings.ai_provider = "bedrock"
            mock_settings.anthropic_api_key = SecretStr("sk-ant-test")
            assert _resolve_provider() == "bedrock"

    def test_explicit_anthropic_used_when_set(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings:
            mock_settings.ai_provider = "anthropic"
            mock_settings.anthropic_api_key = SecretStr("sk-ant-test")
            assert _resolve_provider() == "anthropic"

    def test_auto_detect_picks_anthropic_when_key_present(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings:
            mock_settings.ai_provider = None
            mock_settings.anthropic_api_key = SecretStr("sk-ant-test")
            assert _resolve_provider() == "anthropic"

    def test_auto_detect_falls_back_to_bedrock_when_no_key(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings:
            mock_settings.ai_provider = None
            mock_settings.anthropic_api_key = None
            assert _resolve_provider() == "bedrock"


_BEDROCK_PATCH = "pydantic_ai.models.bedrock.BedrockConverseModel"


@pytest.mark.unit
class TestGetModelForTier:
    """get_model_for_tier returns a correctly-configured Model per backend.

    The Bedrock class is patched at its source location because the factory's
    lazy import path is ``from pydantic_ai.models.bedrock import
    BedrockConverseModel`` inside the function body — same pattern used by the
    existing subagent test suites.
    """

    def test_bedrock_backend_instantiates_bedrock_class_with_efficient_id(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings, patch(
            _BEDROCK_PATCH
        ) as mock_bedrock:
            mock_settings.ai_provider = "bedrock"
            mock_settings.anthropic_api_key = None
            mock_bedrock.return_value = MagicMock(name="BedrockConverseModelInstance")

            get_model_for_tier(ModelTier.EFFICIENT)

            mock_bedrock.assert_called_once_with(BEDROCK_TIER_MAP[ModelTier.EFFICIENT])

    def test_bedrock_backend_instantiates_bedrock_class_with_reasoning_id(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings, patch(
            _BEDROCK_PATCH
        ) as mock_bedrock:
            mock_settings.ai_provider = "bedrock"
            mock_settings.anthropic_api_key = None
            mock_bedrock.return_value = MagicMock(name="BedrockConverseModelInstance")

            get_model_for_tier(ModelTier.REASONING)

            mock_bedrock.assert_called_once_with(BEDROCK_TIER_MAP[ModelTier.REASONING])

    def test_anthropic_returns_anthropic_model_with_efficient_id(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings:
            mock_settings.ai_provider = "anthropic"
            mock_settings.anthropic_api_key = SecretStr("sk-ant-test")
            model = get_model_for_tier(ModelTier.EFFICIENT)
            assert isinstance(model, AnthropicModel)
            assert model.model_name == ANTHROPIC_TIER_MAP[ModelTier.EFFICIENT]

    def test_anthropic_without_key_raises_value_error(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings:
            mock_settings.ai_provider = "anthropic"
            mock_settings.anthropic_api_key = None
            with pytest.raises(ValueError, match="anthropic_api_key"):
                get_model_for_tier(ModelTier.EFFICIENT)

    def test_auto_detect_with_key_uses_anthropic(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings:
            mock_settings.ai_provider = None
            mock_settings.anthropic_api_key = SecretStr("sk-ant-test")
            model = get_model_for_tier(ModelTier.EFFICIENT)
            assert isinstance(model, AnthropicModel)

    def test_auto_detect_without_key_uses_bedrock(self) -> None:
        with patch("src.agents.model_factory.settings") as mock_settings, patch(
            _BEDROCK_PATCH
        ) as mock_bedrock:
            mock_settings.ai_provider = None
            mock_settings.anthropic_api_key = None
            mock_bedrock.return_value = MagicMock(name="BedrockConverseModelInstance")

            get_model_for_tier(ModelTier.EFFICIENT)

            mock_bedrock.assert_called_once()


@pytest.mark.unit
class TestTierMapsStayInSync:
    """Both backend tier maps must cover every ModelTier value."""

    def test_bedrock_map_covers_every_tier(self) -> None:
        assert set(BEDROCK_TIER_MAP.keys()) == set(ModelTier)

    def test_anthropic_map_covers_every_tier(self) -> None:
        assert set(ANTHROPIC_TIER_MAP.keys()) == set(ModelTier)
