"""Circuit-breaker integration in `BaseAPIClient.call()`."""

from __future__ import annotations

from typing import ClassVar

import pytest

from src.core.errors import IntegrationError, UpstreamClientError
from src.integrations.base_client import BaseAPIClient


class Stub(BaseAPIClient):
    service_name: ClassVar[str] = "stub"
    rate_limit_key: ClassVar[str] = "stub.breaker"

    def authenticate(self) -> None:
        return


def _settings(settings, threshold: int = 2):
    return settings.model_copy(
        update={"circuit_failure_threshold": threshold, "circuit_cooldown_s": 30.0}
    )


def test_transient_failures_open_the_breaker_and_refuse_fast(settings):
    client = Stub(_settings(settings))
    attempts: list[int] = []

    def boom() -> None:
        attempts.append(1)
        raise IntegrationError("stub", "503")

    for _ in range(2):
        with pytest.raises(IntegrationError):
            client.call("op", boom)
    assert client.available is False

    with pytest.raises(IntegrationError, match="circuit open"):
        client.call("op", boom)
    assert len(attempts) == 2  # third call never reached the function


def test_client_errors_do_not_trip_the_breaker(settings):
    client = Stub(_settings(settings, threshold=1))

    def bad_request() -> None:
        raise UpstreamClientError("stub", 400, "malformed")

    with pytest.raises(UpstreamClientError):
        client.call("op", bad_request)
    assert client.available is True


def test_success_after_failure_keeps_breaker_closed(settings):
    client = Stub(_settings(settings, threshold=2))

    def boom() -> None:
        raise IntegrationError("stub", "503")

    with pytest.raises(IntegrationError):
        client.call("op", boom)
    assert client.call("op", lambda: "ok") == "ok"
    with pytest.raises(IntegrationError):
        client.call("op", boom)
    assert client.available is True


def test_unexpected_exceptions_count_as_transient(settings):
    client = Stub(_settings(settings, threshold=1))

    def crash() -> None:
        raise RuntimeError("socket reset")

    with pytest.raises(IntegrationError):
        client.call("op", crash)
    assert client.available is False
    assert client.breaker.retry_after_s > 0
