"""LTI test configuration and fixtures.

IMPORTANT: This module sets ALL LTI environment variables including key paths
BEFORE any src imports. Keys are generated at module load time.
"""

import os
import tempfile
from pathlib import Path

import pytest

# ============================================================================
# STEP 1: Create temp directory and generate RSA keys at module load time
# ============================================================================
_temp_dir = tempfile.mkdtemp(prefix="lti_test_keys_")
_private_key_path = Path(_temp_dir) / "lti_private.pem"
_public_key_path = Path(_temp_dir) / "lti_public.pem"

# Generate RSA keypair using jwcrypto directly (before any src imports)
from jwcrypto import jwk

_key = jwk.JWK.generate(kty="RSA", size=2048)

# Save private key
_private_pem = _key.export_to_pem(private_key=True, password=None)
with open(_private_key_path, "wb") as f:
    f.write(_private_pem)
_private_key_path.chmod(0o600)

# Save public key
_public_pem = _key.export_to_pem(private_key=False, password=None)
with open(_public_key_path, "wb") as f:
    f.write(_public_pem)

# ============================================================================
# STEP 2: Set ALL environment variables before any src imports
# ============================================================================
os.environ["LTI_ENABLED"] = "true"
os.environ["LTI_ISSUER"] = "https://canvas.test.instructure.com"
os.environ["LTI_CLIENT_ID"] = "10000000000001"
os.environ["LTI_DEPLOYMENT_ID"] = "10000000000001"
os.environ["LTI_AUTH_LOGIN_URL"] = "https://canvas.test.instructure.com/api/lti/authorize_redirect"
os.environ["LTI_AUTH_TOKEN_URL"] = "https://canvas.test.instructure.com/login/oauth2/token"
os.environ["LTI_JWKS_URL"] = "https://canvas.test.instructure.com/api/lti/security/jwks"
os.environ["LTI_PRIVATE_KEY_PATH"] = str(_private_key_path)
os.environ["LTI_PUBLIC_KEY_PATH"] = str(_public_key_path)


# ============================================================================
# STEP 3: Fixtures (can now safely import from src)
# ============================================================================
@pytest.fixture(scope="module")
def temp_keys():
    """Provide access to the pre-generated RSA keys.

    Keys are generated at module load time to ensure environment variables
    are set before any src imports.
    """
    yield {"private": _private_key_path, "public": _public_key_path}


@pytest.fixture(scope="module")
def lti_app(temp_keys):
    """Create FastAPI app with LTI routes enabled.

    This creates a fresh app instance with LTI routes registered,
    with mock Redis for state storage.
    """
    import sys
    from unittest.mock import AsyncMock, MagicMock

    # CRITICAL: Reload config module to pick up new env vars
    # The parent conftest imports src.main which creates a Settings singleton
    # with default paths. We need to reload to get our temp key paths.

    # Remove cached modules to force fresh import with new env vars
    modules_to_reload = [
        "src.config",
        "src.lti",
        "src.lti.keys",
        "src.lti.config",
        "src.lti.router",
        "src.lti.service",
        "src.lti.models",
        "src.lti.adapters",
    ]

    for mod_name in modules_to_reload:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    # Now import fresh copies
    import src.config  # noqa: F811

    # Verify the settings have correct paths
    assert str(_private_key_path) in src.config.settings.lti_private_key_path
    assert str(_public_key_path) in src.config.settings.lti_public_key_path

    # Import LTI modules fresh
    from src.lti.config import get_tool_config
    from src.lti.keys import load_private_key, load_public_key

    # Clear any caches (they should be empty after module removal, but be safe)
    get_tool_config.cache_clear()
    load_private_key.cache_clear()
    load_public_key.cache_clear()

    # Create fresh FastAPI app with LTI router
    from fastapi import FastAPI

    from src.dependencies import get_redis_client
    from src.lti.router import router as lti_router

    app = FastAPI(title="Equalify PDF Converter - LTI Test")
    app.include_router(lti_router)

    # Create mock Redis that stores state in memory
    _state_storage: dict = {}

    async def mock_get(key):
        import json

        data = _state_storage.get(key)
        if data:
            return json.dumps(data)
        return None

    async def mock_set(key, value, ex=None):
        import json

        _state_storage[key] = json.loads(value) if isinstance(value, str) else value
        return True

    async def mock_delete(key):
        _state_storage.pop(key, None)
        return 1

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=mock_get)
    mock_redis.set = AsyncMock(side_effect=mock_set)
    mock_redis.delete = AsyncMock(side_effect=mock_delete)
    mock_redis.setex = AsyncMock(side_effect=lambda k, t, v: mock_set(k, v))

    # Override Redis dependency
    app.dependency_overrides[get_redis_client] = lambda: mock_redis

    return app


@pytest.fixture
def lti_client(lti_app):
    """Test client with LTI routes."""
    from fastapi.testclient import TestClient

    return TestClient(lti_app)


def pytest_sessionfinish(session, exitstatus):
    """Clean up temp directory after all tests complete."""
    import shutil

    if Path(_temp_dir).exists():
        shutil.rmtree(_temp_dir)
