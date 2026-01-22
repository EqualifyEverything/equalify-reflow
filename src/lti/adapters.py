"""FastAPI and Redis adapters for pylti1p3 library.

This module provides adapters that bridge pylti1p3's expected interfaces
with FastAPI's Request/Response objects and Redis for state storage.

The key adaptation is using Redis instead of cookies for state storage,
which is required for iframe-based LTI launches where third-party cookies
are blocked by modern browsers.
"""

import json
import logging
from typing import Any
from urllib.parse import parse_qs

from fastapi import Request, Response
from pylti1p3.launch_data_storage.base import LaunchDataStorage
from pylti1p3.request import Request as LTI1p3Request
from pylti1p3.session import SessionService
from redis.asyncio import Redis

from ..config import settings

logger = logging.getLogger(__name__)


class FastAPIRequest(LTI1p3Request):
    """Adapter that wraps FastAPI Request for pylti1p3 compatibility.

    pylti1p3 expects a request object with specific methods for accessing
    form data, query parameters, and cookies. This adapter translates
    FastAPI's Request to that interface.
    """

    def __init__(
        self,
        request: Request,
        form_data: dict[str, Any] | None = None,
        session_service: "SessionService | None" = None,
    ):
        """Initialize the adapter.

        Args:
            request: FastAPI Request object
            form_data: Pre-parsed form data (since FastAPI form parsing is async)
            session_service: Optional session service for state management
        """
        super().__init__()
        self._request = request
        self._form_data = form_data or {}
        self._session_service = session_service

    def get_param(self, key: str) -> str | None:
        """Get a parameter from POST data or query string.

        Args:
            key: Parameter name

        Returns:
            Parameter value or None
        """
        # Check POST data first (form_data)
        if key in self._form_data:
            value = self._form_data[key]
            if isinstance(value, list):
                return value[0] if value else None
            return str(value) if value is not None else None

        # Then check query parameters
        query_value = self._request.query_params.get(key)
        return query_value

    def is_secure(self) -> bool:
        """Check if request was made over HTTPS.

        Returns:
            True if HTTPS, False otherwise
        """
        # Check X-Forwarded-Proto header (for reverse proxy setups)
        forwarded_proto = self._request.headers.get("X-Forwarded-Proto", "")
        if forwarded_proto.lower() == "https":
            return True

        # Check direct URL scheme
        return self._request.url.scheme == "https"

    def get_session_service(self) -> "SessionService | None":
        """Get the session service for state management.

        Returns:
            SessionService instance or None
        """
        return self._session_service

    def set_session_service(self, service: "SessionService") -> "FastAPIRequest":
        """Set the session service.

        Args:
            service: SessionService instance

        Returns:
            Self for chaining
        """
        self._session_service = service
        return self


class RedisLaunchDataStorage(LaunchDataStorage):
    """Redis-based storage for LTI launch data and OIDC state.

    This replaces cookie-based session storage, which is required because:
    1. LTI launches happen in iframes
    2. Modern browsers block third-party cookies in iframes
    3. Redis provides reliable state storage across requests

    State is stored with a TTL to prevent memory leaks from abandoned flows.
    """

    # Redis key prefixes
    STATE_PREFIX = "eq-pdf:lti:state:"
    NONCE_PREFIX = "eq-pdf:lti:nonce:"
    LAUNCH_PREFIX = "eq-pdf:lti:launch:"

    def __init__(self, redis_client: Redis):
        """Initialize with Redis client.

        Args:
            redis_client: Async Redis client instance
        """
        self._redis = redis_client
        self._state_ttl = settings.lti_state_ttl_seconds

    def _get_key(self, prefix: str, key: str) -> str:
        """Build Redis key with prefix.

        Args:
            prefix: Key prefix
            key: State/nonce/launch key

        Returns:
            Full Redis key
        """
        return f"{prefix}{key}"

    def can_set_keys_expiration(self) -> bool:
        """Whether this storage supports key expiration.

        Returns:
            True - Redis supports TTL
        """
        return True

    def check_value(self, key: str) -> bool:
        """Check if value exists in storage (sync wrapper).

        Note: This is a sync method required by pylti1p3 base class.
        Use check_value_async for async operations.

        Args:
            key: Storage key

        Returns:
            True if key exists, False otherwise
        """
        # This should be called in an async context via check_value_async
        raise NotImplementedError("Use check_value_async instead")

    async def check_value_async(self, key: str) -> bool:
        """Check if value exists in Redis storage.

        Args:
            key: Storage key

        Returns:
            True if key exists, False otherwise
        """
        redis_key = self._get_key(self.STATE_PREFIX, key)
        return bool(await self._redis.exists(redis_key))

    def get_value(self, key: str) -> dict[str, Any] | None:
        """Get value from storage (sync wrapper).

        Note: This is a sync method required by pylti1p3, but we need
        async Redis. We use a sync Redis call here.

        Args:
            key: Storage key

        Returns:
            Stored value or None
        """
        # This should be called in an async context via get_value_async
        raise NotImplementedError("Use get_value_async instead")

    async def get_value_async(self, key: str) -> dict[str, Any] | None:
        """Get value from Redis storage.

        Args:
            key: Storage key

        Returns:
            Stored dictionary value or None
        """
        redis_key = self._get_key(self.STATE_PREFIX, key)
        value = await self._redis.get(redis_key)

        if value is None:
            return None

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in LTI state key: {key}")
            return None

    def set_value(
        self,
        key: str,
        value: dict[str, Any],
        exp: int | None = None,
    ) -> None:
        """Set value in storage (sync wrapper).

        Args:
            key: Storage key
            value: Value to store
            exp: Expiration time (unused, we use config TTL)
        """
        raise NotImplementedError("Use set_value_async instead")

    async def set_value_async(
        self,
        key: str,
        value: dict[str, Any],
        exp: int | None = None,
    ) -> None:
        """Set value in Redis storage with TTL.

        Args:
            key: Storage key
            value: Dictionary value to store
            exp: Expiration time (optional, uses config TTL if not provided)
        """
        redis_key = self._get_key(self.STATE_PREFIX, key)
        ttl = exp if exp is not None else self._state_ttl

        await self._redis.set(
            redis_key,
            json.dumps(value),
            ex=ttl,
        )
        logger.debug(f"Stored LTI state: {key[:8]}... (TTL: {ttl}s)")

    async def check_nonce_async(self, nonce: str, iss: str) -> bool:
        """Check if nonce has been used (replay attack protection).

        Args:
            nonce: Nonce from JWT
            iss: Issuer (Canvas URL)

        Returns:
            True if nonce is valid (not seen before)
        """
        redis_key = self._get_key(self.NONCE_PREFIX, f"{iss}:{nonce}")

        # SETNX returns True if key was set (nonce is new)
        # Returns False if key already exists (replay attack)
        result = await self._redis.setnx(redis_key, "1")

        if result:
            # Set TTL on new nonce
            await self._redis.expire(redis_key, self._state_ttl)
            return True

        logger.warning(f"Nonce replay detected: {nonce[:8]}...")
        return False

    async def set_launch_data_async(
        self,
        launch_id: str,
        data: dict[str, Any],
    ) -> None:
        """Store launch data for later retrieval.

        Args:
            launch_id: Unique launch identifier
            data: Launch data to store
        """
        redis_key = self._get_key(self.LAUNCH_PREFIX, launch_id)
        await self._redis.set(
            redis_key,
            json.dumps(data),
            ex=self._state_ttl,
        )

    async def get_launch_data_async(
        self,
        launch_id: str,
    ) -> dict[str, Any] | None:
        """Retrieve stored launch data.

        Args:
            launch_id: Launch identifier

        Returns:
            Launch data or None
        """
        redis_key = self._get_key(self.LAUNCH_PREFIX, launch_id)
        value = await self._redis.get(redis_key)

        if value is None:
            return None

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    async def cleanup_state_async(self, key: str) -> None:
        """Remove state after successful launch.

        Args:
            key: State key to remove
        """
        redis_key = self._get_key(self.STATE_PREFIX, key)
        await self._redis.delete(redis_key)


class FastAPISessionService(SessionService):
    """Session service that uses Redis for state storage.

    This adapts pylti1p3's SessionService to use our RedisLaunchDataStorage
    instead of cookies.
    """

    def __init__(
        self,
        request: "FastAPIRequest",
        storage: RedisLaunchDataStorage,
    ):
        """Initialize session service.

        Args:
            request: FastAPI request adapter
            storage: Redis-based storage
        """
        super().__init__(request)
        self._storage = storage
        self._state_params: dict[str, Any] = {}

    def get_state_params(self) -> dict[str, Any]:
        """Get current state parameters.

        Returns:
            State parameters dictionary
        """
        return self._state_params

    def set_state_params(self, params: dict[str, Any]) -> None:
        """Set state parameters.

        Args:
            params: State parameters to store
        """
        self._state_params = params


async def parse_form_data(request: Request) -> dict[str, Any]:
    """Parse form data from FastAPI request.

    FastAPI's form parsing is async, so we need to do this before
    creating the pylti1p3 request adapter.

    Args:
        request: FastAPI Request

    Returns:
        Dictionary of form data
    """
    try:
        form = await request.form()
        return {k: v for k, v in form.items()}
    except Exception:
        # Try parsing body as URL-encoded
        try:
            body = await request.body()
            return parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except Exception:
            return {}
