# Model tiers reference

Model selection across the pipeline happens through two tiers defined in `src/agents/model_tiers.py`. Each agent picks a tier; a backend-specific map resolves that tier to a concrete model ID at call time.

**Source of truth:** `src/agents/model_tiers.py`.

## Tiers

| Tier | Model family | Used for | Pricing (per 1M tokens, approx) |
|---|---|---|---|
| `ModelTier.EFFICIENT` | Claude Haiku 4.5 | Every current agent call site. Fast, cheap, validated against integration fixtures. | ~$1 input / ~$5 output |
| `ModelTier.REASONING` | Claude Sonnet 4.5 | Reserved for heavier analysis. Not wired into pipeline call sites today; available for new agents that measurably benefit. | ~$3 input / ~$15 output |

Default to `EFFICIENT`. Promote to `REASONING` only when integration fixtures show measurable quality improvement that the 3× cost justifies.

## Backend maps

Two dicts, one per supported backend. Both are pinned to the same model generation (`...-v1:0` for Bedrock, bare `claude-<tier>-4-5-<date>` for Anthropic direct) so output parity holds when switching backends.

### Bedrock inference profiles

```python
BEDROCK_TIER_MAP = {
    ModelTier.REASONING: "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ModelTier.EFFICIENT: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}
```

The `us.` prefix is **required** for Claude 4.5 models on Bedrock — these are inference profile IDs, not on-demand model IDs. On-demand will 404.

### Anthropic direct

```python
ANTHROPIC_TIER_MAP = {
    ModelTier.REASONING: "claude-sonnet-4-5-20250929",
    ModelTier.EFFICIENT: "claude-haiku-4-5-20251001",
}
```

## Resolving a model at call time

Agents should **not** reach into these dicts directly. Use `get_model_for_tier` from `src/agents/model_factory.py`:

```python
from src.agents.model_factory import get_model_for_tier
from src.agents.model_tiers import ModelTier

model = get_model_for_tier(ModelTier.EFFICIENT)
agent = Agent(model=model, output_type=MyOutputModel, ...)
```

The factory picks the backend based on `AI_PROVIDER` and available credentials (see [AI backend selection](../explanation/ai-backend-selection.md) when that doc lands; for now the logic is: `AI_PROVIDER=anthropic` forces direct, `AI_PROVIDER=bedrock` forces Bedrock, unset auto-detects by checking `ANTHROPIC_API_KEY`).

## Backwards-compatibility aliases

`model_tiers.py` still exports `MODEL_TIER_MAP` (alias for `BEDROCK_TIER_MAP`) and `get_model_id(tier)` (returns Bedrock ID). New code should not use these — they're retained only for the handful of call sites that haven't been migrated to the factory.

## Updating the tiers

When a new Claude model generation ships and fixtures validate the upgrade:

1. Update both `BEDROCK_TIER_MAP` and `ANTHROPIC_TIER_MAP` together — never one without the other, or output will drift between backends.
2. Run `make test-e2e` against the full fixture suite to catch regressions.
3. Note the generation change in the PR body so reviewers can sanity-check.
