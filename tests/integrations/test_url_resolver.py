"""Tests for redirect resolution under the SSRF and robots policies.

DNS is faked through `UrlSafetyPolicy(resolver=...)` and HTTP through an
`httpx.MockTransport`, so every hop is exercised without a network.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.url_safety import UrlSafetyPolicy
from src.integrations.url_resolver import RedirectResolver, ResolvedUrl

PUBLIC_IPS = {
    "example.com": ("93.184.216.34",),
    "cdn.example.net": ("93.184.216.35",),
    "internal.example": ("10.0.0.5",),
}
ALLOW_ALL = "User-agent: *\nAllow: /\n"


def fake_dns(host: str) -> tuple[str, ...]:
    return PUBLIC_IPS.get(host, ())


class FakeWeb:
    """Routes requests by (method, host, path) and records everything it served."""

    def __init__(self) -> None:
        self.robots: dict[str, httpx.Response | Exception] = {}
        self.pages: dict[tuple[str, str, str], httpx.Response] = {}
        self.requests: list[httpx.Request] = []

    def robots_txt(self, host: str, status: int = 200, text: str = ALLOW_ALL) -> None:
        self.robots[host] = httpx.Response(status, text=text)

    def page(self, method: str, url: str, status: int, location: str | None = None) -> None:
        parsed = httpx.URL(url)
        headers = {"Location": location} if location else {}
        self.pages[(method, parsed.host, parsed.path)] = httpx.Response(status, headers=headers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            outcome = self.robots.get(request.url.host)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome or httpx.Response(200, text=ALLOW_ALL)
        return self.pages[(request.method, request.url.host, request.url.path)]

    def count(self, method: str, path: str, host: str | None = None) -> int:
        return sum(
            1
            for r in self.requests
            if r.method == method and r.url.path == path and (host is None or r.url.host == host)
        )


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def resolver(settings, web) -> RedirectResolver:
    return RedirectResolver(
        settings,
        policy=UrlSafetyPolicy(resolver=fake_dns),
        transport=httpx.MockTransport(web),
    )


def test_single_hop_is_resolved_in_place(resolver, web):
    web.page("HEAD", "https://example.com/a", 200)
    result = resolver.resolve("https://example.com/a")

    assert result == ResolvedUrl(
        requested="https://example.com/a", final_url="https://example.com/a", hops=0, resolved=True
    )
    assert web.count("GET", "/robots.txt") == 1
    assert web.count("HEAD", "/a") == 1


def test_two_hop_chain_reports_final_url_and_hops(resolver, web):
    web.page("HEAD", "https://example.com/a", 302, "https://cdn.example.net/b")
    web.page("HEAD", "https://cdn.example.net/b", 301, "https://example.com/final")
    web.page("HEAD", "https://example.com/final", 200)
    result = resolver.resolve("https://example.com/a")

    assert result.resolved is True
    assert result.final_url == "https://example.com/final"
    assert result.hops == 2
    assert result.reason is None
    # robots.txt is cached per host even when the chain revisits one.
    assert web.count("GET", "/robots.txt", host="example.com") == 1
    assert web.count("GET", "/robots.txt", host="cdn.example.net") == 1


def test_robots_disallow_stops_resolution_before_any_head(resolver, web):
    web.robots_txt("example.com", text="User-agent: *\nDisallow: /private/\n")
    web.page("HEAD", "https://example.com/private/x", 200)
    result = resolver.resolve("https://example.com/private/x")

    assert result.resolved is False
    assert result.hops == 0
    assert "robots" in result.reason
    assert web.count("HEAD", "/private/x") == 0


def test_robots_server_error_fails_closed(resolver, web):
    web.robots_txt("example.com", status=500, text="oops")
    web.page("HEAD", "https://example.com/a", 200)
    result = resolver.resolve("https://example.com/a")

    assert result.resolved is False
    assert "robots" in result.reason
    assert web.count("HEAD", "/a") == 0


def test_robots_transport_failure_fails_closed(resolver, web):
    web.robots["example.com"] = httpx.ConnectError("refused")
    web.page("HEAD", "https://example.com/a", 200)
    result = resolver.resolve("https://example.com/a")

    assert result.resolved is False
    assert "robots" in result.reason


def test_missing_robots_is_permissive(resolver, web):
    web.robots_txt("example.com", status=404, text="Not found")
    web.page("HEAD", "https://example.com/a", 200)
    assert resolver.resolve("https://example.com/a").resolved is True


def test_hop_to_unresolvable_host_is_unsafe(resolver, web):
    web.page("HEAD", "https://example.com/a", 302, "https://nowhere.invalid/x")
    result = resolver.resolve("https://example.com/a")

    assert result.resolved is False
    assert result.reason.startswith("unsafe")
    assert result.hops == 1
    assert result.final_url == "https://nowhere.invalid/x"
    assert web.count("GET", "/robots.txt", host="nowhere.invalid") == 0


def test_hop_to_private_address_is_unsafe(resolver, web):
    web.page("HEAD", "https://example.com/a", 307, "http://internal.example/admin")
    result = resolver.resolve("https://example.com/a")

    assert result.resolved is False
    assert result.reason.startswith("unsafe")
    assert "10.0.0.5" in result.reason
    assert web.count("HEAD", "/admin") == 0


def test_initial_url_is_validated_too(resolver, web):
    result = resolver.resolve("ftp://example.com/a")
    assert result.resolved is False
    assert result.reason.startswith("unsafe")
    assert result.hops == 0
    assert web.requests == []


def test_more_than_five_redirects_gives_up(resolver, web):
    for n in range(6):
        web.page("HEAD", f"https://example.com/r{n}", 302, f"https://example.com/r{n + 1}")
    result = resolver.resolve("https://example.com/r0")

    assert result.resolved is False
    assert result.reason == "too many redirects"
    assert result.hops == 5
    assert result.final_url == "https://example.com/r5"
    assert web.count("HEAD", "/r5") == 0


def test_405_on_head_falls_back_to_get(resolver, web):
    web.page("HEAD", "https://example.com/legacy", 405)
    web.page("GET", "https://example.com/legacy", 302, "https://example.com/dest")
    web.page("HEAD", "https://example.com/dest", 200)
    result = resolver.resolve("https://example.com/legacy")

    assert result.resolved is True
    assert result.final_url == "https://example.com/dest"
    assert result.hops == 1
    assert web.count("GET", "/legacy") == 1


def test_relative_location_is_joined_against_the_current_url(resolver, web):
    web.page("HEAD", "https://example.com/dir/page", 303, "/other/dest")
    web.page("HEAD", "https://example.com/other/dest", 308, "sibling?x=1")
    web.page("HEAD", "https://example.com/other/sibling", 200)
    result = resolver.resolve("https://example.com/dir/page")

    assert result.resolved is True
    assert result.final_url == "https://example.com/other/sibling?x=1"
    assert result.hops == 2


def test_redirect_without_location_is_treated_as_final(resolver, web):
    web.page("HEAD", "https://example.com/odd", 302)
    result = resolver.resolve("https://example.com/odd")
    assert result.resolved is True
    assert result.hops == 0


def test_robots_is_fetched_once_per_host_across_resolves(resolver, web):
    web.page("HEAD", "https://example.com/a", 200)
    web.page("HEAD", "https://example.com/b", 200)
    resolver.resolve("https://example.com/a")
    resolver.resolve("https://example.com/b")

    assert web.count("GET", "/robots.txt") == 1
    assert web.count("HEAD", "/a") == 1
    assert web.count("HEAD", "/b") == 1


def test_crawl_delay_builds_a_single_slot_bucket(resolver, web):
    web.robots_txt("example.com", text="User-agent: *\nCrawl-delay: 10\n")
    web.page("HEAD", "https://example.com/a", 200)
    assert resolver.resolve("https://example.com/a").resolved is True
    bucket = resolver._host_buckets["example.com"]  # noqa: SLF001
    assert bucket.capacity == 1
    assert bucket.refill_per_second == pytest.approx(0.1)


def test_requests_identify_the_tracker(resolver, web):
    web.page("HEAD", "https://example.com/a", 200)
    resolver.resolve("https://example.com/a")
    assert all("RankUno" in r.headers["user-agent"] for r in web.requests)


def test_authenticate_is_a_no_op(resolver):
    assert resolver.authenticate() is None
