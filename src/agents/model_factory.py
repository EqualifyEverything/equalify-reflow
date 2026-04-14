"""Backend-agnostic model factory for pipeline agents.

The versioned pipeline in ``src/services/pipeline_viewer.py`` instantiates a
PydanticAI ``Model`` at every agent call site. Historically this was always
``BedrockConverseModel(MODEL_TIER_MAP[tier])``, which hard-coded the backend.

This module replaces that pattern with a single ``get_model_for_tier(tier)``
factory function that returns a Bedrock or direct-Anthropic model based on the
runtime configuration:

- ``AI_PROVIDER=bedrock`` -> Bedrock (production default via env var)
- ``AI_PROVIDER=anthropic`` -> Anthropic direct
- ``AI_PROVIDER`` unset -> auto-detect: Anthropic if ``ANTHROPIC_API_KEY`` is
  set, otherwise Bedrock.

Auto-detect is the contributor-friendly mode: set ``ANTHROPIC_API_KEY`` in your
local ``.env`` and ``make dev`` runs against Anthropic direct with no other
configuration. Production deployments set ``AI_PROVIDER=bedrock`` explicitly in
the ECS task definition to guarantee deterministic backend selection.

Both ``AnthropicModel`` and ``BedrockConverseModel`` implement PydanticAI's
``Model`` interface, so they drop into ``Agent(model, ...)`` call sites
interchangeably. No changes to pipeline logic are required.
"""

import logging
from typing import TYPE_CHECKING, Literal

from ..config import settings
from .model_tiers import ANTHROPIC_TIER_MAP, BEDROCK_TIER_MAP, ModelTier

if TYPE_CHECKING:
    from pydantic_ai.models import Model

logger = logging.getLogger(__name__)


ResolvedProvider = Literal["anthropic", "bedrock"]


def _resolve_provider() -> ResolvedProvider:
    """Determine which backend to use for the current process.

    Resolution order:

    1. If ``settings.ai_provider`` is explicitly set, honour it.
    2. Otherwise auto-detect: return ``anthropic`` when
       ``settings.anthropic_api_key`` is set, else ``bedrock``.

    Returns:
        The resolved provider name.
    """
    if settings.ai_provider is not None:
        return settings.ai_provider

    if settings.anthropic_api_key is not None:
        return "anthropic"

    return "bedrock"


def get_model_for_tier(tier: ModelTier) -> "Model":
    """Return a PydanticAI ``Model`` instance for the requested tier.

    The returned model is constructed from the backend resolved by
    ``_resolve_provider()``. Callers should treat this as opaque and pass it
    directly to ``Agent(model, ...)`` — the calling code should not depend on
    which concrete ``Model`` subclass comes back.

    The pydantic-ai model imports are deliberately lazy (done inside the
    function body) so that unit tests can patch
    ``pydantic_ai.models.bedrock.BedrockConverseModel`` and
    ``pydantic_ai.models.anthropic.AnthropicModel`` at the source location and
    have those patches take effect on every call.

    Args:
        tier: The capability tier (EFFICIENT or REASONING) the agent needs.

    Returns:
        A ``Model`` ready to be passed to a PydanticAI ``Agent``.

    Raises:
        ValueError: If ``ai_provider=anthropic`` is selected but
            ``anthropic_api_key`` is not configured.
    """
    provider = _resolve_provider()

    if provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise ValueError(
                "ai_provider=anthropic requires anthropic_api_key to be set"
            )
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        model_id = ANTHROPIC_TIER_MAP[tier]
        return AnthropicModel(
            model_id,
            provider=AnthropicProvider(
                api_key=settings.anthropic_api_key.get_secret_value()
            ),
        )

    from pydantic_ai.models.bedrock import BedrockConverseModel

    model_id = BEDROCK_TIER_MAP[tier]
    return BedrockConverseModel(model_id)


__all__ = [
    "get_model_for_tier",
]
