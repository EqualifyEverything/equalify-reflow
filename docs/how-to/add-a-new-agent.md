# How to add a new agent

An "agent" here is a PydanticAI `Agent` instance with a system prompt and a structured output type. Adding one means three things: a new prompt module, a new call site in the pipeline, and tests.

For the current agent call sites and pipeline flow, see [pipeline phases reference](../reference/pipeline-phases.md). For model-tier selection, see [model tiers reference](../reference/model-tiers.md).

## 1. Create the prompt module

```
src/agents/my_new_agent.py
```

```python
"""Prompt and output types for the {what it does} agent."""

from pydantic import BaseModel, Field


MY_AGENT_SYSTEM_PROMPT = """
You are a {...}. Given {input}, produce {output}.

Rules:
- ...
"""


class MyAgentOutput(BaseModel):
    """Structured output for my_new_agent."""

    # Keep this tight. Every field should have a clear purpose.
    decision: str
    reasoning: str = Field(description="Why this decision was made.")
```

Always use `output_type=<PydanticModel>`. Never parse free text from agent responses — the whole point of the agent framework is schema-validated output.

## 2. Add the call site

In `src/services/pipeline_viewer.py`, add the agent invocation inside the appropriate `_step_*` method:

```python
from pydantic_ai import Agent
from src.agents.my_new_agent import MY_AGENT_SYSTEM_PROMPT, MyAgentOutput
from src.agents.model_factory import get_model_for_tier
from src.agents.model_tiers import ModelTier

async def _step_my_phase(self, result: PipelineViewerResult) -> None:
    model = get_model_for_tier(ModelTier.EFFICIENT)  # Default — promote only if fixtures prove it
    agent: Agent[None, MyAgentOutput] = Agent(
        model=model,
        output_type=MyAgentOutput,
        system_prompt=MY_AGENT_SYSTEM_PROMPT,
    )
    # ... run the agent, record the StepResult, update version ...
```

Resolve models through `get_model_for_tier` — never hardcode model IDs or instantiate backend-specific classes directly. That's what keeps Bedrock / Anthropic interchangeable.

## 3. Decide which public phase this belongs to

Update the viewer's `PIPELINE_STAGES` in `clients/viewer/src/types/pipeline-viewer.ts` to place the new step under the right public phase (Extraction / Analysis / Headings / Translation / Assembly), and add its display name to the `nameMap` in `StageTabs.tsx`. If your step genuinely doesn't fit any of the five, leave it out of `PIPELINE_STAGES` and it will surface in the dynamic **Review** stage.

Update [pipeline phases reference](../reference/pipeline-phases.md) to match.

## 4. Write tests

**Unit** (`tests/unit/services/` or `tests/unit/agents/`): mock the model. Test that your `_step_*` method handles the full decision matrix — each enum value, each edge case, each error path.

```python
@pytest.mark.unit
async def test_my_agent_happy_path():
    # ... mock get_model_for_tier to return a stub that yields a specific MyAgentOutput ...
```

**Integration** (`tests/integration/`): real agent against a tiny fixture. Validates the prompt actually produces the expected structured output with a real Haiku call.

## 5. Update the agent index

Add a row to the pipeline table in `AGENTS.md` so human and agent readers see the new step immediately.

## 6. Verify end-to-end

```bash
make test-fast
make test-integration
make test-e2e  # If your step affects downstream behaviour
```

## Tier selection

Default `ModelTier.EFFICIENT` (Haiku 4.5). Only promote to `ModelTier.REASONING` (Sonnet 4.5) if your integration fixtures show measurable quality improvement that justifies the ~3× cost. "Seems better" is not enough — quantify it in the PR body.

## Subagent pattern

If your step delegates sub-tasks (e.g., the page-content agent invoking an image describer), implement each subagent as its own `Agent(...)` call within the parent's tool function. See `_step_page_content` for the canonical pattern — image, table, and list subagents are all structured this way.
