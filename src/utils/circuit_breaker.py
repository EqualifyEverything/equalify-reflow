"""Circuit breaker pattern for fault tolerance and cascading failure prevention.

Implements the circuit breaker pattern to prevent repeated calls to failing services.
When a service repeatedly fails, the circuit breaker "opens" to prevent further
attempts, giving the service time to recover. After a timeout, it transitions to
"half-open" to test if the service has recovered.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Service is failing, requests blocked immediately
- HALF_OPEN: Testing recovery, limited requests allowed

Typical usage:
    >>> breaker = CircuitBreaker(name="s3-upload", failure_threshold=5)
    >>>
    >>> # Before operation
    >>> if breaker.is_open:
    >>>     raise CircuitBreakerOpenError("S3 circuit breaker open")
    >>>
    >>> try:
    >>>     result = await some_s3_operation()
    >>>     breaker.record_success()
    >>> except Exception:
    >>>     breaker.record_failure()
    >>>     raise
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from threading import Lock

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"          # Normal operation
    OPEN = "open"              # Blocking requests (service failing)
    HALF_OPEN = "half_open"    # Testing recovery


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open (blocking requests)."""
    pass


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior.

    Attributes:
        failure_threshold: Number of consecutive failures to open circuit (default: 5)
        success_threshold: Number of consecutive successes in half-open to close circuit (default: 2)
        timeout: Seconds to wait before transitioning from open to half-open (default: 60)
        half_open_max_calls: Max concurrent calls allowed in half-open state (default: 1)
    """
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 60.0
    half_open_max_calls: int = 1


class CircuitBreaker:
    """Circuit breaker for preventing cascading failures.

    Thread-safe implementation that tracks failure/success counts and manages
    state transitions to protect downstream services from repeated failures.

    Args:
        name: Human-readable name for logging and metrics
        config: Configuration object (uses defaults if not provided)
        failure_threshold: Override failure threshold
        success_threshold: Override success threshold
        timeout: Override timeout in seconds

    Example:
        >>> breaker = CircuitBreaker("s3-upload")
        >>>
        >>> # Check state before operation
        >>> breaker.check_state()  # Raises CircuitBreakerOpenError if open
        >>>
        >>> try:
        >>>     result = await s3_client.put_object(...)
        >>>     breaker.record_success()
        >>> except Exception:
        >>>     breaker.record_failure()
        >>>     raise
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
        failure_threshold: int | None = None,
        success_threshold: int | None = None,
        timeout: float | None = None
    ):
        """Initialize circuit breaker.

        Args:
            name: Circuit breaker name for logging
            config: Full configuration object
            failure_threshold: Override config failure threshold
            success_threshold: Override config success threshold
            timeout: Override config timeout
        """
        self.name = name

        # Use provided config or create default
        self.config = config or CircuitBreakerConfig()

        # Allow individual overrides
        if failure_threshold is not None:
            self.config.failure_threshold = failure_threshold
        if success_threshold is not None:
            self.config.success_threshold = success_threshold
        if timeout is not None:
            self.config.timeout = timeout

        # State tracking
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0

        # Thread safety
        self._lock = Lock()

        logger.info(
            f"Circuit breaker '{name}' initialized: "
            f"failure_threshold={self.config.failure_threshold}, "
            f"success_threshold={self.config.success_threshold}, "
            f"timeout={self.config.timeout}s"
        )

    @property
    def state(self) -> CircuitState:
        """Get current circuit state (thread-safe)."""
        with self._lock:
            self._update_state()
            return self._state

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking requests)."""
        return self.state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self.state == CircuitState.HALF_OPEN

    def check_state(self) -> None:
        """Check circuit state and raise exception if open.

        Raises:
            CircuitBreakerOpenError: If circuit is open or half-open limit reached
        """
        with self._lock:
            self._update_state()

            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is open. "
                    f"Service has failed {self._failure_count} times. "
                    f"Retry after {self.config.timeout}s."
                )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is half-open with max concurrent calls reached."
                    )
                self._half_open_calls += 1

    def record_success(self) -> None:
        """Record successful operation.

        - In CLOSED: Resets failure count
        - In HALF_OPEN: Increments success count, may transition to CLOSED
        """
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                self._half_open_calls = max(0, self._half_open_calls - 1)

                logger.debug(
                    f"Circuit breaker '{self.name}': success in half-open state "
                    f"({self._success_count}/{self.config.success_threshold})"
                )

                # Transition to CLOSED if enough successes
                if self._success_count >= self.config.success_threshold:
                    self._transition_to_closed()

            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                if self._failure_count > 0:
                    logger.debug(
                        f"Circuit breaker '{self.name}': success, resetting failure count "
                        f"(was {self._failure_count})"
                    )
                    self._failure_count = 0

    def record_failure(self) -> None:
        """Record failed operation.

        - In CLOSED: Increments failure count, may transition to OPEN
        - In HALF_OPEN: Immediately transitions back to OPEN
        """
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    f"Circuit breaker '{self.name}': failure in half-open state, reopening circuit"
                )
                self._half_open_calls = max(0, self._half_open_calls - 1)
                self._transition_to_open()

            elif self._state == CircuitState.CLOSED:
                logger.debug(
                    f"Circuit breaker '{self.name}': failure recorded "
                    f"({self._failure_count}/{self.config.failure_threshold})"
                )

                # Transition to OPEN if threshold reached
                if self._failure_count >= self.config.failure_threshold:
                    self._transition_to_open()

    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED state.

        Useful for testing or manual intervention.
        """
        with self._lock:
            logger.info(f"Circuit breaker '{self.name}': manual reset")
            self._transition_to_closed()

    def _update_state(self) -> None:
        """Update state based on timeout (OPEN → HALF_OPEN transition).

        Must be called while holding self._lock.
        """
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.time() - self._last_failure_time

            if elapsed >= self.config.timeout:
                logger.info(
                    f"Circuit breaker '{self.name}': timeout elapsed ({elapsed:.1f}s), "
                    f"transitioning to half-open"
                )
                self._transition_to_half_open()

    def _transition_to_open(self) -> None:
        """Transition to OPEN state (must hold lock)."""
        self._state = CircuitState.OPEN
        self._success_count = 0
        self._half_open_calls = 0

        logger.warning(
            f"Circuit breaker '{self.name}': OPEN - service has failed "
            f"{self._failure_count} times (threshold: {self.config.failure_threshold})"
        )

    def _transition_to_half_open(self) -> None:
        """Transition to HALF_OPEN state (must hold lock)."""
        self._state = CircuitState.HALF_OPEN
        self._success_count = 0
        self._failure_count = 0
        self._half_open_calls = 0

        logger.info(f"Circuit breaker '{self.name}': HALF_OPEN - testing recovery")

    def _transition_to_closed(self) -> None:
        """Transition to CLOSED state (must hold lock)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._half_open_calls = 0

        logger.info(f"Circuit breaker '{self.name}': CLOSED - service recovered")

    def get_stats(self) -> dict:
        """Get current circuit breaker statistics.

        Returns:
            Dictionary with state, counts, and timing info
        """
        with self._lock:
            self._update_state()

            return {
                'name': self.name,
                'state': self._state.value,
                'failure_count': self._failure_count,
                'success_count': self._success_count,
                'last_failure_time': self._last_failure_time,
                'half_open_calls': self._half_open_calls,
                'config': {
                    'failure_threshold': self.config.failure_threshold,
                    'success_threshold': self.config.success_threshold,
                    'timeout': self.config.timeout,
                    'half_open_max_calls': self.config.half_open_max_calls
                }
            }
