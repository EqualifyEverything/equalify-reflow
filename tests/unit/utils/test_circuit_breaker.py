"""Unit tests for circuit breaker implementation."""

import pytest
import time
from unittest.mock import patch

from src.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpen,
    CircuitBreakerConfig
)


@pytest.mark.unit
class TestCircuitBreakerStates:
    """Test circuit breaker state transitions."""

    def test_initial_state_is_closed(self):
        """Circuit breaker should start in CLOSED state."""
        breaker = CircuitBreaker("test")
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_closed
        assert not breaker.is_open
        assert not breaker.is_half_open

    def test_closed_to_open_on_failures(self):
        """Circuit should open after reaching failure threshold."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        # Record failures
        breaker.record_failure()
        assert breaker.is_closed  # Still closed (1/3)

        breaker.record_failure()
        assert breaker.is_closed  # Still closed (2/3)

        breaker.record_failure()
        assert breaker.is_open    # Now open (3/3)

    def test_success_resets_failure_count_in_closed(self):
        """Success in CLOSED state should reset failure count."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_closed

        # Success resets counter
        breaker.record_success()

        # Should take 3 more failures to open
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_closed  # Still closed

        breaker.record_failure()
        assert breaker.is_open    # Now opens

    def test_open_to_half_open_after_timeout(self):
        """Circuit should transition to HALF_OPEN after timeout."""
        breaker = CircuitBreaker("test", failure_threshold=2, timeout=0.1)

        # Trigger open state
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open

        # Wait for timeout
        time.sleep(0.15)

        # Check state (triggers timeout check)
        assert breaker.is_half_open

    def test_half_open_to_closed_on_successes(self):
        """Circuit should close after enough successes in HALF_OPEN."""
        breaker = CircuitBreaker(
            "test",
            failure_threshold=2,
            success_threshold=2,
            timeout=0.1
        )

        # Open circuit
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open

        # Wait for timeout
        time.sleep(0.15)
        assert breaker.is_half_open

        # First success
        breaker.check_state()  # Allow call
        breaker.record_success()
        assert breaker.is_half_open  # Still half-open (1/2)

        # Second success -> closes
        breaker.check_state()
        breaker.record_success()
        assert breaker.is_closed

    def test_half_open_to_open_on_failure(self):
        """Failure in HALF_OPEN should immediately reopen circuit."""
        breaker = CircuitBreaker("test", failure_threshold=2, timeout=0.1)

        # Open circuit
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open

        # Transition to half-open
        time.sleep(0.15)
        assert breaker.is_half_open

        # Failure immediately reopens
        breaker.check_state()
        breaker.record_failure()
        assert breaker.is_open


@pytest.mark.unit
class TestCircuitBreakerBlocking:
    """Test circuit breaker request blocking behavior."""

    def test_check_state_passes_when_closed(self):
        """check_state() should not raise when circuit is closed."""
        breaker = CircuitBreaker("test")
        breaker.check_state()  # Should not raise

    def test_check_state_raises_when_open(self):
        """check_state() should raise CircuitBreakerOpen when open."""
        breaker = CircuitBreaker("test", failure_threshold=1)

        breaker.record_failure()
        assert breaker.is_open

        with pytest.raises(CircuitBreakerOpen) as exc_info:
            breaker.check_state()

        assert "test" in str(exc_info.value)
        assert "is open" in str(exc_info.value)

    def test_half_open_allows_limited_concurrent_calls(self):
        """HALF_OPEN should allow only half_open_max_calls concurrent calls."""
        breaker = CircuitBreaker(
            "test",
            failure_threshold=1,
            timeout=0.1,
            config=CircuitBreakerConfig(half_open_max_calls=2)
        )

        # Open circuit
        breaker.record_failure()
        assert breaker.is_open

        # Transition to half-open
        time.sleep(0.15)
        assert breaker.is_half_open

        # First call allowed
        breaker.check_state()

        # Second call allowed
        breaker.check_state()

        # Third call blocked (max is 2)
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            breaker.check_state()

        assert "half-open" in str(exc_info.value).lower()
        assert "max concurrent calls" in str(exc_info.value).lower()

    def test_half_open_calls_decrement_on_completion(self):
        """Half-open call count should decrement after success/failure."""
        breaker = CircuitBreaker(
            "test",
            failure_threshold=1,
            timeout=0.1,
            config=CircuitBreakerConfig(half_open_max_calls=1)
        )

        # Open and transition to half-open
        breaker.record_failure()
        time.sleep(0.15)
        assert breaker.is_half_open

        # Start call
        breaker.check_state()

        # Complete with success
        breaker.record_success()

        # Should allow another call now
        breaker.check_state()  # Should not raise


@pytest.mark.unit
class TestCircuitBreakerConfiguration:
    """Test circuit breaker configuration options."""

    def test_custom_failure_threshold(self):
        """Test custom failure threshold configuration."""
        breaker = CircuitBreaker("test", failure_threshold=10)

        for i in range(9):
            breaker.record_failure()
            assert breaker.is_closed

        breaker.record_failure()  # 10th failure
        assert breaker.is_open

    def test_custom_success_threshold(self):
        """Test custom success threshold in half-open state."""
        breaker = CircuitBreaker(
            "test",
            failure_threshold=1,
            success_threshold=3,
            timeout=0.1
        )

        # Open circuit
        breaker.record_failure()
        time.sleep(0.15)
        assert breaker.is_half_open

        # Need 3 successes to close
        breaker.check_state()
        breaker.record_success()
        assert breaker.is_half_open

        breaker.check_state()
        breaker.record_success()
        assert breaker.is_half_open

        breaker.check_state()
        breaker.record_success()
        assert breaker.is_closed

    def test_custom_timeout(self):
        """Test custom timeout configuration."""
        breaker = CircuitBreaker("test", failure_threshold=1, timeout=0.5)

        breaker.record_failure()
        assert breaker.is_open

        # Too early
        time.sleep(0.2)
        assert breaker.is_open

        # After timeout
        time.sleep(0.4)
        assert breaker.is_half_open

    def test_config_object(self):
        """Test using CircuitBreakerConfig object."""
        config = CircuitBreakerConfig(
            failure_threshold=7,
            success_threshold=3,
            timeout=120.0,
            half_open_max_calls=5
        )

        breaker = CircuitBreaker("test", config=config)

        stats = breaker.get_stats()
        assert stats['config']['failure_threshold'] == 7
        assert stats['config']['success_threshold'] == 3
        assert stats['config']['timeout'] == 120.0
        assert stats['config']['half_open_max_calls'] == 5


@pytest.mark.unit
class TestCircuitBreakerReset:
    """Test manual reset functionality."""

    def test_manual_reset_from_open(self):
        """Manual reset should close circuit from OPEN state."""
        breaker = CircuitBreaker("test", failure_threshold=1)

        breaker.record_failure()
        assert breaker.is_open

        breaker.reset()
        assert breaker.is_closed

    def test_manual_reset_clears_counters(self):
        """Reset should clear failure and success counters."""
        breaker = CircuitBreaker("test", failure_threshold=5)

        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()

        breaker.reset()

        stats = breaker.get_stats()
        assert stats['failure_count'] == 0
        assert stats['success_count'] == 0
        assert stats['last_failure_time'] is None


@pytest.mark.unit
class TestCircuitBreakerStats:
    """Test statistics and monitoring functionality."""

    def test_get_stats_returns_current_state(self):
        """get_stats() should return current circuit state."""
        breaker = CircuitBreaker("test-circuit")

        stats = breaker.get_stats()

        assert stats['name'] == "test-circuit"
        assert stats['state'] == CircuitState.CLOSED.value
        assert stats['failure_count'] == 0
        assert stats['success_count'] == 0
        assert stats['last_failure_time'] is None

    def test_get_stats_includes_failure_count(self):
        """Stats should include current failure count."""
        breaker = CircuitBreaker("test", failure_threshold=5)

        breaker.record_failure()
        breaker.record_failure()

        stats = breaker.get_stats()
        assert stats['failure_count'] == 2
        assert stats['last_failure_time'] is not None

    def test_get_stats_includes_config(self):
        """Stats should include configuration values."""
        breaker = CircuitBreaker(
            "test",
            failure_threshold=10,
            success_threshold=3,
            timeout=90.0
        )

        stats = breaker.get_stats()

        assert stats['config']['failure_threshold'] == 10
        assert stats['config']['success_threshold'] == 3
        assert stats['config']['timeout'] == 90.0


@pytest.mark.unit
class TestCircuitBreakerThreadSafety:
    """Test thread safety of circuit breaker operations.

    Note: These are basic tests. Full thread-safety testing would require
    concurrent operations from multiple threads.
    """

    def test_concurrent_check_state_safe(self):
        """Multiple check_state() calls should be safe."""
        breaker = CircuitBreaker("test")

        # Should not raise due to race conditions
        for _ in range(100):
            breaker.check_state()

    def test_concurrent_record_operations_safe(self):
        """Concurrent record_success/record_failure should be safe."""
        breaker = CircuitBreaker("test", failure_threshold=50)

        # Simulate mixed success/failure operations
        for i in range(100):
            if i % 3 == 0:
                breaker.record_success()
            else:
                breaker.record_failure()

        # Should have consistent state
        stats = breaker.get_stats()
        assert isinstance(stats['failure_count'], int)
        assert stats['state'] in [s.value for s in CircuitState]


@pytest.mark.unit
class TestCircuitBreakerEdgeCases:
    """Test edge cases and error conditions."""

    def test_zero_failure_threshold_not_recommended(self):
        """Circuit with zero threshold opens on first failure."""
        breaker = CircuitBreaker("test", failure_threshold=0)
        assert breaker.is_closed  # Starts closed

        # First failure opens immediately (0 threshold)
        breaker.record_failure()
        assert breaker.is_open

    def test_very_short_timeout(self):
        """Circuit should handle very short timeouts."""
        breaker = CircuitBreaker("test", failure_threshold=1, timeout=0.01)

        breaker.record_failure()
        assert breaker.is_open

        time.sleep(0.02)
        assert breaker.is_half_open

    def test_state_property_triggers_update(self):
        """Accessing .state property should trigger timeout check."""
        breaker = CircuitBreaker("test", failure_threshold=1, timeout=0.1)

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.15)

        # Accessing state should trigger transition
        assert breaker.state == CircuitState.HALF_OPEN

    def test_multiple_resets_safe(self):
        """Multiple resets should be safe."""
        breaker = CircuitBreaker("test")

        for _ in range(10):
            breaker.reset()
            assert breaker.is_closed

    def test_success_in_closed_with_no_failures(self):
        """Recording success when no failures is safe."""
        breaker = CircuitBreaker("test")

        breaker.record_success()
        breaker.record_success()

        assert breaker.is_closed
        stats = breaker.get_stats()
        assert stats['failure_count'] == 0
