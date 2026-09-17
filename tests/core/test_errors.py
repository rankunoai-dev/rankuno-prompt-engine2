"""Tests for the new error types added in this repository."""

from __future__ import annotations

from src.core.errors import (
    GuardrailViolationError,
    RankunoError,
    UnsafeUrlError,
    UpstreamClientError,
)


def test_unsafe_url_error_is_a_guardrail_violation():
    err = UnsafeUrlError("http://10.0.0.1/", "private")
    assert isinstance(err, GuardrailViolationError)
    assert err.url == "http://10.0.0.1/"
    assert "private" in str(err)


def test_upstream_client_error_carries_status_and_is_not_integration_error():
    from src.core.errors import IntegrationError

    err = UpstreamClientError("openai", 401, "bad key")
    assert isinstance(err, RankunoError)
    assert not isinstance(err, IntegrationError)
    assert err.status_code == 401
    assert "401" in str(err)
