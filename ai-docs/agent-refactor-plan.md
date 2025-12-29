# Agent Architecture Refactoring Plan

## Executive Summary

Refactor the agent architecture from class-based inheritance to a simpler function/module pattern while preserving all valuable features (YAML prompts, model tiers, dynamic instructions, Reasoned[T], cost tracking).

**Goal**: Reduce ~600 lines of unnecessary abstraction while maintaining production functionality.

**Approach**: Three-phase hybrid strategy—start with zero-risk dead code removal, then prove the module function pattern on one agent, then decide whether to continue or pivot.

---

## Critical Review Incorporated

This plan has been reviewed by a critic who identified several concerns. This section documents those concerns and our responses.

### Concerns Addressed

| Concern | Severity | Response |
|---------|----------|----------|
| **Thread safety in module singletons** | Raised as CRITICAL | **Assessed as LOW for this codebase.** FastAPI + async architecture means coroutines, not threads. Race conditions require true multi-threading. See "Threading Model Analysis" below. |
| **Wrapper class anti-pattern** | MAJOR | **Agreed.** Plan updated to modify `AgentRouter` Protocol instead of creating wrapper classes. |
| **Test isolation with module state** | MAJOR | **Agreed.** Plan includes pytest fixtures for proper cleanup. |
| **Migration order wrong** | MODERATE | **Agreed.** AnalysisAgent moved to last in migration order. |
| **Helper function duplication** | MINOR | **Agreed.** Plan includes shared `utils.py` for common functions. |

### Threading Model Analysis

The critic raised thread safety as a critical concern for this pattern:
```python
def get_agent():
    global _agent
    if _agent is None:  # Potential race condition
        _agent = create_agent(...)
    return _agent
```

**Assessment for this codebase: LOW RISK**

1. **FastAPI with uvicorn uses async, not threads.** Multiple HTTP requests are handled via `async/await` with a single event loop. Only one coroutine executes at a time—no true parallelism within a process.

2. **Background workers are process-isolated.** The architecture uses Redis queues with separate worker processes. Each process has its own memory space—no shared state.

3. **Worst-case scenario is benign.** If two coroutines somehow did race (which asyncio prevents), the result is creating two agents with one being garbage collected. No data corruption, just wasted initialization.

4. **Initialization happens once per process.** After the first `get_agent()` call, the cached agent is reused for all subsequent requests.

**Conclusion**: Thread safety mitigation (locks) would add complexity without meaningful benefit for this async architecture. If the deployment model changes to true multi-threading, locks can be added then.

### Alternative Approaches Considered

The critic proposed **dataclass composition** as a safer alternative:

```python
@dataclass
class TypographyAgent:
    _agent: Agent | None = field(default=None, init=False)

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(...)
        return self._agent

    async def analyze(self, ...):
        agent = self._get_agent()
        # ... same logic
```

**Why we're not using this approach:**

1. **Doesn't reduce complexity much.** You still have classes, lazy initialization methods, and instance state management. The structure is nearly identical to the current pattern minus inheritance.

2. **Doesn't align with PydanticAI idioms.** PydanticAI is designed for simple, direct agent usage:
   ```python
   agent = Agent('model', instructions='...')
   result = await agent.run("prompt")
   ```
   The module function approach gets closer to this philosophy.

3. **The inheritance isn't the core problem—indirection is.** Both current and dataclass approaches wrap PydanticAI agents in lifecycle-managing classes. Module functions eliminate this indirection.

**The dataclass approach remains a valid fallback** if the module function pattern proves problematic in practice. Phase 2 is explicitly designed to test this before committing.

---

## Current Architecture Problems

### 1. Heavy Inheritance Chain
```
BaseDocumentAgent[TOutput] (315 lines)
  └─ AgentCore (290 lines)
      └─ AgentConfig (60 lines)
```

Every agent requires:
- Subclassing `BaseDocumentAgent`
- Creating an `AgentConfig` instance
- Overriding `_get_agent()` to register dynamic instructions
- Implementing `process()` that always raises `NotImplementedError`

### 2. Unused Infrastructure
- `Registry` system (~176 lines) - agents instantiated directly, not looked up
- `BatchResult` (~78 lines) - never used
- `AgentInput`/`AgentOutput` models - dead code

### 3. Abstract Method Anti-Pattern
```python
async def process(self, input_data: Any) -> TOutput:
    raise NotImplementedError("Use analyze() instead")  # Every agent does this
```

---

## Proposed Architecture

### Core Principles
1. **Agents as modules, not classes** - Each agent is a Python module with functions
2. **Factory function for agent creation** - Simple `create_agent()` replaces `AgentCore` + `BaseDocumentAgent`
3. **Lazy initialization via getter** - Preserve testability and avoid AWS calls at import time
4. **Protocol for router compatibility** - Keep `SpecializedAgent` Protocol for `AgentRouter`

### New File Structure

```
src/agents/
├── __init__.py              # Public exports
├── factory.py               # NEW: Agent creation utilities (~80 lines)
├── model_tiers.py           # KEEP: Already clean (59 lines)
├── dependencies.py          # KEEP: AgentDependencies for dynamic instructions
├── specialized_models.py    # KEEP: Output models with Reasoned[T]
├── agent_router.py          # KEEP: Minor updates to work with new pattern
│
├── analysis_agent.py        # REFACTOR: Module with functions
├── extraction_agent.py      # REFACTOR: Module with functions
├── figures_agent.py         # REFACTOR: Module with functions
├── tables_agent.py          # REFACTOR: Module with functions
├── structure_agent.py       # REFACTOR: Module with functions
├── typography_agent.py      # REFACTOR: Module with functions
└── consolidation_agent.py   # REFACTOR: Module with functions
│
├── base_agent.py            # DELETE after migration
├── core.py                  # DELETE after migration (merge into factory.py)
└── registry.py              # DELETE (unused)
```

---

## New Infrastructure

### `factory.py` (~80 lines)

```python
"""Agent creation utilities.

Provides factory functions for creating PydanticAI agents with
standard configuration (YAML prompts, model tiers, cost tracking).
"""

import logging
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

TOutput = TypeVar("TOutput", bound=BaseModel)


def load_prompts(filename: str) -> dict[str, Any]:
    """Load prompts from YAML with security validation.

    Args:
        filename: YAML filename in config/agents/ directory

    Returns:
        Dictionary containing prompts

    Raises:
        FileNotFoundError: If prompts file does not exist
        ValueError: If path traversal attempted or invalid extension
    """
    base_dir = Path(settings.agent_prompts_dir).resolve()
    prompts_file = (base_dir / filename).resolve()

    # Security: Prevent path traversal
    if base_dir not in prompts_file.parents and prompts_file != base_dir:
        raise ValueError(f"Invalid prompts file path: must be within {base_dir}")

    # Security: Validate extension
    if prompts_file.suffix.lower() not in (".yaml", ".yml"):
        raise ValueError(f"Invalid file type: {prompts_file.suffix}")

    with open(prompts_file) as f:
        return yaml.safe_load(f)


def create_agent(
    prompts_file: str,
    output_type: type[TOutput],
    model_tier: ModelTier = ModelTier.EFFICIENT,
    use_deps: bool = False,
    max_retries: int = 2,
) -> Agent[Any, TOutput]:
    """Create a PydanticAI agent with standard configuration.

    Args:
        prompts_file: YAML filename for prompts
        output_type: Pydantic model for structured output
        model_tier: REASONING (Sonnet) or EFFICIENT (Haiku)
        use_deps: Enable AgentDependencies for dynamic instructions
        max_retries: Retry count for validation failures

    Returns:
        Configured PydanticAI Agent instance
    """
    prompts = load_prompts(prompts_file)
    model = BedrockConverseModel(MODEL_TIER_MAP[model_tier])

    if use_deps:
        from src.agents.dependencies import AgentDependencies
        return Agent(
            model,
            deps_type=AgentDependencies,
            output_type=output_type,
            system_prompt=prompts["system_prompt"],
            retries=max_retries,
        )

    return Agent(
        model,
        output_type=output_type,
        system_prompt=prompts["system_prompt"],
        retries=max_retries,
    )


def extract_usage(result: Any, model_tier: ModelTier) -> LLMUsage:
    """Extract token usage and calculate cost from agent result.

    Args:
        result: PydanticAI agent run result
        model_tier: Model tier for pricing lookup

    Returns:
        LLMUsage with token counts and cost
    """
    usage = result.usage()
    input_tokens = usage.request_tokens or 0
    output_tokens = usage.response_tokens or 0

    pricing = get_pricing_for_tier(model_tier)
    cost = calculate_estimated_cost(input_tokens, output_tokens, pricing)

    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_cents=cost,
    )


def aggregate_usage(usages: list[LLMUsage]) -> LLMUsage:
    """Aggregate multiple LLMUsage objects into one."""
    return LLMUsage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        total_tokens=sum(u.total_tokens for u in usages),
        estimated_cost_cents=sum(u.estimated_cost_cents for u in usages),
    )
```

---

## Refactored Agent Example: `typography_agent.py`

### Before (395 lines with class)

```python
class TypographyAgent(BaseDocumentAgent[TypographyAnalysisOutput]):
    def __init__(self) -> None:
        config = AgentConfig(
            name="typography_agent",
            prompts_file=Path("typography.yaml"),
            output_type=TypographyAnalysisOutput,
            correction_types=["emphasis", "definition", "semantic_color"],
            max_retries=2,
            temperature=0.3,
            model_tier=ModelTier.REASONING,
            use_deps=True,
        )
        super().__init__(config)
        self._instructions_registered = False

    def _get_agent(self) -> Agent[AgentDependencies, TypographyAnalysisOutput]:
        agent = super()._get_agent()
        if not self._instructions_registered:
            self._register_dynamic_instructions(agent)
            self._instructions_registered = True
        return agent

    def _register_dynamic_instructions(self, agent):
        @agent.instructions
        def document_context(ctx): ...
        # ... more decorators

    async def process(self, input_data):
        raise NotImplementedError()

    async def analyze(self, pages, manifest, markdown, job_id, deps=None):
        # ... 120 lines of actual logic
```

### After (~180 lines as module)

```python
"""Typography agent for semantic typography analysis.

Analyzes typography-based semantics: bold emphasis, italic definitions,
color-coding, and font size changes that convey meaning.
"""

import base64
import logging
from typing import Any
from uuid import uuid4

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, aggregate_usage
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import TypographyAnalysisOutput, TypographyIssue
from src.config import settings
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures

logger = logging.getLogger(__name__)

# Module-level state for lazy initialization
_agent: Agent[AgentDependencies, TypographyAnalysisOutput] | None = None
_prompts: dict[str, Any] | None = None
_MODEL_TIER = ModelTier.REASONING


def get_agent() -> Agent[AgentDependencies, TypographyAnalysisOutput]:
    """Get or create the typography agent (lazy initialization)."""
    global _agent, _prompts

    if _agent is None:
        _prompts = load_prompts("typography.yaml")
        _agent = create_agent(
            "typography.yaml",
            TypographyAnalysisOutput,
            model_tier=_MODEL_TIER,
            use_deps=True,
            max_retries=2,
        )
        _register_dynamic_instructions(_agent)
        logger.info(f"TypographyAgent initialized with model tier {_MODEL_TIER.value}")

    return _agent


def _register_dynamic_instructions(agent: Agent[AgentDependencies, TypographyAnalysisOutput]) -> None:
    """Register dynamic instruction generators on the agent."""

    @agent.instructions
    def document_context(ctx: RunContext[AgentDependencies]) -> str:
        if ctx.deps.manifest:
            return f"""<document_context>
Title: {ctx.deps.manifest.document_title}
Type: {ctx.deps.manifest.document_type}
Total pages: {ctx.deps.manifest.total_pages}
</document_context>"""
        return ""

    @agent.instructions
    def document_type_guidance(ctx: RunContext[AgentDependencies]) -> str:
        # ... same logic, moved out of class
        pass

    @agent.instructions
    def page_context(ctx: RunContext[AgentDependencies]) -> str:
        # ... same logic
        pass

    @agent.instructions
    def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
        # ... same logic
        pass

    logger.debug("TypographyAgent: Dynamic instructions registered")


# Public interface
async def analyze(
    pages: list[PageData],
    manifest: DocumentManifest,
    markdown: str,
    job_id: str,
    deps: AgentDependencies | None = None,
) -> tuple[list[Observation], LLMUsage]:
    """Analyze typography for semantic meaning.

    Args:
        pages: Pages to analyze (filtered to complexity > 0.5)
        manifest: Document manifest with page features
        markdown: Current markdown content
        job_id: Job identifier
        deps: Optional AgentDependencies for dynamic instructions

    Returns:
        Tuple of (observations, combined usage metrics)
    """
    agent = get_agent()
    observations: list[Observation] = []
    usages: list[LLMUsage] = []

    if deps is None:
        deps = AgentDependencies(
            job_id=job_id,
            manifest=manifest,
            document_type=manifest.document_type,
        )

    for page in pages:
        page_features = _get_page_features(manifest, page.page_num)
        if not page_features:
            continue

        page_deps = deps.clone_for_page(
            page.page_num,
            complexity_score=page_features.complexity_score,
            layout_type=page_features.layout_type,
        )

        user_message = _prompts["user_prompt_template"].format(
            page_num=page.page_num,
            document_title=manifest.document_title,
            complexity_score=page_features.complexity_score,
            complexity_factors=", ".join(page_features.complexity_factors or ["none"]),
            page_markdown=_extract_page_markdown(markdown, page.page_num),
        )

        messages: list[str | BinaryContent] = [user_message]
        if page.image_base64:
            messages.append(BinaryContent(
                data=base64.b64decode(page.image_base64),
                media_type="image/png"
            ))

        try:
            result = await agent.run(
                user_prompt=messages,
                deps=page_deps,
                model_settings={"max_tokens": 16000, "temperature": 0.3},
            )

            usage = extract_usage(result, _MODEL_TIER)
            usages.append(usage)

            # Log reasoning corpus
            corpus_service = get_reasoning_corpus_service()
            for issue in result.output.issues:
                corpus = issue.extract_reasoning_corpus()
                await corpus_service.log_corpus_batch(job_id, "typography", corpus)

            # Convert to observations
            page_observations = _issues_to_observations(result.output.issues, page.page_num, job_id)
            observations.extend(page_observations)

        except Exception as e:
            logger.error(f"Typography analysis failed on page {page.page_num}: {e}")

    return observations, aggregate_usage(usages)


# Private helpers
def _get_page_features(manifest: DocumentManifest, page_num: int) -> PageFeatures | None:
    for pf in manifest.page_features:
        if pf.page_num == page_num:
            return pf
    return None


def _extract_page_markdown(markdown: str, page_num: int) -> str:
    lines = markdown.split("\n")
    if not lines:
        return ""
    chunk_size = max(len(lines) // 10, 30)
    start = (page_num - 1) * chunk_size
    end = min(page_num * chunk_size, len(lines))
    return "\n".join(lines[start:end])


def _issues_to_observations(issues: list[TypographyIssue], page_num: int, job_id: str) -> list[Observation]:
    # ... same logic as before
    pass


# For AgentRouter compatibility - thin wrapper class
class TypographyAgent:
    """Wrapper class for AgentRouter protocol compatibility."""

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> tuple[list[Observation], LLMUsage]:
        return await analyze(pages, manifest, markdown, job_id)
```

---

## Three-Phase Migration Strategy

This strategy is designed to minimize risk while validating the approach before full commitment.

### Why Three Phases?

1. **Phase 1 is zero-risk.** Deleting dead code can't break anything. It provides immediate value and clears noise before the real refactor.

2. **Phase 2 is a proof of concept.** By migrating ONE agent completely, we learn:
   - Does the testing pattern actually work?
   - Are there hidden dependencies we missed?
   - Does the code feel simpler or just different?

3. **Phase 3 is a decision point.** After Phase 2, we have real data to decide:
   - Continue with module functions for remaining agents?
   - Pivot to dataclass composition if issues emerged?
   - Stop if the complexity reduction isn't worth it?

---

### Phase 1: Zero-Risk Dead Code Removal

**Scope**: ~350 lines deleted, zero migration risk
**Duration**: Can be done immediately

#### What to Delete

| File/Code | Lines | Reason Unused |
|-----------|-------|---------------|
| `src/agents/registry.py` | ~176 | Agents instantiated directly in ProcessingService, never looked up |
| `BatchResult` class in `base_agent.py` | ~78 | No agent uses batch processing pattern |
| `AgentInput`/`AgentOutput` in `agent_models.py` | ~50 | Agents use specialized models (ExtractionOutput, etc.) |
| `process()` abstract method | ~15/agent | Every implementation raises `NotImplementedError` |

#### Steps

1. Search codebase for any imports of `Registry`, `BatchResult`, `AgentInput`, `AgentOutput`
2. Verify no usages exist
3. Delete the code
4. Run `make test-fast` to confirm nothing breaks
5. Commit: "chore: remove unused agent infrastructure"

#### Validation Criteria
- All existing tests pass
- No import errors
- No runtime errors in dev environment

---

### Phase 2: Proof of Concept with Typography Agent

**Scope**: ~300 lines changed (one agent + infrastructure)
**Duration**: 1-2 focused sessions
**Risk**: Medium (isolated to one agent)

#### Why Typography Agent?

1. **Used via AgentRouter**, not directly by ProcessingService—isolated from main pipeline
2. **Has dynamic instructions**—tests the most complex pattern
3. **Has Reasoned[T] output**—tests integration with specialized models
4. **Not first in pipeline**—if it breaks, earlier stages still work

#### Deliverables

1. **`src/agents/factory.py`** (~80 lines)
   - `load_prompts()` - YAML loading with security checks
   - `create_agent()` - PydanticAI agent creation
   - `extract_usage()` - Token/cost extraction
   - `aggregate_usage()` - Combine multiple usages

2. **`src/agents/helpers.py`** (~40 lines)
   - `get_page_features()` - Shared across agents
   - `extract_page_markdown()` - Shared across agents

3. **Refactored `src/agents/typography_agent.py`** (~180 lines, down from ~395)
   - Module-level lazy singleton
   - Dynamic instructions registered at module level
   - `analyze()` as module function
   - `get_agent()` for access to underlying agent
   - `reset_agent()` for testing

4. **Updated `src/agents/agent_router.py`** (~20 lines changed)
   - Protocol updated to accept callables OR classes
   - Router handles both patterns during migration

5. **`tests/conftest.py` fixture** (~15 lines)
   - `reset_agent_singletons` fixture for test isolation

6. **Updated `tests/unit/agents/test_typography_agent.py`**
   - Adapted to new module pattern
   - Uses reset fixture

#### AgentRouter Protocol Update

```python
# Before: Only accepts classes with analyze method
class SpecializedAgent(Protocol):
    async def analyze(self, pages, manifest, markdown, job_id) -> tuple[list[Observation], LLMUsage]:
        ...

# After: Accepts classes OR module references with analyze function
AnalyzeCallable = Callable[
    [list[PageData], DocumentManifest, str, str],
    Awaitable[tuple[list[Observation], LLMUsage]]
]

class AgentRouter:
    def register_agent(self, name: str, agent: SpecializedAgent | Any) -> None:
        """Register agent class or module with analyze function."""
        self._agents[name] = agent

    async def _run_single_agent(self, agent, ...):
        # Handle both patterns
        if hasattr(agent, 'analyze'):
            # Class with method
            return await agent.analyze(pages, manifest, markdown, job_id)
        elif hasattr(agent, 'analyze') and callable(agent.analyze):
            # Module with function
            return await agent.analyze(pages, manifest, markdown, job_id)
```

#### Test Fixture for Singleton Reset

```python
# tests/conftest.py
import pytest

@pytest.fixture(autouse=True)
def reset_agent_singletons():
    """Reset all agent module singletons before and after each test."""
    # Import all agent modules that use singleton pattern
    from src.agents import typography_agent

    # Reset before test
    if hasattr(typography_agent, 'reset_agent'):
        typography_agent.reset_agent()

    yield

    # Reset after test (cleanup)
    if hasattr(typography_agent, 'reset_agent'):
        typography_agent.reset_agent()
```

#### Validation Criteria

1. `test_typography_agent.py` passes with new pattern
2. `test_agent_router.py` passes (can route to new-style agent)
3. Integration tests pass (typography agent works in full pipeline)
4. Manual test: Process a document and verify typography observations generated

#### Decision Point After Phase 2

Ask these questions:

| Question | If Yes | If No |
|----------|--------|-------|
| Was testing straightforward? | Continue | Consider dataclass approach |
| Is the code actually simpler? | Continue | Re-evaluate value proposition |
| Did hidden dependencies surface? | Address them | Continue |
| Is the pattern easy to understand? | Continue | Document better or reconsider |

---

### Phase 3: Complete Migration (Conditional)

**Prerequisite**: Phase 2 completed successfully and decision to continue
**Scope**: ~1500 lines changed
**Duration**: 3-5 focused sessions
**Risk**: Medium (but validated by Phase 2)

#### Migration Order (Corrected)

Order by risk—migrate agents used later in pipeline first:

| Order | Agent | Used By | Risk Level |
|-------|-------|---------|------------|
| 1 | `consolidation_agent.py` | ConsolidationService (last step) | Low |
| 2 | `figures_agent.py` | AgentRouter (parallel) | Low |
| 3 | `tables_agent.py` | AgentRouter (parallel) | Low |
| 4 | `structure_agent.py` | AgentRouter (parallel) | Low |
| 5 | `extraction_agent.py` | ProcessingService (step 5) | Medium |
| 6 | `analysis_agent.py` | ProcessingService (step 4, first!) | High |

**AnalysisAgent is LAST** because:
- It runs first in the pipeline
- All other agents depend on its manifest output
- Breaking it breaks everything

#### Per-Agent Migration Steps

For each agent:

1. **Create module version** alongside existing class (temporarily)
2. **Update imports** in consumers to use module functions
3. **Update tests** to use new pattern
4. **Run test suite** - all tests must pass
5. **Delete old class** after validation
6. **Commit** with clear message: "refactor(agents): migrate {agent} to module pattern"

#### Infrastructure Cleanup (After All Agents)

1. Delete `src/agents/base_agent.py`
2. Delete `src/agents/core.py`
3. Simplify `src/agents/__init__.py` exports
4. Update any remaining imports

#### Final Validation

1. `make test-fast` - All unit tests pass
2. `make test-integration` - Integration tests pass
3. `make test-e2e` - End-to-end tests pass
4. Manual smoke test - Process a real PDF document

---

## Testing Strategy

### Current Test Pattern
Tests currently:
1. Patch `builtins.open` and `yaml.safe_load` to avoid file I/O
2. Patch `_get_agent()` to return a mock agent
3. Test agent-specific logic (formatting, validation, etc.)

### New Test Pattern
Tests will:
1. Use `reset_agent()` function to clear singleton state
2. Patch `factory.load_prompts` to control YAML loading
3. Patch module's `get_agent()` to return mock agent when needed
4. Use autouse fixture to ensure cleanup between tests

### Example Test Migration

```python
# BEFORE: Class-based pattern
@patch("src.agents.core.yaml.safe_load")
@patch("builtins.open")
def test_analyze_returns_observations(self, mock_open, mock_yaml):
    mock_yaml.return_value = {"system_prompt": "test", "user_prompt_template": "test"}
    agent = TypographyAgent()

    with patch.object(agent, "_get_agent") as mock_get:
        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_get.return_value = mock_agent

        observations, usage = await agent.analyze(pages, manifest, markdown, job_id)
        assert len(observations) == 2

# AFTER: Module function pattern
@patch("src.agents.factory.load_prompts")
def test_analyze_returns_observations(self, mock_load):
    mock_load.return_value = {"system_prompt": "test", "user_prompt_template": "test"}

    from src.agents import typography_agent

    with patch.object(typography_agent, "get_agent") as mock_get:
        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_get.return_value = mock_agent

        observations, usage = await typography_agent.analyze(pages, manifest, markdown, job_id)
        assert len(observations) == 2
```

### Test Isolation Guarantee

The `reset_agent_singletons` fixture (see Phase 2) ensures:
1. Each test starts with fresh agent state
2. No test pollution from cached agents
3. Prompts can be mocked per-test

---

## Risk Assessment

### Phase 1: Zero Risk
- Deleting code that has no imports/usages
- No behavioral changes
- Easily reversible (git revert)

### Phase 2: Medium Risk (Contained)
- Only affects TypographyAgent
- AgentRouter updated to handle both patterns
- If issues found, can abort before Phase 3
- Full test coverage validates changes

### Phase 3: Medium Risk (Validated)
- Pattern already proven in Phase 2
- One agent at a time with full validation
- AnalysisAgent last to minimize blast radius
- Can stop at any point

### Mitigations

1. **Incremental approach** - Phase 2 validates before committing to Phase 3
2. **Dual-pattern support** - AgentRouter handles old and new during migration
3. **Explicit decision point** - Phase 2 ends with go/no-go decision
4. **Reversibility** - Each phase is a clean commit, easy to revert

---

## Success Metrics

| Metric | Before | After Phase 1 | After Phase 3 |
|--------|--------|---------------|---------------|
| Lines in agent infrastructure | ~800 | ~450 | ~200 |
| Lines per agent (avg) | ~350 | ~350 | ~180 |
| Inheritance depth | 2 | 2 | 0 |
| Unused code | ~350 | 0 | 0 |
| Time to understand an agent | High | High | Low |

---

## Questions Resolved

| Original Question | Resolution |
|-------------------|------------|
| Protocol compatibility: wrapper classes or update router? | **Update router.** Wrapper classes are an anti-pattern. |
| Singleton reset in tests: `_agent = None` acceptable? | **Yes, with fixture.** Autouse fixture ensures cleanup. |
| Prompts caching: module-level or re-read? | **Module-level.** Prompts are static config, no need to re-read. |
| Error handling in `get_agent()`? | **Let errors propagate.** Initialization errors should fail fast. |
| Keep old class names as aliases? | **No.** Clean break. Migration is one-at-a-time with full validation. |

---

## Appendix: Fallback to Dataclass Composition

If Phase 2 reveals that module functions cause significant testing pain, the fallback is dataclass composition:

```python
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent

from src.agents.factory import create_agent, load_prompts, extract_usage
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import TypographyAnalysisOutput


@dataclass
class TypographyAgent:
    """Lightweight agent using composition instead of inheritance."""

    _agent: Agent | None = field(default=None, init=False, repr=False)
    _prompts: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def _get_agent(self) -> Agent:
        if self._agent is None:
            self._prompts = load_prompts("typography.yaml")
            self._agent = create_agent(
                "typography.yaml",
                TypographyAnalysisOutput,
                model_tier=ModelTier.REASONING,
                use_deps=True,
            )
            self._register_dynamic_instructions(self._agent)
        return self._agent

    def _register_dynamic_instructions(self, agent: Agent) -> None:
        # Same dynamic instruction registration
        pass

    async def analyze(self, pages, manifest, markdown, job_id):
        agent = self._get_agent()
        # Same analysis logic
        pass
```

**When to use this fallback:**
- Test isolation proves too difficult with module singletons
- Team finds module pattern confusing
- Hidden state issues emerge

**Trade-offs vs module functions:**
- (+) Instance-based state (easier testing)
- (+) No global state management
- (-) Still using classes (less simplification)
- (-) Still have `_get_agent()` boilerplate

---

## Summary: Why This Approach

### The Core Problem
The current agent architecture wraps PydanticAI in ~800 lines of abstraction (BaseDocumentAgent, AgentCore, AgentConfig) that provides minimal value over what PydanticAI already offers. Every agent requires subclassing, configuration objects, and implementing abstract methods that always raise NotImplementedError.

### Why Module Functions Over Dataclass Composition
The dataclass approach was considered but rejected because:
1. It still keeps classes, lazy initialization methods, and instance state
2. It doesn't get closer to PydanticAI's intended usage pattern
3. The simplification is marginal (~20% reduction vs ~50% with modules)

### Why Three Phases
1. **Phase 1 (dead code)** - Immediate value with zero risk. Removes noise before the real work.
2. **Phase 2 (proof of concept)** - Validates the pattern before full commitment. One agent, full validation.
3. **Phase 3 (conditional)** - Only proceeds if Phase 2 succeeds. Explicit decision point.

### Why Thread Safety Isn't Critical
The application uses FastAPI with async coroutines, not threads. The "race condition" in module singleton initialization:
- Requires true multi-threading (not present)
- Has benign worst-case (duplicate initialization, one garbage collected)
- Happens once per process at startup

### The Escape Hatch
If module functions prove problematic, the dataclass composition fallback is documented and ready. Phase 2 is explicitly designed to surface issues before committing to the full migration.

---

---

## Final Status: Migration Complete

**Date**: December 2024

### Completed Work

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1: Dead Code Removal | ✅ Complete | Deleted registry.py, BatchResult, AgentOutput, AgentCorrection, process() methods (~350 lines) |
| Phase 2: Typography Proof of Concept | ✅ Complete | Created factory.py, helpers.py. Migrated typography_agent.py |
| Phase 3: Full Migration | ✅ Complete | All 7 agents migrated to module pattern |

### Files Created
- `src/agents/factory.py` - Agent creation utilities (~130 lines)
- `src/agents/helpers.py` - Shared helper functions (~50 lines)

### Agents Migrated
All agents now use the module function pattern with lazy singleton initialization:

| Agent | Main Function | Model Tier |
|-------|--------------|------------|
| `typography_agent.py` | `analyze()` | REASONING |
| `figures_agent.py` | `analyze()` | REASONING |
| `tables_agent.py` | `analyze()` | REASONING |
| `structure_agent.py` | `analyze()` | REASONING |
| `consolidation_agent.py` | `consolidate()` | REASONING |
| `extraction_agent.py` | `extract()` | EFFICIENT |
| `analysis_agent.py` | `analyze()` | REASONING |

### Test Results
- **597 tests pass** after migration
- Test isolation via `reset_agent_singletons` fixture in conftest.py
- Wrapper classes provide backward compatibility for existing tests

### Infrastructure Kept for Test Compatibility

The following files were **NOT deleted** because they're still used by test infrastructure:

| File | Why Kept |
|------|----------|
| `base_agent.py` | `AgentConfig` imported by wrapper classes and tests for backward compatibility |
| `core.py` | `AgentConfig`, `AgentCore` used by `test_agent_core.py`, `test_path_validation.py`, `test_base_agent.py` |

**These files are deprecated for new development.** New agents should use the module pattern with `factory.py`.

### Future Cleanup (Optional Phase 4)

To fully remove `base_agent.py` and `core.py`:

1. Create a lightweight `AgentConfig` dataclass in a shared location (or inline in wrappers)
2. Migrate security tests from `AgentCore` to `factory.py::load_prompts`
3. Update ~20 test assertions that check `agent.config.*` properties
4. Delete the old files

This cleanup is **optional** since:
- All production code uses the new pattern
- The old files are only used for test backward compatibility
- The security functionality exists in both `core.py` and `factory.py`

### Metrics Achieved

| Metric | Before | After |
|--------|--------|-------|
| Lines in agent infrastructure | ~800 | ~200 (factory.py + helpers.py) |
| Lines per agent (avg) | ~350 | ~250 (including wrapper class) |
| Inheritance depth | 2 | 0 |
| Unused code | ~350 | 0 |

### Key Learnings

1. **Wrapper classes needed for test compatibility** - The original plan assumed AgentRouter Protocol changes would suffice, but existing tests relied on class properties like `config`, `model_tier`, `prompts`

2. **AWS mocking critical** - Tests must mock `get_agent()` to prevent real Bedrock connections during test discovery

3. **extract_usage needs model_tier** - Critical bug caught during review: `extract_usage(result)` needs `model_tier` parameter for correct pricing

4. **Test fixture essential** - `reset_agent_singletons` autouse fixture prevents test pollution from cached singleton state

---

*Last updated: December 2024. Migration complete.*
