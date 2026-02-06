"""Tests for dossier threading through deps and orchestrator."""

from __future__ import annotations

import pytest
from src.agents.dossier import (
    PipelineDossier,
    PlanningSnapshot,
)
from src.agents.models import DocumentType


class TestDepsAcceptDossierNone:
    """Verify all deps dataclasses accept dossier=None (backward compat)."""

    def test_worker_deps_default_none(self):
        # WorkerDeps has required positional args; use a minimal mock
        from unittest.mock import MagicMock

        from src.agents.worker import WorkerDeps

        deps = WorkerDeps(
            job=MagicMock(),
            page_image=MagicMock(),
            element_bboxes={},
            page_width=612.0,
            current_markdown="# Test",
            pending_edits=[],
            validated_edits=[],
            dictionary=[],
        )
        assert deps.dossier is None

    def test_paragraph_agent_deps_default_none(self):
        from unittest.mock import MagicMock

        from src.agents.paragraph_agent import ParagraphAgentDeps

        deps = ParagraphAgentDeps(
            job=MagicMock(),
            page_image=MagicMock(),
            current_markdown="# Test",
        )
        assert deps.dossier is None

    def test_recovery_deps_default_none(self):
        from unittest.mock import MagicMock

        from src.agents.recovery import RecoveryDeps

        deps = RecoveryDeps(
            page_num=1,
            page_image=MagicMock(),
            current_markdown="# Test",
            issues=["test issue"],
            processing_history=["initial processing"],
        )
        assert deps.dossier is None

    def test_critic_deps_default_none(self):
        from unittest.mock import MagicMock

        from src.agents.critic import CriticDeps

        deps = CriticDeps(
            document_id="doc-1",
            merged_markdown="# Test",
            page_boundary_map=MagicMock(),
            page_images={},
            round_number=1,
        )
        assert deps.dossier is None

    def test_document_worker_deps_default_none(self):
        from unittest.mock import MagicMock

        from src.agents.document_worker import DocumentWorkerDeps

        deps = DocumentWorkerDeps(
            job=MagicMock(),
            current_markdown="# Test",
            page_images={},
            page_boundary_map=MagicMock(),
            ledger=MagicMock(),
        )
        assert deps.dossier is None


class TestDepsAcceptPopulatedDossier:
    """Verify all deps accept a populated dossier."""

    @pytest.fixture()
    def dossier(self) -> PipelineDossier:
        return PipelineDossier(
            document_id="doc-test",
            filename="test.pdf",
            planning=PlanningSnapshot(
                document_title="Test",
                total_pages=5,
            ),
        )

    def test_worker_deps_with_dossier(self, dossier):
        from unittest.mock import MagicMock

        from src.agents.worker import WorkerDeps

        deps = WorkerDeps(
            job=MagicMock(),
            page_image=MagicMock(),
            element_bboxes={},
            page_width=612.0,
            current_markdown="# Test",
            pending_edits=[],
            validated_edits=[],
            dictionary=[],
            dossier=dossier,
        )
        assert deps.dossier is not None
        assert deps.dossier.planning.document_title == "Test"

    def test_paragraph_agent_deps_with_dossier(self, dossier):
        from unittest.mock import MagicMock

        from src.agents.paragraph_agent import ParagraphAgentDeps

        deps = ParagraphAgentDeps(
            job=MagicMock(),
            page_image=MagicMock(),
            current_markdown="# Test",
            dossier=dossier,
        )
        assert deps.dossier is not None
        assert deps.dossier.document_id == "doc-test"

    def test_recovery_deps_with_dossier(self, dossier):
        from unittest.mock import MagicMock

        from src.agents.recovery import RecoveryDeps

        deps = RecoveryDeps(
            page_num=1,
            page_image=MagicMock(),
            current_markdown="# Test",
            issues=[],
            processing_history=[],
            dossier=dossier,
        )
        assert deps.dossier is not None

    def test_critic_deps_with_dossier(self, dossier):
        from unittest.mock import MagicMock

        from src.agents.critic import CriticDeps

        deps = CriticDeps(
            document_id="doc-1",
            merged_markdown="# Test",
            page_boundary_map=MagicMock(),
            page_images={},
            round_number=1,
            dossier=dossier,
        )
        assert deps.dossier is not None

    def test_document_worker_deps_with_dossier(self, dossier):
        from unittest.mock import MagicMock

        from src.agents.document_worker import DocumentWorkerDeps

        deps = DocumentWorkerDeps(
            job=MagicMock(),
            current_markdown="# Test",
            page_images={},
            page_boundary_map=MagicMock(),
            ledger=MagicMock(),
            dossier=dossier,
        )
        assert deps.dossier is not None


class TestPopulateExecutionSnapshot:
    """Test the _populate_execution_snapshot helper."""

    def test_populate_from_job_results(self):
        from src.agents.models import Job, JobContext, JobResult, JobType, Task, TaskType
        from src.agents.orchestrator import _populate_execution_snapshot

        dossier = PipelineDossier(document_id="doc", filename="f.pdf")

        # Create a minimal plan with jobs
        from src.agents.models import DocumentPlan, DocumentStructure

        job1 = Job(
            job_id="j1",
            job_type=JobType.CONTENT,
            page=1,
            tasks=[Task(task_type=TaskType.ALT_TEXT, target="fig:1", context="test")],
            context=JobContext(
                document_title="T",
                document_type=DocumentType.OTHER,
                section_context="",
            ),
            page_markdown="# P1",
        )
        plan = DocumentPlan(
            filename="f.pdf",
            total_pages=1,
            structure=DocumentStructure(title="T"),
            jobs=[job1],
        )

        job_results = [
            JobResult(
                job_id="j1",
                success=True,
                updated_markdown="# P1 updated",
                tasks_completed=1,
                tasks_failed=0,
                subagent_findings=[
                    {
                        "page": 1, "task_type": "alt_text",
                        "description": "Generated alt",
                        "confidence": 0.9, "applied": True,
                    }
                ],
            ),
        ]

        _populate_execution_snapshot(dossier, job_results, plan)

        assert dossier.execution is not None
        assert dossier.execution.pages_processed == [1]
        assert dossier.execution.tasks_completed == 1
        assert 1 in dossier.execution.subagent_findings
        assert dossier.execution.subagent_findings[1][0].task_type == "alt_text"

    def test_populate_empty_results(self):
        from src.agents.models import DocumentPlan, DocumentStructure
        from src.agents.orchestrator import _populate_execution_snapshot

        dossier = PipelineDossier(document_id="doc", filename="f.pdf")
        plan = DocumentPlan(
            filename="f.pdf",
            total_pages=1,
            structure=DocumentStructure(title="T"),
            jobs=[],
        )

        _populate_execution_snapshot(dossier, [], plan)

        assert dossier.execution is not None
        assert dossier.execution.pages_processed == []
        assert dossier.execution.tasks_completed == 0
