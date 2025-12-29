# PR #32 Integration Guide: Debug Logging for New Agent Architecture

## Executive Summary

PR #32 adds comprehensive debug logging but was built against an **old class-based agent architecture** that has since been refactored. This document provides context and instructions for adapting the PR to work with the new module-based agent pattern.

**Action Required**: Adapt PR #32's agent instrumentation to work with the new module function pattern while preserving the reusable infrastructure components.

---

## Background: Agent Architecture Refactoring

### What Changed (Commits on `refactor/agent-infrastructure-and-prds`)

The agent architecture was refactored from **class-based inheritance** to **module functions with lazy singletons**.

#### Old Pattern (What PR #32 Targets)

```python
# Old: Agents inherited from BaseDocumentAgent
class TypographyAgent(BaseDocumentAgent[TypographyAnalysisOutput]):
    def __init__(self):
        config = AgentConfig(...)
        super().__init__(config)

    async def analyze(self, pages, manifest, markdown, job_id):
        # Called self._run_agent() from base class
        output, usage = await self._run_agent(user_message, image_bytes)
        return observations, usage
```

The key method was `BaseDocumentAgent._run_agent()` in `base_agent.py` which:
- Created the PydanticAI agent
- Ran `agent.run()` with the prompt
- Extracted usage metrics
- **PR #32 added debug logging here** - one place instrumented all agents

#### New Pattern (Current Architecture)

```python
# New: Agents are modules with functions
# src/agents/typography_agent.py

_agent: Agent[AgentDependencies, TypographyAnalysisOutput] | None = None
_prompts: dict[str, Any] | None = None
_MODEL_TIER = ModelTier.REASONING

def get_agent() -> Agent[...]:
    """Lazy singleton initialization."""
    global _agent, _prompts
    if _agent is None:
        _prompts = load_prompts("typography.yaml")
        _agent = create_agent("typography.yaml", TypographyAnalysisOutput, ...)
        _register_dynamic_instructions(_agent)
    return _agent

async def analyze(pages, manifest, markdown, job_id, deps=None):
    """Main analysis function - calls agent.run() directly."""
    agent = get_agent()
    # ... build prompt ...
    result = await agent.run(user_message, deps=page_deps, ...)  # Direct call!
    output = result.data
    usage = extract_usage(result, _MODEL_TIER)
    # ... convert to observations ...
    return observations, combined_usage

# Wrapper class for backward compatibility with AgentRouter
class TypographyAgent:
    async def analyze(self, pages, manifest, markdown, job_id, deps=None):
        return await analyze(pages, manifest, markdown, job_id, deps)
```

**Key Differences**:
1. No `_run_agent()` method - each agent calls `agent.run()` directly
2. No inheritance chain - agents are standalone modules
3. `factory.py` provides shared utilities (`create_agent`, `load_prompts`, `extract_usage`)
4. Wrapper classes exist only for `AgentRouter` compatibility

### Why This Matters for PR #32

PR #32 added debug logging to `base_agent.py._run_agent()`:

```python
# PR #32's approach (NO LONGER WORKS)
async def _run_agent(self, user_message, image_bytes=None, job_id=None):
    debug_logger.log_prompt(job_id, self.name, ...)  # Added by PR #32
    result = await agent.run(messages, ...)
    debug_logger.log_response(job_id, ...)  # Added by PR #32
    return result.output, usage
```

**This method no longer exists.** Agents now call `agent.run()` directly in their module functions.

---

## Files in PR #32: What's Reusable vs What Needs Adaptation

### ✅ Fully Reusable (Cherry-pick as-is)

| File | Purpose |
|------|---------|
| `src/services/debug_logging_service.py` | Core debug logging infrastructure - completely architecture-agnostic |
| `src/middleware/debug_middleware.py` | HTTP request/response logging - no agent dependencies |
| `src/config.py` additions | Debug mode settings (`debug_mode`, `debug_log_prompts`, etc.) |
| `src/main.py` changes | Middleware registration |

### ⚠️ Needs Review (May Have Conflicts)

| File | Issue |
|------|-------|
| `src/services/processing_service.py` | Check against current `processing_service.py` - may have merge conflicts from other changes |
| `src/workers/pii_worker.py` | Check for conflicts |
| `src/workers/processing_worker.py` | Check for conflicts |

### ❌ Needs Complete Rewrite

| File | Issue |
|------|-------|
| `src/agents/base_agent.py` | PR adds to `_run_agent()` which no longer handles LLM calls |
| `src/agents/analysis_agent.py` | PR modifies old class-based `analyze()` method |
| `src/agents/consolidation_agent.py` | PR modifies old class-based `consolidate()` method |
| `src/agents/extraction_agent.py` | PR modifies old class-based `extract()` method |
| `src/agents/figures_agent.py` | PR adds `job_id` to `_run_agent()` call that no longer exists |
| `src/agents/structure_agent.py` | PR adds `job_id` to `_run_agent()` call that no longer exists |
| `src/agents/tables_agent.py` | PR adds `job_id` to `_run_agent()` call that no longer exists |
| `src/agents/typography_agent.py` | PR adds `job_id` to `_run_agent()` call that no longer exists |

---

## Integration Strategy

### Recommended Approach: Centralized Wrapper in `factory.py`

Add a debug-aware wrapper function to `src/agents/factory.py`:

```python
# Add to src/agents/factory.py

import time
from typing import Any

async def run_agent_with_debug(
    agent: Agent,
    prompt: str | list[Any],
    job_id: str,
    agent_name: str,
    model_tier: ModelTier,
    system_prompt: str | None = None,
    image_info: dict | None = None,
    **run_kwargs,
) -> Any:
    """Run agent with optional debug logging.

    Wraps agent.run() with debug logging when settings.debug_mode is enabled.
    Use this instead of calling agent.run() directly in agents.

    Args:
        agent: PydanticAI agent instance
        prompt: User prompt (string or list with images)
        job_id: Job ID for correlation
        agent_name: Name of the calling agent (e.g., "typography", "analysis")
        model_tier: Model tier for pricing info
        system_prompt: Optional system prompt for logging
        image_info: Optional image metadata for logging
        **run_kwargs: Additional kwargs passed to agent.run()

    Returns:
        PydanticAI AgentRunResult
    """
    from src.config import settings

    # Only import debug logger if debug mode is enabled
    if settings.debug_mode:
        from src.services.debug_logging_service import debug_logger
        from src.agents.model_tiers import MODEL_TIER_MAP

        debug_logger.log_prompt(
            job_id=job_id,
            agent_name=agent_name,
            system_prompt=system_prompt,
            user_message=prompt if isinstance(prompt, str) else str(prompt),
            image_info=image_info,
            model_id=MODEL_TIER_MAP[model_tier],
            model_tier=model_tier.value,
            temperature=run_kwargs.get("model_settings", {}).get("temperature"),
            max_tokens=run_kwargs.get("model_settings", {}).get("max_tokens"),
        )

    start_time = time.time()
    result = await agent.run(prompt, **run_kwargs)
    duration_ms = (time.time() - start_time) * 1000

    if settings.debug_mode:
        usage = result.usage()
        debug_logger.log_response(
            job_id=job_id,
            agent_name=agent_name,
            response_text=None,  # Structured output
            parsed_output=result.data,
            input_tokens=usage.request_tokens or 0,
            output_tokens=usage.response_tokens or 0,
            total_tokens=(usage.request_tokens or 0) + (usage.response_tokens or 0),
            estimated_cost_cents=0,  # Calculate if needed
            duration_ms=duration_ms,
            model_id=MODEL_TIER_MAP[model_tier],
        )

    return result
```

### Then Update Each Agent

Replace direct `agent.run()` calls with `run_agent_with_debug()`:

```python
# Example: src/agents/typography_agent.py

# Before (current code):
result = await agent.run(
    user_message,
    deps=page_deps,
    message_history=[...],
)

# After (with debug logging):
from src.agents.factory import run_agent_with_debug

result = await run_agent_with_debug(
    agent=agent,
    prompt=user_message,
    job_id=job_id,
    agent_name="typography",
    model_tier=_MODEL_TIER,
    system_prompt=_prompts.get("system_prompt"),
    image_info={"page_num": page.page_num} if page.image_base64 else None,
    deps=page_deps,
    message_history=[...],
)
```

---

## Step-by-Step Implementation Guide

### Step 1: Cherry-pick Reusable Infrastructure

```bash
# From PR #32 branch, cherry-pick or manually copy these files:
# - src/services/debug_logging_service.py (new file)
# - src/middleware/debug_middleware.py (new file)
# - src/config.py (merge debug settings)
# - src/main.py (merge middleware registration)
```

### Step 2: Add Wrapper to `factory.py`

Add the `run_agent_with_debug()` function shown above to `src/agents/factory.py`.

### Step 3: Update Each Agent Module

For each agent, find where `agent.run()` is called and wrap it:

| Agent | Function | Location of `agent.run()` |
|-------|----------|---------------------------|
| `typography_agent.py` | `analyze()` | ~line 265 |
| `figures_agent.py` | `analyze()` | ~line 200 |
| `tables_agent.py` | `analyze()` | ~line 200 |
| `structure_agent.py` | `analyze()` | ~line 205 |
| `consolidation_agent.py` | `consolidate()` | ~line 270 |
| `extraction_agent.py` | `extract()` | ~line 350 |
| `analysis_agent.py` | `analyze()` | ~line 340 |

### Step 4: Merge Processing Service and Worker Changes

Review and merge the `processing_service.py` and worker changes from PR #32, resolving any conflicts with current code.

### Step 5: Test

```bash
# Run tests
make test-fast

# Test debug mode manually
DEBUG_MODE=true make dev
# Submit a document and check logs
```

---

## Current Agent File Locations and Patterns

For reference, here's where to find the `agent.run()` calls in each agent:

### `src/agents/typography_agent.py`
```python
# Around line 265-280
if image_bytes:
    result = await agent.run(
        user_message,
        deps=page_deps,
        message_history=[{...}],
    )
else:
    result = await agent.run(user_message, deps=page_deps)
```

### `src/agents/analysis_agent.py`
```python
# Around line 340
result = await agent.run(
    user_message,
    model_settings={"max_tokens": 16000, "temperature": 0.3},
)
```

### `src/agents/extraction_agent.py`
```python
# Around line 350
result = await agent.run(
    user_message,
    model_settings={"max_tokens": 16384, "temperature": 0.2},
)
```

### `src/agents/consolidation_agent.py`
```python
# Around line 270
result = await agent.run(
    user_message,
    model_settings={"max_tokens": 32000, "temperature": 0.3},
)
```

### `src/agents/figures_agent.py`, `tables_agent.py`, `structure_agent.py`
```python
# Similar pattern around line 200-220
result = await agent.run(
    user_message,
    deps=page_deps,
    message_history=[{...}],
)
```

---

## Key Files to Read

Before making changes, read these files to understand the current architecture:

1. `src/agents/factory.py` - Agent creation utilities (NEW FILE)
2. `src/agents/helpers.py` - Shared helper functions (NEW FILE)
3. `src/agents/typography_agent.py` - Reference implementation of new pattern
4. `ai-docs/agent-refactor-plan.md` - Full refactoring plan with rationale

---

## Testing Checklist

After integration:

- [ ] All 597 unit tests pass (`make test-fast`)
- [ ] Debug mode can be enabled via `DEBUG_MODE=true`
- [ ] Debug logs appear when processing a document
- [ ] Debug logs include job_id for correlation
- [ ] Debug logs show prompt and response for each agent
- [ ] No debug logs appear when `DEBUG_MODE=false` (default)
- [ ] Ruff and mypy pass on changed files

---

## Summary

| Task | Approach |
|------|----------|
| Debug infrastructure | Cherry-pick from PR #32 |
| Agent instrumentation | Add `run_agent_with_debug()` to `factory.py`, update each agent |
| Config settings | Merge from PR #32 |
| Middleware | Cherry-pick from PR #32 |
| Worker logging | Review and merge from PR #32 |

The core debug logging service is excellent work and fully reusable. Only the agent instrumentation needs to be adapted for the new module function pattern.
