"""Tests for the shared HTTP plumbing: client defaults, pinning and status mapping."""

from __future__ import annotations

import httpx
import pytest

from src.core.errors import IntegrationError, UpstreamClientError
from src.core.robots import DEFAULT_USER_AGENT
from src.core.url_safety import SafeUrl
from src.integrations.http import PinnedTransport, check_response, json_client, parse_json

_REQUEST = httpx.Request("GET", "https://api.example/")


def _response(status: int, **kwargs) -> httpx.Response:
    return httpx.Response(status, request=_REQUEST, **kwargs)


class TestJsonClient:
    def test_identifies_the_platform_and_accepts_json(self):
        with json_client(2.0) as client:
            assert client.headers["user-agent"] == DEFAULT_USER_AGENT
            assert client.headers["accept"] == "application/json"
            assert client.timeout.connect == 2.0
            assert client.timeout.read == 2.0

    def test_extra_headers_are_merged_and_can_override_defaults(self):
        headers = {"Authorization": "Bearer abc", "Accept": "text/csv"}
        with json_client(1.0, headers=headers) as client:
            assert client.headers["authorization"] == "Bearer abc"
            assert client.headers["accept"] == "text/csv"
            assert client.headers["user-agent"] == DEFAULT_USER_AGENT

    def test_transport_override_is_used(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ua": request.headers["user-agent"]})

        with json_client(1.0, transport=httpx.MockTransport(handler)) as client:
            assert client.get("https://api.example/").json() == {"ua": DEFAULT_USER_AGENT}


class TestCheckResponse:
    @pytest.mark.parametrize("status", [200, 201, 204])
    def test_success_statuses_return_silently(self, status):
        assert check_response("svc", _response(status)) is None

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_retryable_statuses_raise_integration_error(self, status):
        with pytest.raises(IntegrationError) as info:
            check_response("svc", _response(status, text="slow\ndown"))
        assert info.value.service == "svc"
        assert f"HTTP {status}" in info.value.detail
        # Newlines are flattened so the excerpt stays on one log line.
        assert "slow down" in info.value.detail

    @pytest.mark.parametrize("status", [400, 401, 404])
    def test_client_errors_raise_upstream_client_error(self, status):
        with pytest.raises(UpstreamClientError) as info:
            check_response("svc", _response(status, text="bad key"))
        assert info.value.status_code == status
        assert info.value.service == "svc"
        assert info.value.detail == "bad key"
        assert not isinstance(info.value, IntegrationError)

    def test_body_excerpt_is_truncated(self):
        with pytest.raises(UpstreamClientError) as info:
            check_response("svc", _response(403, text="x" * 1000))
        assert len(info.value.detail) == 300


class TestParseJson:
    def test_returns_object_bodies(self):
        assert parse_json("svc", _response(200, json={"a": 1})) == {"a": 1}

    def test_non_json_body_is_a_client_error(self):
        with pytest.raises(UpstreamClientError, match="Non-JSON") as info:
            parse_json("svc", _response(200, text="<html>oops</html>"))
        assert info.value.status_code == 200

    def test_json_array_body_is_a_client_error(self):
        with pytest.raises(UpstreamClientError, match="not an object"):
            parse_json("svc", _response(200, json=[1, 2, 3]))


def _safe(host: str, ip: str) -> SafeUrl:
    return SafeUrl(
        url=f"https://{host}/path", scheme="https", host=host, port=443, resolved_ips=(ip,)
    )


class TestPinnedTransport:
    @pytest.fixture
    def captured(self) -> list[httpx.Request]:
        return []

    @pytest.fixture
    def inner(self, captured) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"ok": True})

        return httpx.MockTransport(handler)

    def test_rewrites_host_to_resolved_ip_and_keeps_identity(self, inner, captured):
        safe = _safe("example.com", "93.184.216.34")
        with httpx.Client(transport=PinnedTransport(safe, inner)) as client:
            response = client.get("https://Example.com/path?q=1", headers={"X-Test": "1"})

        assert response.status_code == 200
        request = captured[0]
        assert request.url.host == "93.184.216.34"
        assert request.url.path == "/path"
        assert request.url.query == b"q=1"
        assert request.headers["host"] == "example.com"
        assert request.headers["x-test"] == "1"
        assert request.extensions["sni_hostname"] == "example.com"

    def test_ipv6_addresses_are_bracketed(self, inner, captured):
        ip = "2606:2800:220:1:248:1893:25c8:1946"
        with httpx.Client(transport=PinnedTransport(_safe("v6.example", ip), inner)) as client:
            client.get("https://v6.example/path")

        request = captured[0]
        assert request.url.host == ip
        assert str(request.url) == f"https://[{ip}]/path"
        assert request.headers["host"] == "v6.example"

    def test_other_hosts_pass_through_untouched(self, inner, captured):
        safe = _safe("example.com", "93.184.216.34")
        with httpx.Client(transport=PinnedTransport(safe, inner)) as client:
            client.get("https://other.example/x")

        request = captured[0]
        assert request.url.host == "other.example"
        assert request.headers["host"] == "other.example"
        assert "sni_hostname" not in request.extensions

    def test_request_body_survives_pinning(self, inner, captured):
        safe = _safe("example.com", "93.184.216.34")
        with httpx.Client(transport=PinnedTransport(safe, inner)) as client:
            client.post("https://example.com/path", json={"k": "v"})

        assert captured[0].method == "POST"
        assert captured[0].content == b'{"k":"v"}'

    def test_close_closes_the_inner_transport(self):
        class Inner(httpx.BaseTransport):
            closed = False

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200)

            def close(self) -> None:
                self.closed = True

        inner = Inner()
        PinnedTransport(_safe("example.com", "93.184.216.34"), inner).close()
        assert inner.closed is True
