"""Tests for the SSRF guard."""

from __future__ import annotations

import pytest

from src.core.errors import UnsafeUrlError
from src.core.url_safety import SafeUrl, UrlSafetyPolicy, system_resolver


def _resolver(mapping: dict[str, tuple[str, ...]]):
    def resolve(host: str) -> tuple[str, ...]:
        return mapping.get(host, ())

    return resolve


@pytest.fixture
def policy() -> UrlSafetyPolicy:
    return UrlSafetyPolicy(
        resolver=_resolver(
            {
                "example.com": ("93.184.216.34",),
                "dual.example": ("93.184.216.34", "10.0.0.5"),
                "v6.example": ("2606:2800:220:1:248:1893:25c8:1946",),
            }
        )
    )


def test_public_https_url_is_validated_and_pinned(policy):
    safe = policy.validate("https://Example.com/path?q=1")
    assert isinstance(safe, SafeUrl)
    assert safe.host == "example.com"
    assert safe.port == 443
    assert safe.resolved_ips == ("93.184.216.34",)


def test_default_port_follows_scheme(policy):
    assert policy.validate("http://example.com/").port == 80
    assert policy.validate("https://example.com:8443/").port == 8443


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "example.com/no-scheme",
    ],
)
def test_non_http_schemes_are_refused(policy, url):
    with pytest.raises(UnsafeUrlError):
        policy.validate(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.5/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_literal_private_addresses_are_refused(policy, url):
    with pytest.raises(UnsafeUrlError, match="non-public"):
        policy.validate(url)


def test_host_resolving_to_any_private_address_is_refused(policy):
    """One public + one private answer is the classic rebinding setup."""
    with pytest.raises(UnsafeUrlError, match="10.0.0.5"):
        policy.validate("https://dual.example/")


def test_ipv6_public_address_is_allowed(policy):
    safe = policy.validate("https://v6.example/")
    assert safe.resolved_ips[0].startswith("2606:")


def test_unresolvable_host_is_refused(policy):
    with pytest.raises(UnsafeUrlError, match="no addresses"):
        policy.validate("https://nowhere.invalid/")


def test_embedded_credentials_are_refused(policy):
    with pytest.raises(UnsafeUrlError, match="Credentials"):
        policy.validate("https://user:pw@example.com/")


def test_blocked_hosts_are_refused_before_dns():
    policy = UrlSafetyPolicy(resolver=_resolver({}), blocked_hosts=frozenset({"Bad.example"}))
    with pytest.raises(UnsafeUrlError, match="blocked"):
        policy.validate("https://bad.example/")


def test_missing_host_is_refused(policy):
    with pytest.raises(UnsafeUrlError, match="no host"):
        policy.validate("https:///path")


def test_overlong_url_is_refused(policy):
    with pytest.raises(UnsafeUrlError, match="length"):
        policy.validate("https://example.com/" + "a" * 3000)


def test_invalid_port_is_refused(policy):
    with pytest.raises(UnsafeUrlError):
        policy.validate("https://example.com:99999/")


def test_private_networks_can_be_allowed_for_local_development():
    policy = UrlSafetyPolicy(resolver=_resolver({}), allow_private_networks=True)
    assert policy.validate("http://127.0.0.1:8000/").resolved_ips == ("127.0.0.1",)


def test_system_resolver_reports_failure_as_unsafe():
    with pytest.raises(UnsafeUrlError, match="DNS"):
        system_resolver("this-host-does-not-exist.invalid")
