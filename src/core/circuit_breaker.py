"""Circuit breaker for outbound vendors (closed → open → half-open).

Retry-with-backoff protects one call. It does nothing for the next ninety calls
to a vendor that is down: each one still waits through its own backoff and
burns quota. A breaker remembers that the vendor is failing and refuses calls
outright for a cooldown period, then lets a single trial through to see whether
it recovered.

Only *transient* failures trip the breaker. A 400 or 401 means our request is
wrong, not that the vendor is unavailable, and must not lock out a healthy API.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import StrEnum

from src.core.logger import get_logger

__all__ = ["CircuitBreaker", "CircuitBreakerRegistry", "CircuitState"]

_logger = get_logger("core.circuit_breaker")


class CircuitState(StrEnum):
    """Breaker states. Governance-style lowercase, like `ExecutionStatus`."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe breaker for one upstream key."""

    def __init__(
        self,
        key: str,
        *,
        failure_threshold: int = 5,
        cooldown_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build a closed breaker.

        Args:
            key: Upstream identity, for logs.
            failure_threshold: Consecutive transient failures that open the breaker.
            cooldown_s: How long the breaker stays open before one trial call.
            clock: Monotonic time source; injectable for tests.
        """
        if failure_threshold < 1:
            msg = "failure_threshold must be at least 1."
            raise ValueError(msg)
        if cooldown_s <= 0:
            msg = "cooldown_s must be positive."
            raise ValueError(msg)
        self.key = key
        self._threshold = failure_threshold
        self._cooldown = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    @property
    def state(self) -> CircuitState:
        """Current state, promoting OPEN to HALF_OPEN once the cooldown elapsed."""
        with self._lock:
            self._promote_locked()
            return self._state

    @property
    def retry_after_s(self) -> float:
        """Seconds until an open breaker permits a trial. Zero when not open."""
        with self._lock:
            if self._state is not CircuitState.OPEN:
                return 0.0
            return max(0.0, self._opened_at + self._cooldown - self._clock())

    def allow(self) -> bool:
        """Return True if a call may proceed now.

        In HALF_OPEN exactly one trial is admitted; further callers are refused
        until that trial reports success or failure.
        """
        with self._lock:
            self._promote_locked()
            if self._state is CircuitState.CLOSED:
                return True
            if self._state is CircuitState.HALF_OPEN and not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            return False

    def record_success(self) -> None:
        """Close the breaker and clear the failure count."""
        with self._lock:
            if self._state is not CircuitState.CLOSED:
                _logger.info("circuit_closed", extra={"key": self.key})
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._trial_in_flight = False

    def record_failure(self) -> None:
        """Count a transient failure; open the breaker at the threshold."""
        with self._lock:
            self._failures += 1
            self._trial_in_flight = False
            if self._state is CircuitState.HALF_OPEN or self._failures >= self._threshold:
                if self._state is not CircuitState.OPEN:
                    _logger.warning(
                        "circuit_opened",
                        extra={"key": self.key, "failures": self._failures},
                    )
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()

    def reset(self) -> None:
        """Force-close. Tests and explicit operator action only."""
        self.record_success()

    def _promote_locked(self) -> None:
        """OPEN → HALF_OPEN once the cooldown has elapsed. Caller holds the lock."""
        if self._state is CircuitState.OPEN and self._clock() >= self._opened_at + self._cooldown:
            self._state = CircuitState.HALF_OPEN
            self._trial_in_flight = False
            _logger.info("circuit_half_open", extra={"key": self.key})


class CircuitBreakerRegistry:
    """Process-wide map of upstream key to breaker."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self, key: str, *, failure_threshold: int = 5, cooldown_s: float = 120.0
    ) -> CircuitBreaker:
        """Return the breaker for `key`, creating it on first use."""
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(
                    key, failure_threshold=failure_threshold, cooldown_s=cooldown_s
                )
                self._breakers[key] = breaker
            return breaker

    def reset(self) -> None:
        """Drop all breakers. Tests only."""
        with self._lock:
            self._breakers.clear()
