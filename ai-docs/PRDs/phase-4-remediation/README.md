# Phase 4: Accessibility Remediation Pipeline

> **Reference**: [Full Architecture Documentation](../../../docs/features/accessibility-remediation-pipeline.md)
> **GitHub Issues**: [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24)

## Overview

Phase 4 implements a comprehensive accessibility remediation pipeline that transforms the current extraction-only approach into a multi-phase system with human-in-the-loop review.

### Key Design Principles

1. **Observation-first** - AI observes discrepancies, doesn't claim violations
2. **Smart model routing** - Sonnet for analysis, Haiku for transcription
3. **Human-in-the-loop** - All changes require approval before application
4. **Surgical edits** - Search-replace, not full rewrites
5. **Graceful degradation** - Low-confidence items flagged but don't block

## PRD Summary

### Core Pipeline PRDs

| PRD | Title | Effort | Dependencies |
|-----|-------|--------|--------------|
| [PRD-011](PRD-011-remediation-data-models.md) | Remediation Data Models | 2 days | PRD-002 |
| [PRD-012](PRD-012-analysis-agent.md) | Analysis Agent (Sonnet) | 3 days | PRD-011 |
| [PRD-013](PRD-013-extraction-agent.md) | Extraction Agent (Haiku) | 2 days | PRD-012 |
| [PRD-014](PRD-014-specialized-agents.md) | Specialized Agents | 5 days | PRD-013 |
| [PRD-015](PRD-015-consolidation-service.md) | Consolidation Service | 3 days | PRD-014 |
| [PRD-016](PRD-016-review-api.md) | Review API & Workflow | 4 days | PRD-015 |
| [PRD-017](PRD-017-application-service.md) | Application Service | 2 days | PRD-016 |

**Core Pipeline Effort**: 21 days

### Infrastructure Enhancement PRDs

| PRD | Title | Effort | Dependencies | Source |
|-----|-------|--------|--------------|--------|
| [PRD-018](PRD-018-agent-infrastructure-consolidation.md) | Agent Infrastructure Consolidation | 2 days | PRD-014 | Best Practices Review |
| [PRD-019](PRD-019-security-hardening.md) | Security Hardening | 1 day | PRD-018 | Security Review |
| [PRD-020](PRD-020-hybrid-confidence-reasoning.md) | Hybrid Confidence & Reasoning | 3 days | PRD-018, PRD-014 | Refactoring Plan |
| [PRD-021](PRD-021-dynamic-agent-instructions.md) | Dynamic Agent Instructions | 2 days | PRD-018, PRD-020 | Refactoring Plan |

**Infrastructure Enhancement Effort**: 8 days

**Total Estimated Effort**: 29 days

## Dependency Graph

```
PRD-002 (Shared Data Models)
    │
    ▼
PRD-011 (Remediation Data Models)
    │
    ▼
PRD-012 (Analysis Agent - Sonnet)
    │
    ▼
PRD-013 (Extraction Agent - Haiku)
    │
    ▼
PRD-014 (Specialized Agents)
    │   ├── FiguresAgent (#24)
    │   ├── TablesAgent (#24)
    │   ├── StructureAgent (#23)
    │   └── TypographyAgent (#23)
    │
    ├───────────────────────────────────┐
    │                                   │
    ▼                                   ▼
PRD-015 (Consolidation Service)    PRD-018 (Infrastructure Consolidation)
    │                                   │
    ▼                                   ├──► PRD-019 (Security Hardening)
PRD-016 (Review API)                    │
    │                                   └──► PRD-020 (Hybrid Confidence)
    ▼                                            │
PRD-017 (Application Service)                    ▼
                                        PRD-021 (Dynamic Instructions)
```

### Infrastructure Enhancement Track

The infrastructure PRDs (018-021) can be implemented in parallel with
or after the core pipeline PRDs (015-017). They improve quality,
security, and maintainability without changing the core workflow.

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REMEDIATION PIPELINE                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│  │   PDF       │ ──▶ │  Analysis   │ ──▶ │  Extraction │                   │
│  │   Upload    │     │  (Sonnet)   │     │  (Haiku)    │                   │
│  └─────────────┘     │  PRD-012    │     │  PRD-013    │                   │
│                      └─────────────┘     └─────────────┘                   │
│                             │                   │                           │
│                             ▼                   ▼                           │
│                      DocumentManifest      Markdown v0                      │
│                      + Initial Obs.                                         │
│                             │                   │                           │
│                             └────────┬──────────┘                           │
│                                      ▼                                      │
│                      ┌─────────────────────────────┐                       │
│                      │   Specialized Agents        │                       │
│                      │   (Sonnet) PRD-014          │                       │
│                      │   ├── Figures               │                       │
│                      │   ├── Tables                │                       │
│                      │   ├── Structure             │                       │
│                      │   └── Typography            │                       │
│                      └─────────────────────────────┘                       │
│                                      │                                      │
│                                      ▼                                      │
│                               Observations                                  │
│                                      │                                      │
│                                      ▼                                      │
│                      ┌─────────────────────────────┐                       │
│                      │   Consolidation             │                       │
│                      │   (Sonnet) PRD-015          │                       │
│                      │   Observations → Proposals   │                       │
│                      └─────────────────────────────┘                       │
│                                      │                                      │
│                                      ▼                                      │
│                               Proposals                                     │
│                                      │                                      │
│                                      ▼                                      │
│                      ┌─────────────────────────────┐                       │
│                      │   Human Review              │                       │
│                      │   PRD-016                   │                       │
│                      │   Accept / Reject / Edit    │                       │
│                      └─────────────────────────────┘                       │
│                                      │                                      │
│                                      ▼                                      │
│                      ┌─────────────────────────────┐                       │
│                      │   Application               │                       │
│                      │   PRD-017                   │                       │
│                      │   Search-Replace Edits      │                       │
│                      └─────────────────────────────┘                       │
│                                      │                                      │
│                                      ▼                                      │
│                           Remediated Document                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Cost Model

| Phase | Model | Input Tokens | Output Tokens | Est. Cost |
|-------|-------|--------------|---------------|-----------|
| Analysis | Sonnet 4.5 | ~50K | ~2K | $0.18 |
| Extraction | Haiku 4.5 | ~52K | ~8K | $0.07 |
| Specialized | Sonnet 4.5 | ~15K | ~1K | $0.06 |
| Consolidation | Sonnet 4.5 | ~10K | ~1.5K | $0.05 |
| **Total** | | | | **~$0.36** |

vs. Current (Haiku only): ~$0.15

**Tradeoff**: 2.4x cost increase for significantly better analysis quality and human review workflow.

## New Data Models (PRD-011)

```python
# Core models
DocumentManifest    # Analysis output with page features
Observation         # Discrepancy between visual and markup
Proposal            # Actionable edit with search-replace diff
RemediationProgress # Job substatus tracking
```

## New API Endpoints (PRD-016)

```
GET  /api/documents/{job_id}/review          # Review summary
GET  /api/documents/{job_id}/observations    # List observations
GET  /api/documents/{job_id}/proposals       # List proposals
POST /api/documents/{job_id}/proposals/{id}/approve
POST /api/documents/{job_id}/proposals/{id}/reject
POST /api/documents/{job_id}/proposals/{id}/edit
POST /api/documents/{job_id}/proposals/batch-approve
POST /api/documents/{job_id}/apply           # Trigger application
```

## Job State Machine

```
processing
├── substatus: analyzing      (PRD-012)
├── substatus: extracting     (PRD-013)
├── substatus: specializing   (PRD-014)
├── substatus: consolidating  (PRD-015)
├── substatus: awaiting_review (PRD-016)
└── substatus: applying       (PRD-017)
    │
    ▼
completed (with metrics)
```

## Files Created by Phase 4

### Core Pipeline Files (PRD-011 through PRD-017)

```
src/
├── agents/
│   ├── model_tiers.py
│   ├── analysis_agent.py
│   ├── extraction_agent.py
│   ├── agent_router.py
│   ├── specialized_agent_base.py
│   ├── figures_agent.py
│   ├── tables_agent.py
│   ├── structure_agent.py
│   ├── typography_agent.py
│   └── consolidation_agent.py
├── services/
│   ├── remediation_storage_service.py
│   ├── consolidation_service.py
│   └── application_service.py
├── api/
│   └── review.py
├── shared/models/
│   ├── remediation.py
│   ├── observation.py
│   ├── proposal.py
│   └── remediation_progress.py
└── utils/
    └── diff_utils.py

config/agents/
├── analysis.yaml
├── extraction.yaml
├── figures.yaml
├── tables.yaml
├── structure.yaml
├── typography.yaml
└── consolidation.yaml
```

### Infrastructure Enhancement Files (PRD-018 through PRD-021)

```
src/
├── agents/
│   ├── core.py                    # Enhanced AgentCore (PRD-018)
│   ├── base_agent.py              # Updated with composition (PRD-018)
│   └── dependencies.py            # AgentDependencies (PRD-021)
├── shared/models/
│   ├── reasoned.py                # Reasoned[T] wrapper (exists)
│   └── quality_signals.py         # QualitySignals (PRD-020)
├── services/
│   └── reasoning_corpus_service.py # Reasoning capture (PRD-020)
└── utils/
    ├── prompt_sanitizer.py        # Security sanitization (PRD-019)
    └── confidence.py              # Hybrid confidence (PRD-020)

tests/
├── unit/agents/
│   ├── test_path_validation.py    # Security tests (PRD-019)
│   ├── test_dependencies.py       # Deps tests (PRD-021)
│   └── test_dynamic_instructions.py
├── unit/utils/
│   ├── test_prompt_sanitizer.py   # Sanitization tests (PRD-019)
│   └── test_confidence.py         # Confidence tests (PRD-020)
└── unit/models/
    └── test_quality_signals.py    # QualitySignals tests (PRD-020)
```

## Implementation Order

### Milestone 1: Foundation (PRD-011)
- Data models for observations, proposals, manifest
- S3 storage schema
- Job substatus support

### Milestone 2: Analysis + Extraction (PRD-012, PRD-013)
- Split current agent into Sonnet analysis + Haiku extraction
- DocumentManifest generation
- Model tier switching

### Milestone 3: Specialized Agents (PRD-014)
- Implement four specialized agents
- Agent routing based on manifest
- Per-page processing

### Milestone 4: Consolidation (PRD-015)
- Observations → Proposals transformation
- Search-replace diff generation
- Manual routing

### Milestone 5: Review + Application (PRD-016, PRD-017)
- Review API endpoints
- Human workflow support
- Edit application
- Job completion

### Milestone 6: Infrastructure Enhancements (PRD-018 through PRD-021)

Can be implemented in parallel with Milestones 4-5 or after completion.

**PRD-018: Infrastructure Consolidation**
- Eliminate code duplication across agents
- Standardize on AgentCore composition pattern
- Add batch error reporting

**PRD-019: Security Hardening**
- Path traversal prevention in prompt loading
- Prompt injection mitigation
- Confidence threshold bounds
- Structured security logging

**PRD-020: Hybrid Confidence & Reasoning**
- QualitySignals model for programmatic confidence
- calculate_confidence() hybrid function
- Apply Reasoned[T] to complex determinations
- Reasoning corpus service for analysis

**PRD-021: Dynamic Agent Instructions**
- AgentDependencies for runtime context
- Manifest-guided extraction instructions
- Failure recovery instructions
- Enhanced field descriptions for LLM guidance

## Open Questions

1. **WebSocket for review?** - Real-time updates during review?
2. **Concurrent reviewers?** - Optimistic locking needed?
3. **Rollback support?** - Undo applied proposals?
4. **Learning from rejections?** - Feed back to improve agents?
5. **Integration with Canvas LMS?** - How does review fit instructor workflow?
