"""
Shared test fixtures for Equalify PDF Converter test suite.

This package provides canonical fixtures to eliminate duplication across
the test suite. All fixtures use function scope by default unless specified.

Modules:
    clients: Mock clients (Redis, S3, AI services)
    data_factories: Test data generators (UUIDs, jobs, documents)
    helpers: Assertion and setup helpers
    services: Pre-configured service instances with mocked dependencies

Note: Fixtures are registered via pytest_plugins in tests/conftest.py.
      Do not import from submodules here to avoid pytest assert rewrite warnings.
"""

# Do NOT import from submodules here - causes pytest assert rewrite warnings
# Fixtures are automatically discovered via pytest_plugins registration in conftest.py
__all__ = []
