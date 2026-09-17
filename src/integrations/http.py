"""HTTP plumbing shared by every connector.

Three things live here so no connector reinvents them:

* `json_client()` — one place that sets timeouts and identifies us to vendors.
* `PinnedTransport` — connects to the addresses a `SafeUrl` resolved to, sending
  the original hostname for TLS SNI and the `Host` header. This is what closes
  the DNS-rebinding window `core.url_safety` warns about.
* `check_response()` — maps HTTP status to the platform's error hierarchy so the
  retry policy sees transient failures as transient and everything else as final.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from src.core.errors import IntegrationError, UpstreamClientError
from src.core.robots import DEFAULT_USER_AGENT
from src.core.url_safety import SafeUrl

__all__ = ["PinnedTransport", "check_response", "json_client", "parse_json"]

_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_BODY_EXCERPT = 300


def json_client(
    timeout_s: float,
    *,
    headers: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Build an `httpx.Client` with the platform defaults.

    Args:
        timeout_s: Applied to connect, read and write.
        headers: Extra default headers (authorization, content type).
        transport: Override for tests (`httpx.MockTransport`) or pinning.
    """
    merged = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    return httpx.Client(timeout=timeout_s, headers=merged, transport=transport)


class PinnedTransport(httpx.BaseTransport):
    """Route requests for `safe.host` to one of its pre-resolved addresses.

    The request URL is rewritten to the literal IP while the `Host` header and
    the TLS `sni_hostname` extension keep the original name, so certificate
    validation still succeeds and virtual hosting still works.
    """

    def __init__(self, safe: SafeUrl, inner: httpx.BaseTransport | None = None) -> None:
        """Pin `safe.host` to `safe.resolved_ips[0]`."""
        self._safe = safe
        self._inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Rewrite the destination when it matches the pinned host."""
        if request.url.host.lower() != self._safe.host:
            return self._inner.handle_request(request)

        ip = self._safe.resolved_ips[0]
        pinned_host = f"[{ip}]" if ":" in ip else ip
        pinned = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=pinned_host),
            headers=request.headers,
            content=request.content,
            extensions={**request.extensions, "sni_hostname": self._safe.host},
        )
        pinned.headers["Host"] = self._safe.host
        return self._inner.handle_request(pinned)

    def close(self) -> None:
        """Close the wrapped transport."""
        self._inner.close()


def check_response(service: str, response: httpx.Response) -> None:
    """Raise the right platform error for a non-2xx response.

    Raises:
        IntegrationError: For statuses worth retrying (429, 5xx, timeouts).
        UpstreamClientError: For everything else — the request itself is wrong.
    """
    if response.is_success:
        return
    excerpt = response.text[:_BODY_EXCERPT].replace("\n", " ")
    if response.status_code in _RETRYABLE_STATUSES:
        raise IntegrationError(service, f"HTTP {response.status_code}: {excerpt}")
    raise UpstreamClientError(service, response.status_code, excerpt)


def parse_json(service: str, response: httpx.Response) -> dict[str, Any]:
    """Decode a JSON object body, failing loudly on anything else.

    `Any` is unavoidable at the wire boundary; every caller narrows the shape
    into a `StrictModel` before it leaves the connector.
    """
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamClientError(service, response.status_code, "Non-JSON body.") from exc
    if not isinstance(payload, dict):
        raise UpstreamClientError(service, response.status_code, "JSON body is not an object.")
    return payload
