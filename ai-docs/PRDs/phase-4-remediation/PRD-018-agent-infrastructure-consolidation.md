# PRD-018: Agent Infrastructure Consolidation

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation (Infrastructure)
**Estimated Effort**: 2 days
**Dependencies**: PRD-012 (Analysis Agent), PRD-013 (Extraction Agent), PRD-014 (Specialized Agents)
**Source**: Best Practices Review (December 2024)

## Problem Statement

The agent infrastructure has accumulated technical debt through organic growth:

1. **Triplication of Logic**: Prompt loading, token usage extraction, and model tier management are implemented three separate times across `AgentCore`, `BaseDocumentAgent`, and individual agents.

2. **Inconsistent Inheritance**: `AgentCore` exists but isn't used by `BaseDocumentAgent`. `AnalysisAgent` and `ExtractionAgent` bypass both and reimplement everything.

3. **Initialization Pattern Variance**: Some agents take config dataclasses, others create configs internally.

4. **Swallowed Batch Errors**: When batch processing fails for some pages, callers have no way to know which pages failed.

### Current Architecture (Problematic)

```
AgentCore (standalone utility - unused)
    │
    ├─× Not inherited or composed

BaseDocumentAgent (abstract base)
    │
    ├── FiguresAgent
    ├── TablesAgent
    ├── StructureAgent
    └── TypographyAgent

AnalysisAgent (standalone - duplicates everything)
ExtractionAgent (standalone - duplicates everything)
```

### Target Architecture

```
AgentCore (composition)
    │
    └── used by ──┐
                  │
BaseDocumentAgent (inheritance)
    │
    ├── AnalysisAgent
    ├── ExtractionAgent
    ├── FiguresAgent
    ├── TablesAgent
    ├── StructureAgent
    ├── TypographyAgent
    └── ConsolidationAgent
```

## Success Criteria

- [ ] Single implementation of prompt loading (in `AgentCore`)
- [ ] Single implementation of token usage extraction (in `AgentCore`)
- [ ] Single implementation of model tier management (in `AgentCore`)
- [ ] `BaseDocumentAgent` uses `AgentCore` via composition
- [ ] `AnalysisAgent` and `ExtractionAgent` inherit from `BaseDocumentAgent`
- [ ] Consistent initialization pattern across all agents
- [ ] Batch processing surfaces partial failures
- [ ] All 222 existing agent tests pass
- [ ] No code duplication across agent implementations

## Technical Requirements

### 1. Update AgentCore Interface

```python
# src/agents/core.py

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.result import RunResult

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier
from src.shared.models.processing import LLMUsage

TOutput = TypeVar("TOutput", bound=BaseModel)


@dataclass
class AgentConfig:
    """Universal configuration for all agents."""

    name: str
    prompts_file: Path
    model_tier: ModelTier
    output_type: type[BaseModel]
    max_retries: int = 2
    temperature: float = 0.3
    max_tokens: int = 16000


class AgentCore:
    """Shared infrastructure for all agents.

    Single source of truth for:
    - YAML prompt loading
    - Model tier and ID management
    - Token usage extraction and cost calculation
    - PydanticAI agent creation
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.model_tier = config.model_tier
        self.model_id = MODEL_TIER_MAP[config.model_tier]
        self.prompts = self._load_prompts(config.prompts_file)
        self._agent: Agent | None = None

    def _load_prompts(self, prompts_file: Path) -> dict[str, Any]:
        """Load prompts from YAML file.

        Raises:
            FileNotFoundError: If prompts file does not exist (fail fast)
            ValueError: If resolved path escapes base directory
        """
        import yaml

        if not prompts_file.is_absolute():
            base_dir = Path(settings.agent_prompts_dir).resolve()
            prompts_file = (base_dir / prompts_file).resolve()

            # Security: Prevent path traversal
            if not str(prompts_file).startswith(str(base_dir)):
                raise ValueError(f"Invalid prompts file path: {prompts_file}")

        with open(prompts_file) as f:
            return yaml.safe_load(f)

    def get_agent(self, output_type: type[TOutput] | None = None) -> Agent:
        """Get or create the PydanticAI agent."""
        if self._agent is None:
            model = BedrockConverseModel(model_name=self.model_id)
            self._agent = Agent(
                model,
                output_type=output_type or self.config.output_type,
                system_prompt=self.prompts["system_prompt"],
                retries=self.config.max_retries,
            )
        return self._agent

    def create_llm_usage(self, result: RunResult[Any]) -> LLMUsage:
        """Extract token usage and calculate cost from agent result.

        Maps PydanticAI's request_tokens/response_tokens to
        our LLMUsage model's input_tokens/output_tokens fields.
        """
        usage = result.usage()
        input_tokens = usage.request_tokens or 0
        output_tokens = usage.response_tokens or 0
        total_tokens = input_tokens + output_tokens

        pricing = get_pricing_for_tier(self.model_tier)
        cost = calculate_estimated_cost(input_tokens, output_tokens, pricing)

        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=cost,
        )

    @staticmethod
    def aggregate_usage(usages: list[LLMUsage]) -> LLMUsage:
        """Aggregate multiple LLMUsage objects into one."""
        return LLMUsage(
            input_tokens=sum(u.input_tokens for u in usages),
            output_tokens=sum(u.output_tokens for u in usages),
            total_tokens=sum(u.total_tokens for u in usages),
            estimated_cost_cents=sum(u.estimated_cost_cents for u in usages),
        )
```

### 2. Update BaseDocumentAgent to Use AgentCore

```python
# src/agents/base_agent.py

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

from src.agents.core import AgentConfig, AgentCore
from src.shared.models.processing import LLMUsage

TOutput = TypeVar("TOutput", bound=BaseModel)


class BaseDocumentAgent(ABC, Generic[TOutput]):
    """Base class for all document processing agents.

    Uses AgentCore for shared infrastructure via composition.
    Subclasses implement domain-specific analysis logic.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._core = AgentCore(config)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def model_tier(self):
        return self._core.model_tier

    @property
    def model_id(self):
        return self._core.model_id

    @property
    def prompts(self):
        return self._core.prompts

    def _get_agent(self):
        """Get the PydanticAI agent from core."""
        return self._core.get_agent()

    def _create_usage(self, result) -> LLMUsage:
        """Create LLMUsage from agent result."""
        return self._core.create_llm_usage(result)

    @staticmethod
    def _aggregate_usage(usages: list[LLMUsage]) -> LLMUsage:
        """Aggregate multiple usages."""
        return AgentCore.aggregate_usage(usages)

    @abstractmethod
    async def analyze(self, *args, **kwargs):
        """Perform domain-specific analysis."""
        pass
```

### 3. Migrate AnalysisAgent to Inherit from BaseDocumentAgent

```python
# src/agents/analysis_agent.py

from pathlib import Path

from src.agents.base_agent import BaseDocumentAgent
from src.agents.core import AgentConfig
from src.agents.model_tiers import ModelTier


class AnalysisAgent(BaseDocumentAgent[AnalysisOutput]):
    """Document analysis agent using Sonnet for deep analysis."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        if config is None:
            config = AgentConfig(
                name="analysis_agent",
                prompts_file=Path("analysis.yaml"),
                model_tier=ModelTier.REASONING,
                output_type=AnalysisOutput,
            )
        super().__init__(config)

    async def analyze(
        self,
        pages: list[PageData],
        job_id: str,
    ) -> tuple[DocumentManifest, list[Observation], LLMUsage]:
        """Analyze document and return manifest with observations."""
        # Implementation using self._get_agent(), self._create_usage()
        ...
```

### 4. Add Batch Error Reporting

```python
# src/agents/base_agent.py

from dataclasses import dataclass, field


@dataclass
class BatchResult(Generic[TOutput]):
    """Result of batch processing with error tracking."""

    outputs: list[TOutput] = field(default_factory=list)
    usage: LLMUsage | None = None
    errors: list[tuple[int, str]] = field(default_factory=list)  # (page_num, error_msg)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def success_count(self) -> int:
        return len(self.outputs)

    @property
    def error_count(self) -> int:
        return len(self.errors)
```

### 5. Files to Modify

| File | Changes |
|------|---------|
| `src/agents/core.py` | Add `AgentConfig`, path validation, `aggregate_usage()`, `get_agent()` |
| `src/agents/base_agent.py` | Use `AgentCore` via composition, add `BatchResult` |
| `src/agents/analysis_agent.py` | Inherit from `BaseDocumentAgent`, remove duplicate code |
| `src/agents/extraction_agent.py` | Inherit from `BaseDocumentAgent`, remove duplicate code |
| `src/agents/figures_agent.py` | Update to use new base class interface |
| `src/agents/tables_agent.py` | Update to use new base class interface |
| `src/agents/structure_agent.py` | Update to use new base class interface |
| `src/agents/typography_agent.py` | Update to use new base class interface |
| `src/agents/consolidation_agent.py` | Update to use new base class interface |

## Acceptance Criteria

### 1. Code Deduplication
- [ ] Prompt loading implemented only in `AgentCore`
- [ ] Token usage extraction implemented only in `AgentCore`
- [ ] Model tier management implemented only in `AgentCore`
- [ ] No `yaml.safe_load()` calls outside `AgentCore`
- [ ] No manual token extraction outside `AgentCore`

### 2. Consistent Architecture
- [ ] All agents inherit from `BaseDocumentAgent`
- [ ] All agents use `AgentConfig` for initialization
- [ ] `BaseDocumentAgent` uses `AgentCore` via composition
- [ ] No standalone agent implementations

### 3. Error Handling
- [ ] Batch operations return `BatchResult` with errors
- [ ] Callers can detect and report partial failures
- [ ] Error messages include page numbers

### 4. Tests
- [ ] All 222 existing agent tests pass
- [ ] New tests for `AgentCore.aggregate_usage()`
- [ ] New tests for `BatchResult` error tracking
- [ ] New tests for path validation security

## Deliverables

### Files Modified
```
src/agents/
├── core.py                    # Enhanced with AgentConfig, path validation
├── base_agent.py              # Uses AgentCore, adds BatchResult
├── analysis_agent.py          # Inherits from BaseDocumentAgent
├── extraction_agent.py        # Inherits from BaseDocumentAgent
├── figures_agent.py           # Updated interface
├── tables_agent.py            # Updated interface
├── structure_agent.py         # Updated interface
├── typography_agent.py        # Updated interface
└── consolidation_agent.py     # Updated interface
```

### Files Deleted
```
# After migration, these constants/methods should be removed:
- ANALYSIS_SYSTEM_PROMPT constants
- EXTRACTION_SYSTEM_PROMPT constants
- Duplicate _load_prompts() implementations
- Duplicate token extraction code
```

## Technical Notes

### Migration Strategy

1. **Phase 1**: Update `AgentCore` with new interface (non-breaking)
2. **Phase 2**: Update `BaseDocumentAgent` to use composition (non-breaking)
3. **Phase 3**: Migrate specialized agents one at a time (tests verify each)
4. **Phase 4**: Migrate `AnalysisAgent` and `ExtractionAgent` (largest change)
5. **Phase 5**: Remove dead code and run full test suite

### Backward Compatibility

During migration, maintain backward compatibility by:
- Keeping existing method signatures on `BaseDocumentAgent`
- Using property accessors that delegate to `AgentCore`
- Deprecating direct access with warnings before removal

## Definition of Done

- [ ] Single implementation of shared functionality in `AgentCore`
- [ ] All agents inherit from `BaseDocumentAgent`
- [ ] Consistent `AgentConfig` initialization pattern
- [ ] Batch error reporting via `BatchResult`
- [ ] All 222 agent tests pass
- [ ] No code duplication across agents
- [ ] Documentation updated
