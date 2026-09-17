"""Tests for the `BaseAPIClient` contract every connector inherits."""

from __future__ import annotations

from typing import ClassVar

import pytest

from src.core.config import reset_settings_cache
from src.core.errors import IntegrationError, UpstreamClientError
from src.integrations.base_client import BaseAPIClient


class StubClient(BaseAPIClient):
    """Smallest legal connector."""

    service_name: ClassVar[str] = "stub"
    rate_limit_key: ClassVar[str] = "stub.api"

    def authenticate(self) -> None:
        return


class TestDeclarationContract:
    def test_missing_service_name_fails_at_construction(self, settings):
        class NoService(BaseAPIClient):
            rate_limit_key = "stub.api"

            def authenticate(self) -> None:
                return

        with pytest.raises(TypeError, match="service_name"):
            NoService(settings)

    def test_missing_rate_limit_key_fails_at_construction(self, settings):
        class NoKey(BaseAPIClient):
            service_name = "stub"

            def authenticate(self) -> None:
                return

        with pytest.raises(TypeError, match="rate_limit_key"):
            NoKey(settings)

    def test_abstract_authenticate_must_be_implemented(self, settings):
        class NoAuth(BaseAPIClient):
            service_name = "stub"
            rate_limit_key = "stub.api"

        with pytest.raises(TypeError):
            NoAuth(settings)  # type: ignore[abstract]

    def test_base_authenticate_is_abstract(self, settings):
        with pytest.raises(NotImplementedError):
            BaseAPIClient.authenticate(StubClient(settings))

    def test_settings_default_to_the_process_singleton(self):
        client = StubClient()
        assert client._settings.default_max_retries == 0  # noqa: SLF001 - pinned by conftest

    def test_clients_with_one_quota_key_share_a_bucket(self, settings):
        class Sibling(StubClient):
            service_name = "stub.sibling"

        assert StubClient(settings)._bucket is Sibling(settings)._bucket  # noqa: SLF001


class TestCall:
    def test_returns_the_callable_result(self, settings):
        assert StubClient(settings).call("op", lambda: {"ok": True}) == {"ok": True}

    def test_integration_error_is_reraised_unwrapped(self, settings):
        original = IntegrationError("stub", "HTTP 503: down")

        def fail():
            raise original

        with pytest.raises(IntegrationError) as info:
            StubClient(settings).call("op", fail)
        assert info.value is original

    def test_upstream_client_error_is_reraised_unwrapped(self, settings):
        original = UpstreamClientError("stub", 401, "bad key")

        def fail():
            raise original

        with pytest.raises(UpstreamClientError) as info:
            StubClient(settings).call("op", fail)
        assert info.value is original
        assert info.value.status_code == 401

    def test_other_exceptions_are_wrapped_naming_service_and_operation(self, settings):
        def fail():
            raise RuntimeError("socket exploded")

        with pytest.raises(IntegrationError) as info:
            StubClient(settings).call("fetch_report", fail)
        assert info.value.service == "stub"
        assert info.value.detail == "fetch_report: socket exploded"
        assert isinstance(info.value.__cause__, RuntimeError)

    def test_permanent_errors_are_not_retried(self, settings, monkeypatch):
        monkeypatch.setenv("DEFAULT_MAX_RETRIES", "2")
        reset_settings_cache()
        attempts: list[int] = []

        def fail():
            attempts.append(1)
            raise UpstreamClientError("stub", 400, "malformed")

        with pytest.raises(UpstreamClientError):
            StubClient(settings).call("op", fail)
        assert len(attempts) == 1

    def test_retry_count_follows_injected_settings(self, settings, monkeypatch):
        """`settings.default_max_retries` is 0, so a transient failure gets one attempt."""
        monkeypatch.setenv("DEFAULT_MAX_RETRIES", "1")
        reset_settings_cache()
        attempts: list[int] = []

        def fail():
            attempts.append(1)
            raise IntegrationError("stub", "HTTP 503: down")

        with pytest.raises(IntegrationError):
            StubClient(settings).call("op", fail)
        assert len(attempts) == 1
