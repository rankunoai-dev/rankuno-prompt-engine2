"""`BaseAPIClient` — the mandatory base for every external API connector.

No module may call an external API directly. Connectors subclass this so that
quota protection, retry/backoff, credential handling and audit logging are
uniform and reviewable in one place.

A subclass declares its `service_name`, its documented quota, and implements
transport. The `call()` wrapper supplies everything else.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry
from src.core.config import Settings, get_settings
from src.core.errors import IntegrationError, UpstreamClientError
from src.core.logger import get_logger
from src.core.rate_limiter import RateLimiterRegistry, TokenBucket
from src.core.retry import with_retries
from src.integrations.usage import ApiCall, current_usage_context, get_usage_ledger

__all__ = ["BaseAPIClient"]

_logger = get_logger("integrations.base_client")

# Connectors to the same vendor share these registries, so two clients pointed
# at the same quota throttle jointly and trip the same breaker.
_SHARED_LIMITERS = RateLimiterRegistry()
_SHARED_BREAKERS = CircuitBreakerRegistry()

ResultT = TypeVar("ResultT")


class BaseAPIClient(ABC):
    """Common behaviour for every outbound API connector.

    Class variables a subclass must set:

    * `service_name` — audit-log identity, e.g. `"google.search_console"`.
    * `rate_limit_key` — shared quota bucket. Clients hitting one vendor quota
      must share a key.
    * `requests_per_minute` — the vendor's documented sustained limit.
    """

    service_name: ClassVar[str]
    rate_limit_key: ClassVar[str]
    requests_per_minute: ClassVar[int] = 60

    def __init__(self, settings: Settings | None = None) -> None:
        """Build a client.

        Args:
            settings: Configuration override, primarily for tests.
        """
        for attr in ("service_name", "rate_limit_key"):
            if not getattr(type(self), attr, None):
                msg = f"{type(self).__name__} must declare a class-level '{attr}'."
                raise TypeError(msg)

        self._settings = settings or get_settings()
        self._bucket: TokenBucket = _SHARED_LIMITERS.get_or_create(
            type(self).rate_limit_key, type(self).requests_per_minute
        )
        self._breaker: CircuitBreaker = _SHARED_BREAKERS.get_or_create(
            type(self).rate_limit_key,
            failure_threshold=self._settings.circuit_failure_threshold,
            cooldown_s=self._settings.circuit_cooldown_s,
        )
        self._usage = get_usage_ledger(self._settings)
        # One connector instance serves many pool threads; the id of the call to
        # enrich must therefore be per thread, or threads would annotate each
        # other's rows.
        self._thread = threading.local()

    @property
    def last_call_id(self) -> str | None:
        """Ledger id of the most recent call made on the current thread."""
        return getattr(self._thread, "last_call_id", None)

    @property
    def available(self) -> bool:
        """False while this vendor's circuit breaker refuses calls."""
        return self._breaker.state.value != "open"

    @property
    def breaker(self) -> CircuitBreaker:
        """The shared breaker for this vendor (read-only use by callers)."""
        return self._breaker

    @abstractmethod
    def authenticate(self) -> None:
        """Acquire or refresh credentials.

        Implementations must read credentials via `self._settings.require(...)`
        so a missing key fails with an actionable message instead of a 401.
        """
        raise NotImplementedError

    def call(
        self, operation: str, func: Callable[[], ResultT], *, estimated_cost_usd: float = 0.0
    ) -> ResultT:
        """Run one outbound request under the platform's standard protections.

        Order matters: the rate limiter runs *before* the retry wrapper, so a
        retry storm cannot bypass the quota it is meant to respect. Every call
        (ok, error, or refused by the breaker) is written to the usage ledger;
        the connector may enrich the row afterwards with `note_usage()`.

        Args:
            operation: Short label for the audit log, e.g. `"query_analytics"`.
            func: Zero-argument callable performing the actual request. Bind
                arguments with a lambda or `functools.partial`.
            estimated_cost_usd: The configured per-call estimate, for the ledger.

        Returns:
            Whatever `func` returns.

        Raises:
            IntegrationError: If the call fails after all retries.
        """
        service = type(self).service_name
        started = time.perf_counter()

        if not self._breaker.allow():
            retry_in = self._breaker.retry_after_s
            _logger.warning(
                "circuit_refused_call",
                extra={"service": service, "op": operation, "retry_after_s": round(retry_in, 1)},
            )
            self._record(operation, "refused", started, estimated_cost_usd, "circuit open")
            raise IntegrationError(service, f"{operation}: circuit open, retry in {retry_in:.0f}s")

        def attempt() -> ResultT:
            self._bucket.acquire(timeout_s=self._settings.default_timeout_s)
            return func()

        try:
            result = with_retries(attempt, max_attempts=self._settings.default_max_retries + 1)
        except UpstreamClientError as exc:
            # Our request was wrong (4xx). The vendor is healthy; do not trip.
            self._breaker.record_success()
            self._record(operation, "error", started, estimated_cost_usd, str(exc))
            raise
        except IntegrationError as exc:
            # Transient and already classified; retries were spent.
            self._breaker.record_failure()
            self._record(operation, "error", started, estimated_cost_usd, str(exc))
            raise
        except Exception as exc:
            self._breaker.record_failure()
            _logger.exception("api_call_failed", extra={"service": service, "op": operation})
            self._record(operation, "error", started, estimated_cost_usd, str(exc))
            raise IntegrationError(service, f"{operation}: {exc}") from exc

        self._breaker.record_success()
        self._record(operation, "ok", started, estimated_cost_usd, None)
        _logger.debug("api_call_ok", extra={"service": service, "op": operation})
        return result

    def _record(
        self, operation: str, status: str, started: float, estimated: float, error: str | None
    ) -> None:
        """Write the ledger row for the call that just finished."""
        context = current_usage_context()
        call = ApiCall(
            vendor=type(self).service_name,
            operation=operation,
            source=context.get("source", "unknown"),
            run_id=context.get("run_id"),
            prompt_id=context.get("prompt_id"),
            engine=context.get("engine"),
            status=status,
            error=error[:300] if error else None,
            latency_ms=(time.perf_counter() - started) * 1000,
            estimated_cost_usd=max(estimated, 0.0),
        )
        self._thread.last_call_id = call.id
        self._usage.record(call)

    def note_usage(self, **fields: Any) -> None:
        """Enrich the most recent call's ledger row (tokens, model, vendor cost, ...)."""
        if self.last_call_id is None:
            return
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            self._usage.update(self.last_call_id, **clean)
