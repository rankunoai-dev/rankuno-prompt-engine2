"""SSRF guard for every outbound fetch.

Design stance: a URL is untrusted until it has been turned into a `SafeUrl`.
Connectors that follow redirects (Gemini grounding links, client landing pages)
would otherwise be an open proxy into private networks: an attacker-controlled
page can redirect to `http://169.254.169.254/` or `http://10.0.0.5/admin`.

`UrlSafetyPolicy.validate()` therefore checks the scheme, the host and — after
resolving DNS once — every resolved address. The resolved addresses travel with
the `SafeUrl` so the HTTP layer can pin the connection to them and close the
DNS-rebinding window between validation and connect.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from pydantic import Field

from src.core.errors import UnsafeUrlError
from src.core.logger import get_logger
from src.core.schemas import StrictModel

__all__ = ["Resolver", "SafeUrl", "UrlSafetyPolicy", "system_resolver"]

_logger = get_logger("core.url_safety")

Resolver = Callable[[str], tuple[str, ...]]
"""Maps a hostname to the IP addresses it resolves to."""

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_URL_LENGTH = 2048


class SafeUrl(StrictModel):
    """A URL that passed `UrlSafetyPolicy.validate()`.

    Only this type may be handed to an HTTP client. Constructing one directly is
    possible but pointless — the HTTP layer treats the `resolved_ips` as the
    pinned connection targets, so a hand-built instance simply pins to itself.
    """

    url: str = Field(min_length=1, max_length=_MAX_URL_LENGTH)
    scheme: str
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    resolved_ips: tuple[str, ...] = Field(min_length=1)


def system_resolver(host: str) -> tuple[str, ...]:
    """Resolve `host` with the OS resolver, returning every A/AAAA answer.

    Every answer is returned rather than the first one because a host that
    resolves to one public and one private address is exactly the rebinding
    trick the policy exists to catch.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(host, f"DNS resolution failed: {exc}") from exc
    return tuple(sorted({str(info[4][0]) for info in infos}))


class UrlSafetyPolicy:
    """Decides which URLs an outbound fetch may target."""

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        allow_private_networks: bool = False,
        blocked_hosts: frozenset[str] = frozenset(),
    ) -> None:
        """Build a policy.

        Args:
            resolver: DNS lookup function. Injected so tests never hit the network.
            allow_private_networks: Permit RFC 1918 / loopback targets. Only
                legitimate for local development against a stub server.
            blocked_hosts: Hostnames refused outright, regardless of address.
        """
        self._resolver = resolver
        self._allow_private = allow_private_networks
        self._blocked_hosts = frozenset(h.lower() for h in blocked_hosts)

    def validate(self, url: str) -> SafeUrl:
        """Validate `url` and return the pinned, fetchable form.

        Raises:
            UnsafeUrlError: If the scheme, host or any resolved address is
                disallowed.
        """
        if len(url) > _MAX_URL_LENGTH:
            raise UnsafeUrlError(url[:80], "URL exceeds maximum length.")

        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise UnsafeUrlError(url, f"Scheme '{scheme or '<none>'}' is not allowed.")

        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise UnsafeUrlError(url, "URL has no host.")
        if parts.username or parts.password:
            raise UnsafeUrlError(url, "Credentials embedded in URLs are not allowed.")
        if host in self._blocked_hosts:
            raise UnsafeUrlError(url, f"Host '{host}' is blocked by policy.")

        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise UnsafeUrlError(url, "Invalid port.") from exc

        ips = self._resolve(host)
        for ip in ips:
            self._check_address(url, ip)

        _logger.debug("url_validated", extra={"host": host, "ips": list(ips)})
        return SafeUrl(url=url, scheme=scheme, host=host, port=port, resolved_ips=ips)

    def _resolve(self, host: str) -> tuple[str, ...]:
        """Resolve a hostname, or pass a literal IP straight through."""
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            ips = self._resolver(host)
            if not ips:
                raise UnsafeUrlError(host, "Host resolved to no addresses.") from None
            return ips
        return (str(literal),)

    def _check_address(self, url: str, ip: str) -> None:
        """Refuse any address that is not globally routable."""
        addr = ipaddress.ip_address(ip)
        if self._allow_private:
            return
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        ):
            _logger.warning("unsafe_url_refused", extra={"url": url, "ip": ip})
            raise UnsafeUrlError(url, f"Resolves to non-public address {ip}.")
