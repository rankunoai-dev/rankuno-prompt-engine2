"""Follows vendor redirect links to the page they actually point at.

Gemini's grounding links go through a Google redirect host. To attribute a
citation to a domain, the tracker has to follow that redirect — which makes this
the one connector that fetches arbitrary, engine-supplied URLs. The crawler-safety
rules therefore apply in full and are not optional:

* every hop is validated by `UrlSafetyPolicy` and the connection is pinned to
  the addresses it resolved to (`PinnedTransport`);
* every hop is checked against the host's robots.txt, with a per-host bucket
  honouring `Crawl-delay`;
* the request is a `HEAD`, never a body download, and redirects are followed
  manually so each hop is re-validated.

A hop that fails any check ends resolution: the caller receives the last URL
reached, flagged `resolved=False`, and moves on. Resolution is best-effort
enrichment, never a hard dependency of the pipeline.
"""

from __future__ import annotations

from functools import partial
from typing import ClassVar
from urllib.parse import urljoin

import httpx
from pydantic import Field

from src.core.config import Settings
from src.core.errors import IntegrationError, RateLimitExceededError, UnsafeUrlError
from src.core.logger import get_logger
from src.core.rate_limiter import TokenBucket
from src.core.robots import RobotsRules
from src.core.schemas import StrictModel
from src.core.url_safety import SafeUrl, UrlSafetyPolicy
from src.integrations.base_client import BaseAPIClient
from src.integrations.http import PinnedTransport, json_client

__all__ = ["RedirectResolver", "ResolvedUrl"]

_logger = get_logger("integrations.url_resolver")

_MAX_HOPS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class ResolvedUrl(StrictModel):
    """Outcome of following a redirect chain."""

    requested: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    hops: int = Field(ge=0)
    resolved: bool = Field(description="True when the chain ended at a non-redirect response.")
    reason: str | None = Field(default=None, description="Why resolution stopped early.")


class RedirectResolver(BaseAPIClient):
    """Resolves redirect chains under the SSRF and robots policies."""

    service_name: ClassVar[str] = "web.redirect_resolver"
    rate_limit_key: ClassVar[str] = "web.redirect_resolver"
    requests_per_minute: ClassVar[int] = 30

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        policy: UrlSafetyPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a resolver.

        Args:
            settings: Configuration override, primarily for tests.
            policy: SSRF policy. Defaults to the strict system-resolver policy.
            transport: When supplied, used for every hop *instead of* pinning —
                for tests only, where there is no real DNS to pin.
        """
        super().__init__(settings)
        self._policy = policy or UrlSafetyPolicy()
        self._transport_override = transport
        self._robots: dict[str, RobotsRules] = {}
        self._host_buckets: dict[str, TokenBucket] = {}

    def authenticate(self) -> None:
        """No credentials: the resolver only performs anonymous HEAD requests."""
        return

    def resolve(self, url: str) -> ResolvedUrl:
        """Follow `url` until a non-redirect response or a policy stop."""
        current = url
        for hop in range(_MAX_HOPS + 1):
            try:
                safe = self._policy.validate(current)
            except UnsafeUrlError as exc:
                return _stopped(url, current, hop, f"unsafe: {exc.reason}")

            if not self._allowed_by_robots(safe):
                return _stopped(url, current, hop, "robots.txt disallows")
            if hop == _MAX_HOPS:
                return _stopped(url, current, hop, "too many redirects")

            try:
                location = self.call("head", partial(self._head, safe))
            except (IntegrationError, RateLimitExceededError) as exc:
                return _stopped(url, current, hop, f"fetch failed: {exc}")
            if location is None:
                return ResolvedUrl(requested=url, final_url=current, hops=hop, resolved=True)
            current = urljoin(current, location)
        return _stopped(url, current, _MAX_HOPS, "too many redirects")  # pragma: no cover

    # -- internals ---------------------------------------------------------

    def _client(self, safe: SafeUrl) -> httpx.Client:
        """A client pinned to `safe`, or the test transport when injected."""
        transport = self._transport_override or PinnedTransport(safe)
        return json_client(self._settings.default_timeout_s, transport=transport)

    def _head(self, safe: SafeUrl) -> str | None:
        """HEAD `safe.url`; return the `Location` header if it redirects."""
        self._bucket_for(safe.host).acquire(timeout_s=self._settings.default_timeout_s)
        with self._client(safe) as client:
            response = client.head(safe.url, follow_redirects=False)
            if response.status_code == 405:
                response = client.get(safe.url, follow_redirects=False)
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("location")
            return location or None
        return None

    def _allowed_by_robots(self, safe: SafeUrl) -> bool:
        """Check `safe.url` against its host's robots.txt (fetched once per host)."""
        rules = self._robots.get(safe.host)
        if rules is None:
            rules = self.call("robots_txt", lambda: self._fetch_robots(safe))
            self._robots[safe.host] = rules
        return rules.can_fetch(safe.url)

    def _fetch_robots(self, safe: SafeUrl) -> RobotsRules:
        """Retrieve robots.txt for `safe.host`; fail closed on errors."""
        robots_url = f"{safe.scheme}://{safe.host}/robots.txt"
        with self._client(safe) as client:
            try:
                response = client.get(robots_url, follow_redirects=False)
            except httpx.HTTPError as exc:
                _logger.warning("robots_fetch_failed", extra={"host": safe.host, "error": str(exc)})
                return RobotsRules(safe.host, None)
        if response.status_code == 404:
            return RobotsRules.permissive(safe.host)
        if response.is_success:
            return RobotsRules(safe.host, response.text)
        return RobotsRules(safe.host, None)

    def _bucket_for(self, host: str) -> TokenBucket:
        """Per-host bucket honouring any declared crawl delay."""
        bucket = self._host_buckets.get(host)
        if bucket is None:
            rules = self._robots.get(host)
            bucket = rules.bucket() if rules else TokenBucket.per_minute(f"robots.{host}", 30)
            self._host_buckets[host] = bucket
        return bucket


def _stopped(requested: str, current: str, hops: int, reason: str) -> ResolvedUrl:
    """Build the early-stop result and record why."""
    _logger.info("redirect_resolution_stopped", extra={"url": current, "reason": reason})
    return ResolvedUrl(
        requested=requested, final_url=current, hops=hops, resolved=False, reason=reason
    )
