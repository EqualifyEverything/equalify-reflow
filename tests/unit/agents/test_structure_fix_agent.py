"""Tests for structure_fix_agent.

Tests cover:
- OCRDecision model validation and defaults
- StructureFixOutput model validation
- StructureFixResult model validation
- Helper functions for formatting issues
- Observation generation from OCR decisions
- Full agent workflow with mocked LLM
- Error handling scenarios
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from src.agents.structure.structure_fix_agent import (
    OCRDecision,
    StructureFixOutput,
    StructureFixResult,
    _build_document_context,
    _format_heading_issues,
    _format_lint_issues,
    _format_ocr_suggestions,
    _generate_ocr_observations,
    _get_domain_terms,
    _get_key_entities,
    fix_structural_issues,
    get_agent,
    get_prompts,
)
from src.services.structure_loop import HeadingIssue, LintIssue
from src.shared.models.document_context import DocumentSummary
from src.shared.models.remediation import DocumentManifest
from src.utils.ocr_checker import OCRSuggestion

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_manifest():
    """Create a mock document manifest with summary."""
    manifest = MagicMock(spec=DocumentManifest)
    manifest.document_type = "syllabus"
    manifest.document_title = "Test Course Syllabus"
    manifest.total_pages = 5
    manifest.summary = MagicMock(spec=DocumentSummary)
    manifest.summary.topic_summary = "Introduction to Computer Science"
    manifest.summary.audience_level = "undergraduate"
    manifest.summary.key_entities = ["Python", "Algorithm", "Data Structure"]
    manifest.summary.domain_terms = ["recursion", "complexity", "polymorphism"]
    return manifest


@pytest.fixture
def mock_manifest_no_summary():
    """Create a mock manifest without summary."""
    manifest = MagicMock(spec=DocumentManifest)
    manifest.document_type = "lecture"
    manifest.document_title = "Week 1 Lecture"
    manifest.total_pages = 3
    manifest.summary = None
    return manifest


@pytest.fixture
def sample_lint_issues():
    """Create sample lint issues."""
    return [
        LintIssue(rule="MD001", message="Heading levels should increment by one", line=5),
        LintIssue(rule="MD025", message="Multiple H1 headings", line=15, column=1),
    ]


@pytest.fixture
def sample_heading_issues():
    """Create sample heading issues."""
    return [
        HeadingIssue(
            type="multiple_h1",
            description="Found 2 H1 headings",
            locations=[1, 15],
        ),
        HeadingIssue(
            type="skipped_level",
            description="Skipped from H2 to H4",
            locations=[10],
        ),
    ]


@pytest.fixture
def sample_ocr_suggestions():
    """Create sample OCR suggestions."""
    return [
        OCRSuggestion(
            word="Pytbon",
            suggestions=["Python"],
            confidence=0.95,
            reason="key_term_variant",
            context="...programming in Pytbon is...",
        ),
        OCRSuggestion(
            word="algorythm",
            suggestions=["algorithm"],
            confidence=0.85,
            reason="spell_check_suggests_key_term",
            context="...the algorythm runs in...",
        ),
    ]


# =============================================================================
# OCRDecision Model Tests
# =============================================================================


@pytest.mark.unit
class TestOCRDecision:
    """Tests for OCRDecision model."""

    def test_valid_fix_decision(self):
        """Test creating a valid fix decision."""
        decision = OCRDecision(
            word="Pytbon",
            decision="fix",
            replacement="Python",
            reasoning="Document is about Python programming",
        )
        assert decision.word == "Pytbon"
        assert decision.decision == "fix"
        assert decision.replacement == "Python"
        assert "Python" in decision.reasoning

    def test_valid_keep_decision(self):
        """Test creating a valid keep decision."""
        decision = OCRDecision(
            word="yt",
            decision="keep",
            replacement=None,
            reasoning="'yt' is a known project name in this context",
        )
        assert decision.decision == "keep"
        assert decision.replacement is None

    def test_valid_uncertain_decision(self):
        """Test creating a valid uncertain decision."""
        decision = OCRDecision(
            word="ambiguous",
            decision="uncertain",
            reasoning="Could be a valid technical term or OCR error",
        )
        assert decision.decision == "uncertain"
        assert decision.replacement is None

    def test_decision_with_default_replacement(self):
        """Test decision with default None replacement."""
        decision = OCRDecision(
            word="test",
            decision="keep",
            reasoning="Word is correct",
        )
        assert decision.replacement is None

    def test_invalid_decision_value(self):
        """Test that invalid decision value raises error."""
        with pytest.raises(ValidationError):
            OCRDecision(
                word="test",
                decision="invalid_decision",  # type: ignore[arg-type]
                reasoning="Test",
            )

    def test_required_fields(self):
        """Test that required fields must be provided."""
        with pytest.raises(ValidationError):
            OCRDecision(word="test")  # Missing decision and reasoning


# =============================================================================
# StructureFixOutput Model Tests
# =============================================================================


@pytest.mark.unit
class TestStructureFixOutput:
    """Tests for StructureFixOutput model."""

    def test_valid_output(self):
        """Test creating a valid StructureFixOutput."""
        output = StructureFixOutput(
            corrected_markdown="# Title\n\n## Section",
            correction_summary="Fixed 1 heading hierarchy issue",
            ocr_decisions=[],
            heading_fixes="Adjusted H3 to H2 at line 10",
            other_fixes="None",
        )
        assert "# Title" in output.corrected_markdown
        assert "Fixed 1" in output.correction_summary

    def test_output_with_ocr_decisions(self):
        """Test output with OCR decisions."""
        decision = OCRDecision(
            word="Pytbon",
            decision="fix",
            replacement="Python",
            reasoning="Test",
        )
        output = StructureFixOutput(
            corrected_markdown="# Python Guide",
            correction_summary="Fixed OCR error",
            ocr_decisions=[decision],
        )
        assert len(output.ocr_decisions) == 1
        assert output.ocr_decisions[0].word == "Pytbon"

    def test_output_defaults(self):
        """Test default values."""
        output = StructureFixOutput(
            corrected_markdown="# Test",
            correction_summary="No changes",
        )
        assert output.ocr_decisions == []
        assert output.heading_fixes == ""
        assert output.other_fixes == ""


# =============================================================================
# StructureFixResult Model Tests
# =============================================================================


@pytest.mark.unit
class TestStructureFixResult:
    """Tests for StructureFixResult model."""

    def test_valid_result(self):
        """Test creating a valid StructureFixResult."""
        result = StructureFixResult(
            corrected_markdown="# Test",
            correction_summary="Fixed issues",
            ocr_decisions=[],
            observations=[],
            cost_cents=1.5,
        )
        assert result.corrected_markdown == "# Test"
        assert result.cost_cents == 1.5

    def test_result_defaults(self):
        """Test default values."""
        result = StructureFixResult(
            corrected_markdown="# Test",
            correction_summary="No changes",
        )
        assert result.ocr_decisions == []
        assert result.observations == []
        assert result.cost_cents == 0.0


# =============================================================================
# Helper Function Tests - Document Context
# =============================================================================


@pytest.mark.unit
class TestBuildDocumentContext:
    """Tests for _build_document_context helper."""

    def test_with_full_manifest(self, mock_manifest):
        """Test building context with full manifest."""
        context = _build_document_context(mock_manifest)

        assert "syllabus" in context
        assert "Test Course Syllabus" in context
        assert "5" in context
        assert "Computer Science" in context
        assert "undergraduate" in context

    def test_without_summary(self, mock_manifest_no_summary):
        """Test building context without summary."""
        context = _build_document_context(mock_manifest_no_summary)

        assert "lecture" in context
        assert "Week 1 Lecture" in context
        assert "Topic:" not in context  # No summary, no topic


@pytest.mark.unit
class TestGetKeyEntities:
    """Tests for _get_key_entities helper."""

    def test_with_entities(self, mock_manifest):
        """Test getting key entities."""
        entities = _get_key_entities(mock_manifest)

        assert "Python" in entities
        assert "Algorithm" in entities
        assert len(entities) == 3

    def test_without_summary(self, mock_manifest_no_summary):
        """Test returns empty list without summary."""
        entities = _get_key_entities(mock_manifest_no_summary)
        assert entities == []

    def test_with_empty_entities(self):
        """Test with empty entities list."""
        manifest = MagicMock(spec=DocumentManifest)
        manifest.summary = MagicMock()
        manifest.summary.key_entities = []

        entities = _get_key_entities(manifest)
        assert entities == []


@pytest.mark.unit
class TestGetDomainTerms:
    """Tests for _get_domain_terms helper."""

    def test_with_terms(self, mock_manifest):
        """Test getting domain terms."""
        terms = _get_domain_terms(mock_manifest)

        assert "recursion" in terms
        assert "complexity" in terms
        assert len(terms) == 3

    def test_without_summary(self, mock_manifest_no_summary):
        """Test returns empty list without summary."""
        terms = _get_domain_terms(mock_manifest_no_summary)
        assert terms == []


# =============================================================================
# Helper Function Tests - Issue Formatting
# =============================================================================


@pytest.mark.unit
class TestFormatLintIssues:
    """Tests for _format_lint_issues helper."""

    def test_format_multiple_issues(self, sample_lint_issues):
        """Test formatting multiple lint issues."""
        formatted = _format_lint_issues(sample_lint_issues)

        assert "Line 5" in formatted
        assert "MD001" in formatted
        assert "Line 15" in formatted
        assert "MD025" in formatted

    def test_format_empty_list(self):
        """Test formatting empty list returns empty string."""
        formatted = _format_lint_issues([])
        assert formatted == ""

    def test_format_single_issue(self):
        """Test formatting single issue."""
        issues = [LintIssue(rule="MD009", message="Trailing spaces", line=3)]
        formatted = _format_lint_issues(issues)

        assert "Line 3" in formatted
        assert "MD009" in formatted
        assert "Trailing spaces" in formatted


@pytest.mark.unit
class TestFormatHeadingIssues:
    """Tests for _format_heading_issues helper."""

    def test_format_multiple_issues(self, sample_heading_issues):
        """Test formatting multiple heading issues."""
        formatted = _format_heading_issues(sample_heading_issues)

        assert "multiple_h1" in formatted
        assert "skipped_level" in formatted
        assert "1, 15" in formatted  # Locations for multiple_h1

    def test_format_empty_list(self):
        """Test formatting empty list returns empty string."""
        formatted = _format_heading_issues([])
        assert formatted == ""


@pytest.mark.unit
class TestFormatOCRSuggestions:
    """Tests for _format_ocr_suggestions helper."""

    def test_format_multiple_suggestions(self, sample_ocr_suggestions):
        """Test formatting multiple OCR suggestions."""
        formatted = _format_ocr_suggestions(sample_ocr_suggestions)

        assert "Pytbon" in formatted
        assert "Python" in formatted
        assert "algorythm" in formatted
        assert "Context:" in formatted
        assert "confidence:" in formatted

    def test_format_empty_list(self):
        """Test formatting empty list returns empty string."""
        formatted = _format_ocr_suggestions([])
        assert formatted == ""

    def test_format_limits_suggestions(self):
        """Test that suggestions are limited to 3."""
        suggestion = OCRSuggestion(
            word="xyz",
            suggestions=["alpha", "beta", "gamma", "delta", "epsilon"],  # 5 suggestions
            confidence=0.8,
            reason="variant",
            context="the xyz value",
        )
        formatted = _format_ocr_suggestions([suggestion])

        # Should only include first 3
        assert "alpha, beta, gamma" in formatted
        assert "delta" not in formatted
        assert "epsilon" not in formatted


# =============================================================================
# Observation Generation Tests
# =============================================================================


@pytest.mark.unit
class TestGenerateOCRObservations:
    """Tests for _generate_ocr_observations helper."""

    def test_fix_decision_creates_closed_observation(self):
        """Test fix decisions create closed observations."""
        decisions = [
            OCRDecision(
                word="Pytbon",
                decision="fix",
                replacement="Python",
                reasoning="OCR error in context",
            )
        ]

        observations = _generate_ocr_observations(decisions, "test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.status == "closed"
        assert obs.resolution == "fixed"
        assert obs.category == "ocr"
        assert obs.agent == "structure"
        assert "Pytbon" in obs.visual_description
        assert "Python" in obs.markup_description
        assert obs.confidence == 0.9

    def test_keep_decision_creates_no_observation(self):
        """Test keep decisions don't create observations."""
        decisions = [
            OCRDecision(
                word="yt",
                decision="keep",
                reasoning="Valid term",
            )
        ]

        observations = _generate_ocr_observations(decisions, "test-job")
        assert len(observations) == 0

    def test_uncertain_decision_creates_open_observation(self):
        """Test uncertain decisions create open observations."""
        decisions = [
            OCRDecision(
                word="ambiguous",
                decision="uncertain",
                reasoning="Could be either",
            )
        ]

        observations = _generate_ocr_observations(decisions, "test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.status == "open"
        assert obs.resolution is None
        assert obs.confidence == 0.5  # Lower confidence for uncertain

    def test_multiple_decisions(self):
        """Test multiple decisions generate appropriate observations."""
        decisions = [
            OCRDecision(word="fix1", decision="fix", replacement="fixed1", reasoning="test"),
            OCRDecision(word="keep1", decision="keep", reasoning="test"),
            OCRDecision(word="uncertain1", decision="uncertain", reasoning="test"),
            OCRDecision(word="fix2", decision="fix", replacement="fixed2", reasoning="test"),
        ]

        observations = _generate_ocr_observations(decisions, "test-job")

        # 2 fix + 1 uncertain = 3 observations (keep doesn't create any)
        assert len(observations) == 3

        closed = [o for o in observations if o.status == "closed"]
        opened = [o for o in observations if o.status == "open"]
        assert len(closed) == 2
        assert len(opened) == 1


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestAgentInitialization:
    """Tests for agent initialization functions."""

    def test_get_prompts_returns_dict(self):
        """Test get_prompts returns a dictionary."""
        prompts = get_prompts()

        assert isinstance(prompts, dict)
        assert "system_prompt" in prompts
        assert "user_prompt" in prompts

    def test_get_prompts_caches_result(self):
        """Test prompts are cached."""
        prompts1 = get_prompts()
        prompts2 = get_prompts()

        # Should be the same object (cached)
        assert prompts1 is prompts2

    def test_get_agent_returns_agent(self):
        """Test get_agent returns an agent instance."""
        # Mock create_agent to avoid AWS connection
        with patch("src.agents.structure.structure_fix_agent.create_agent") as mock_create:
            mock_agent = MagicMock()
            mock_create.return_value = mock_agent

            # Reset cached agent first
            import src.agents.structure.structure_fix_agent as agent_module
            agent_module._agent = None

            agent = get_agent()

            assert agent is mock_agent
            mock_create.assert_called_once()

    def test_get_agent_caches_result(self):
        """Test agent is cached."""
        with patch("src.agents.structure.structure_fix_agent.create_agent") as mock_create:
            mock_agent = MagicMock()
            mock_create.return_value = mock_agent

            # Reset cached agent first
            import src.agents.structure.structure_fix_agent as agent_module
            agent_module._agent = None

            agent1 = get_agent()
            agent2 = get_agent()

            # Should be the same object (cached)
            assert agent1 is agent2
            # create_agent should only be called once
            mock_create.assert_called_once()


# =============================================================================
# Full Workflow Tests (with mocked LLM)
# =============================================================================


@pytest.mark.unit
class TestFixStructuralIssues:
    """Tests for fix_structural_issues function."""

    @pytest.fixture(autouse=True)
    def reset_agent_cache(self):
        """Reset the agent cache before each test."""
        import src.agents.structure.structure_fix_agent as agent_module
        agent_module._agent = None
        yield
        agent_module._agent = None

    @pytest.mark.asyncio
    async def test_fix_with_lint_issues(
        self,
        mock_manifest,
        sample_lint_issues,
    ):
        """Test fixing lint issues."""
        with patch("src.agents.structure.structure_fix_agent.get_agent") as mock_get_agent, \
             patch("src.agents.structure.structure_fix_agent.run_agent") as mock_run:
            mock_get_agent.return_value = MagicMock()
            mock_output = StructureFixOutput(
                corrected_markdown="# Fixed Title\n\n## Section",
                correction_summary="Fixed heading hierarchy",
                heading_fixes="Adjusted heading at line 5",
            )

            mock_result = MagicMock()
            mock_result.output = mock_output

            mock_usage = MagicMock()
            mock_usage.estimated_cost_cents = 1.5
            mock_run.return_value = mock_result

            with patch(
                "src.agents.structure.structure_fix_agent.extract_usage",
                return_value=mock_usage,
            ):
                result = await fix_structural_issues(
                    markdown="# Title\n\n#### Section",
                    lint_issues=sample_lint_issues,
                    heading_issues=[],
                    ocr_suggestions=[],
                    manifest=mock_manifest,
                    job_id="test-job",
                )

            assert result.corrected_markdown == "# Fixed Title\n\n## Section"
            assert "Fixed heading" in result.correction_summary
            assert result.cost_cents == 1.5

    @pytest.mark.asyncio
    async def test_fix_with_ocr_suggestions(
        self,
        mock_manifest,
        sample_ocr_suggestions,
    ):
        """Test fixing OCR suggestions creates observations."""
        with patch("src.agents.structure.structure_fix_agent.get_agent") as mock_get_agent, \
             patch("src.agents.structure.structure_fix_agent.run_agent") as mock_run:
            mock_get_agent.return_value = MagicMock()
            mock_output = StructureFixOutput(
                corrected_markdown="# Python Guide",
                correction_summary="Fixed OCR error: Pytbon -> Python",
                ocr_decisions=[
                    OCRDecision(
                        word="Pytbon",
                        decision="fix",
                        replacement="Python",
                        reasoning="Document is about Python",
                    ),
                    OCRDecision(
                        word="algorythm",
                        decision="keep",
                        reasoning="Actually a valid spelling in this context",
                    ),
                ],
            )

            mock_result = MagicMock()
            mock_result.output = mock_output

            mock_usage = MagicMock()
            mock_usage.estimated_cost_cents = 2.0
            mock_run.return_value = mock_result

            with patch(
                "src.agents.structure.structure_fix_agent.extract_usage",
                return_value=mock_usage,
            ):
                result = await fix_structural_issues(
                    markdown="# Pytbon Guide",
                    lint_issues=[],
                    heading_issues=[],
                    ocr_suggestions=sample_ocr_suggestions,
                    manifest=mock_manifest,
                    job_id="test-job",
                )

            # Should have 1 observation (fix creates, keep doesn't)
            assert len(result.observations) == 1
            assert result.observations[0].status == "closed"
            assert result.observations[0].category == "ocr"

    @pytest.mark.asyncio
    async def test_fix_with_heading_fixes_creates_observation(
        self,
        mock_manifest,
    ):
        """Test heading fixes create observations."""
        with patch("src.agents.structure.structure_fix_agent.get_agent") as mock_get_agent, \
             patch("src.agents.structure.structure_fix_agent.run_agent") as mock_run:
            mock_get_agent.return_value = MagicMock()
            mock_output = StructureFixOutput(
                corrected_markdown="# Title\n\n## Section",
                correction_summary="Fixed heading hierarchy",
                heading_fixes="Adjusted H4 to H2 at line 5",
            )

            mock_result = MagicMock()
            mock_result.output = mock_output

            mock_usage = MagicMock()
            mock_usage.estimated_cost_cents = 1.0
            mock_run.return_value = mock_result

            with patch(
                "src.agents.structure.structure_fix_agent.extract_usage",
                return_value=mock_usage,
            ):
                result = await fix_structural_issues(
                    markdown="# Title\n\n#### Section",
                    lint_issues=[],
                    heading_issues=[
                        HeadingIssue(
                            type="skipped_level",
                            description="Skipped from H1 to H4",
                            locations=[5],
                        )
                    ],
                    ocr_suggestions=[],
                    manifest=mock_manifest,
                    job_id="test-job",
                )

            # Should have 1 observation for heading fix
            heading_obs = [o for o in result.observations if o.category == "heading"]
            assert len(heading_obs) == 1
            assert heading_obs[0].status == "closed"
            assert heading_obs[0].resolution == "fixed"

    @pytest.mark.asyncio
    async def test_fix_with_no_heading_changes(
        self,
        mock_manifest,
    ):
        """Test 'none' heading fixes don't create observations."""
        with patch("src.agents.structure.structure_fix_agent.get_agent") as mock_get_agent, \
             patch("src.agents.structure.structure_fix_agent.run_agent") as mock_run:
            mock_get_agent.return_value = MagicMock()
            mock_output = StructureFixOutput(
                corrected_markdown="# Title",
                correction_summary="No changes needed",
                heading_fixes="None",  # No changes
            )

            mock_result = MagicMock()
            mock_result.output = mock_output

            mock_usage = MagicMock()
            mock_usage.estimated_cost_cents = 0.5
            mock_run.return_value = mock_result

            with patch(
                "src.agents.structure.structure_fix_agent.extract_usage",
                return_value=mock_usage,
            ):
                result = await fix_structural_issues(
                    markdown="# Title",
                    lint_issues=[],
                    heading_issues=[],
                    ocr_suggestions=[],
                    manifest=mock_manifest,
                    job_id="test-job",
                )

            # No heading observations
            heading_obs = [o for o in result.observations if o.category == "heading"]
            assert len(heading_obs) == 0

    @pytest.mark.asyncio
    async def test_fix_without_summary_uses_defaults(
        self,
        mock_manifest_no_summary,
    ):
        """Test fix works without document summary."""
        with patch("src.agents.structure.structure_fix_agent.get_agent") as mock_get_agent, \
             patch("src.agents.structure.structure_fix_agent.run_agent") as mock_run:
            mock_get_agent.return_value = MagicMock()
            mock_output = StructureFixOutput(
                corrected_markdown="# Title",
                correction_summary="No changes",
            )

            mock_result = MagicMock()
            mock_result.output = mock_output

            mock_usage = MagicMock()
            mock_usage.estimated_cost_cents = 0.5
            mock_run.return_value = mock_result

            with patch(
                "src.agents.structure.structure_fix_agent.extract_usage",
                return_value=mock_usage,
            ):
                result = await fix_structural_issues(
                    markdown="# Title",
                    lint_issues=[],
                    heading_issues=[],
                    ocr_suggestions=[],
                    manifest=mock_manifest_no_summary,
                    job_id="test-job",
                )

            # Should complete without error
            assert result.corrected_markdown == "# Title"


# =============================================================================
# Edge Case Tests
# =============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases."""

    def test_format_empty_issues(self):
        """Test all format functions handle empty inputs."""
        assert _format_lint_issues([]) == ""
        assert _format_heading_issues([]) == ""
        assert _format_ocr_suggestions([]) == ""

    def test_generate_observations_empty_list(self):
        """Test observation generation with empty decisions."""
        observations = _generate_ocr_observations([], "test-job")
        assert observations == []

    def test_ocr_decision_with_empty_strings(self):
        """Test OCR decision with minimal data."""
        decision = OCRDecision(
            word="",
            decision="keep",
            reasoning="",
        )
        # Should be valid even with empty strings
        assert decision.word == ""
        assert decision.reasoning == ""

    def test_lint_issue_without_column(self):
        """Test lint issue with default column."""
        issue = LintIssue(rule="MD001", message="Test", line=1)
        formatted = _format_lint_issues([issue])
        assert "Line 1" in formatted

    def test_heading_issue_empty_locations(self):
        """Test heading issue with no locations."""
        issue = HeadingIssue(
            type="test",
            description="Test issue",
            locations=[],
        )
        formatted = _format_heading_issues([issue])
        assert "test" in formatted
        assert "(lines: )" in formatted  # Empty locations

    def test_ocr_suggestion_empty_context(self):
        """Test OCR suggestion with empty context."""
        suggestion = OCRSuggestion(
            word="test",
            suggestions=["best"],
            confidence=0.9,
            reason="test",
            context="",
        )
        formatted = _format_ocr_suggestions([suggestion])
        assert "Context:" in formatted


# =============================================================================
# Unicode and Special Character Tests
# =============================================================================


@pytest.mark.unit
class TestUnicodeHandling:
    """Tests for unicode and special character handling."""

    def test_ocr_decision_with_unicode(self):
        """Test OCR decision with unicode characters."""
        decision = OCRDecision(
            word="caf\u00e9",
            decision="keep",
            reasoning="French word is correct",
        )
        assert decision.word == "caf\u00e9"

    def test_format_lint_issues_with_unicode(self):
        """Test formatting lint issues with unicode."""
        issues = [
            LintIssue(
                rule="MD001",
                message="Heading contains: \u00e9\u00e8\u00ea",
                line=1,
            )
        ]
        formatted = _format_lint_issues(issues)
        assert "\u00e9\u00e8\u00ea" in formatted

    def test_format_ocr_suggestions_with_unicode(self):
        """Test formatting OCR suggestions with unicode."""
        suggestions = [
            OCRSuggestion(
                word="\u00fcber",
                suggestions=["uber"],
                confidence=0.8,
                reason="test",
                context="...the \u00fcber driver...",
            )
        ]
        formatted = _format_ocr_suggestions(suggestions)
        assert "\u00fcber" in formatted
