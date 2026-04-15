"""Integration tests for Review API endpoints.

IMPORTANT: These tests are SKIPPED because they test the old Proposal-based review API
which was deprecated in PRD-021 (4-phase architecture refactor). The Proposal model
was removed in favor of a simplified Observation lifecycle where observations go
directly from "open" to "closed" with a resolution.

The new Review Checklist API will be implemented in PRD-027.

Previous test coverage included:
- Complete workflow: create job → add observations → consolidate → approve → apply
- Batch approve workflow
- Edit workflow (with and without 'after')
- Review summary and listing endpoints

These tests used real Floci S3 and Redis (no mocking of infrastructure).
"""

import pytest

# Skip the entire module - tests are for deprecated Proposal-based API
pytest.skip(
    "Review API tests use deprecated Proposal model - pending PRD-027 implementation",
    allow_module_level=True
)
