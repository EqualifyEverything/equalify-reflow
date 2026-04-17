# Tutorial: Add your first agent

In this walkthrough you will add a new AI agent to the pipeline, wire it in, watch it run against a real PDF, and see its output in the viewer. Plan for **~30 minutes**, most of which is waiting for the stack and reading the result.

**Prerequisite:** work through [convert your first PDF](convert-your-first-pdf.md) first so you have the stack running and have successfully processed at least one document. This tutorial assumes you already know how to submit a PDF and retrieve results.

**Scope:** this tutorial builds a working example so you see the whole loop. For the canonical mechanics and tradeoffs (model-tier selection, subagent patterns, tests), see [how to add a new agent](../how-to/add-a-new-agent.md) — use that as reference, this as introduction.

## The example we're building

A **Topic Tagger**. After the pipeline finishes assembling the document, this agent reads the cleaned markdown and classifies it into one of five broad topics: `science`, `legal`, `education`, `business`, or `other`. It doesn't modify the markdown — it just attaches a label and a one-sentence reason.

This is deliberately tiny. It doesn't fit into any of the 5 public phases (Extraction, Analysis, Headings, Translation, Assembly), so it will surface in the viewer's dynamic **Review** stage — exactly what that stage is for.

## 1. Design the output shape

Every agent in Reflow returns a validated Pydantic model — never free text. Our output needs three things: the topic label, the reasoning, and a confidence level. That's it.

Create `src/agents/prompts/topic_tagger.py`:

```python
"""Prompt and output types for the Topic Tagger agent."""

from typing import Literal

from pydantic import BaseModel, Field


TOPIC_TAGGER_SYSTEM_PROMPT = """\
You are a document topic classifier. Given the full markdown of an accessible
document, assign it to ONE of these broad topics:

- science: research papers, scientific reports, technical analyses
- legal: contracts, policies, terms of service, compliance docs
- education: syllabi, course materials, textbooks, study guides
- business: reports, proposals, memos, internal documentation
- other: anything that doesn't clearly fit the categories above

Rules:
- Pick exactly one topic. Ties go to the most specific category.
- Reasoning must cite a concrete signal from the document (a section heading,
  a vocabulary pattern, a formatting clue) — never just restate the topic.
- Confidence is "high" if the document is unambiguous, "medium" if you had
  to choose between two plausible topics, "low" if you're guessing.
"""


class TopicTaggerOutput(BaseModel):
    """Output from the Topic Tagger agent."""

    topic: Literal["science", "legal", "education", "business", "other"]
    reasoning: str = Field(description="Concrete signal from the document that drove the choice.")
    confidence: Literal["high", "medium", "low"]
```

The `Literal` types here are load-bearing — PydanticAI uses them to validate the model's output. If the model returns anything other than one of those five topic strings, you'll get a validation error instead of garbage data downstream.

## 2. Wire in a new pipeline step

Open `src/services/pipeline_viewer.py`. Find the `_step_cleanup` method (it's the last one) and add a new `_step_topic_tag` method right after it:

```python
async def _step_topic_tag(self, result: PipelineViewerResult) -> None:
    """Tag the assembled document with a broad topic label.

    Reads the final (v3) markdown, classifies it, and records the result
    as metadata. Does NOT modify the markdown.
    """
    from pydantic_ai import Agent
    from ..agents.model_factory import get_model_for_tier
    from ..agents.model_tiers import ModelTier
    from ..agents.prompts.topic_tagger import (
        TOPIC_TAGGER_SYSTEM_PROMPT,
        TopicTaggerOutput,
    )

    step_start = time.time()
    document = result.versions.get("v3", "")

    if not document:
        # Nothing to tag — skip cleanly.
        result.steps.append(
            StepResult(
                name="topic_tag",
                display_name="Topic Tag",
                version_before="v3",
                version_after="v3",
                elapsed_ms=int((time.time() - step_start) * 1000),
                changes=[],
                metadata={},
                skipped=True,
            )
        )
        return

    model = get_model_for_tier(ModelTier.EFFICIENT)
    agent: Agent[None, TopicTaggerOutput] = Agent(
        model=model,
        output_type=TopicTaggerOutput,
        system_prompt=TOPIC_TAGGER_SYSTEM_PROMPT,
    )

    try:
        agent_result = await agent.run(document)
        tagged = agent_result.output
        usage = agent_result.usage()
        input_tokens = usage.request_tokens or 0
        output_tokens = usage.response_tokens or 0
    except Exception as e:
        logger.error(f"Topic tagger failed: {e}")
        result.steps.append(
            StepResult(
                name="topic_tag",
                display_name="Topic Tag",
                version_before="v3",
                version_after="v3",
                elapsed_ms=int((time.time() - step_start) * 1000),
                changes=[],
                metadata={},
                error=str(e),
            )
        )
        return

    result.steps.append(
        StepResult(
            name="topic_tag",
            display_name="Topic Tag",
            version_before="v3",
            version_after="v3",
            elapsed_ms=int((time.time() - step_start) * 1000),
            changes=[],
            metadata={
                "topic": tagged.topic,
                "reasoning": tagged.reasoning,
                "confidence": tagged.confidence,
            },
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )
```

A few things worth noting about this pattern:

- **Import at the call site, not at the top of the file.** `pipeline_viewer.py` is big; top-level imports slow startup and create cycles. Inline imports keep the module self-contained.
- **Always record a `StepResult`**, including on error or skip. The viewer relies on this — missing steps look like bugs to users.
- **No markdown mutation.** We don't touch `result.versions`. This is a read-only analysis step.

## 3. Call the new step from the pipeline

Find where `_step_cleanup` is awaited in the main `process` loop — it's the last step in the sequence. Add `await self._step_topic_tag(result)` right after it:

```python
# ... existing pipeline ...
await self._step_cleanup(result)
await self._step_topic_tag(result)   # ← new
```

## 4. Hot reload picks up the change

If `make dev` is still running, the api-gateway container has the `./src` bind mount and uvicorn's `--reload`. Save the two files and the new step is live on the next pipeline run. No restart needed.

Watch the logs confirm the reload:

```bash
make logs-api | grep -i reload
```

You should see uvicorn noticing the changed files.

## 5. Run a PDF through the pipeline

Submit a document — same flow as [convert your first PDF](convert-your-first-pdf.md). If it triggers PII approval, approve it, otherwise it runs straight through:

```bash
export API_KEY=$(grep "^API_KEYS=" .env | cut -d= -f2 | cut -d, -f1)

JOB_ID=$(curl -s -X POST http://localhost:8080/api/v1/documents/submit \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/path/to/your.pdf" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

echo "Submitted: $JOB_ID"
```

Approve if needed, then wait for `status: completed` (2–4 minutes for a small PDF).

## 6. See your new step in the ledger

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "http://localhost:8080/api/v1/documents/$JOB_ID/ledger" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for step in d.get('steps', []):
    if step['name'] == 'topic_tag':
        print(json.dumps(step, indent=2))
"
```

Expected output shape:

```json
{
  "name": "topic_tag",
  "display_name": "Topic Tag",
  "version_before": "v3",
  "version_after": "v3",
  "elapsed_ms": 1240,
  "changes": [],
  "metadata": {
    "topic": "science",
    "reasoning": "Uses formal academic structure with abstract, numbered sections, and mathematical notation; references peer-reviewed literature.",
    "confidence": "high"
  },
  "input_tokens": 3210,
  "output_tokens": 48,
  "skipped": false,
  "error": null
}
```

## 7. Look at it in the viewer

Open `http://localhost:8080/` and find your job. Scroll the stage tabs — you'll see the 5 standard phases (Extraction → Analysis → Headings → Translation → Assembly) and, at the end, a **Review** stage with your `Topic Tag` step inside. Click it to see the metadata, elapsed time, and token cost.

Because your step name (`topic_tag`) isn't listed in the viewer's `PIPELINE_STAGES` constant, it lands in Review automatically. That's the right home for "analytical" steps that don't belong to any of the public phases. See [pipeline phases reference](../reference/pipeline-phases.md) for when to promote a step into a named phase instead.

## 8. Clean up

If you want to keep your agent, the remaining tasks are:

1. Add unit tests in `tests/unit/services/test_pipeline_viewer_phases.py` that mock `get_model_for_tier` and assert your `_step_topic_tag` handles each `TopicTaggerOutput` case.
2. Add an integration test in `tests/integration/` that runs the agent against a small fixture.
3. Consider whether the step should live in Review forever, or whether it deserves a promotion into `PIPELINE_STAGES` (only if it becomes a consumer-visible phase). See [how to add a new agent](../how-to/add-a-new-agent.md) for the full checklist.

If you just wanted to try this as a learning exercise and roll back:

```bash
git restore src/services/pipeline_viewer.py
rm src/agents/prompts/topic_tagger.py
```

Hot reload will drop the step from the next pipeline run.

## What you just did

- Added a PydanticAI agent with a strictly typed Pydantic output model.
- Wired it into the pipeline as a new `_step_*` method, returning a `StepResult` in both the happy and error paths.
- Used `get_model_for_tier(ModelTier.EFFICIENT)` so the code works identically against Bedrock or Anthropic direct.
- Saw it surface in the viewer's Review stage without any viewer code changes, because the viewer's step-to-phase mapping is a single constant.

That covers the complete shape of how every agent in the pipeline is built. The existing production agents (structure analysis, heading reconciliation, page content corrections, boundary fixes) follow the same pattern with more elaborate prompts, tool calls, and subagent delegation.

## Where to go next

- [How to add a new agent](../how-to/add-a-new-agent.md) — the full checklist including tests and tier selection
- [How to iterate on a prompt](../how-to/iterate-on-a-prompt.md) — improving what your agent does once it's in place
- [Pipeline phases reference](../reference/pipeline-phases.md) — when to leave a step in Review vs. promote it into a named public phase
- [Model tiers reference](../reference/model-tiers.md) — when to move from `EFFICIENT` to `REASONING`
