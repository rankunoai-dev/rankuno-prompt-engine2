"""IP-range verification and the aggregation step that discards addresses."""

from __future__ import annotations

from datetime import UTC, datetime

from src.modules.crawler_logs import ingest as ingest_module
from src.modules.crawler_logs.ingest import aggregate
from src.modules.crawler_logs.parser import Hit, ParseStats
from src.modules.crawler_logs.ranges import BotRanges, bundled_ranges

RANGES = BotRanges(
    {
        "fetched_at": "2026-09-23T10:00:00+00:00",
        "vendors": {
            "openai": {
                "sources": [
                    {
                        "url": "u1",
                        "creationTime": "2026-09-22T02:00:07",
                        "prefixes": ["203.0.113.0/24"],
                    },
                    {
                        "url": "u2",
                        "creationTime": "2026-01-02T11:00:00",
                        "prefixes": ["2001:db8::/32"],
                    },
                ]
            },
            "perplexity": {
                "sources": [
                    {"url": "u3", "creationTime": "2025-02-07", "prefixes": ["198.51.100.0/24"]}
                ]
            },
        },
    }
)


def test_verify_unions_a_vendors_lists_and_is_none_when_unpublished():
    assert RANGES.verify("openai", "203.0.113.9") is True
    assert RANGES.verify("openai", "2001:db8::1") is True
    assert RANGES.verify("openai", "198.51.100.1") is False  # perplexity's, not openai's
    assert RANGES.verify("openai", None) is False and RANGES.verify("openai", "garbage") is False
    assert RANGES.verify("anthropic", "203.0.113.9") is None
    assert RANGES.vendor_of("198.51.100.4") == "perplexity" and RANGES.vendor_of("10.0.0.1") is None
    snap = RANGES.snapshot()
    assert snap.vendors["openai"] == "2026-09-22T02:00:07" and snap.fetched_at is not None


def test_bundled_snapshot_ships_with_the_package():
    ranges = bundled_ranges()
    assert {"openai", "perplexity", "google", "apple"} <= ranges.vendors


def _hit(
    ua: str,
    ip: str | None,
    path: str = "/blog/x",
    status: int = 200,
    host: str | None = None,
    verified_by_cdn: bool = False,
) -> Hit:
    return Hit(
        ts=datetime(2026, 9, 18, 6, 59, 57, tzinfo=UTC),
        host=host,
        path=path,
        method="GET",
        user_agent=ua,
        ip=ip,
        status=status,
        verified_by_cdn=verified_by_cdn,
    )


SEARCH = "Mozilla/5.0 (compatible; OAI-SearchBot/1.0)"
USER = "Mozilla/5.0 (compatible; ChatGPT-User/1.0)"
CLAUDE = "Mozilla/5.0 (compatible; ClaudeBot/1.0)"
BROWSER = "Mozilla/5.0 Chrome/120"


def test_aggregate_keeps_counts_only_and_never_an_address():
    hits = [
        _hit(SEARCH, "203.0.113.9"),  # in range
        _hit(SEARCH, "10.0.0.1"),  # spoofed
        _hit(SEARCH, "203.0.113.9", status=403),
        _hit(SEARCH, "203.0.113.9", status=301),
        _hit(SEARCH, "203.0.113.9", status=304),
        _hit(CLAUDE, "9.9.9.9"),  # unverifiable vendor
        _hit(USER, "203.0.113.9"),  # live fetch: minute rounding
        _hit(BROWSER, "198.51.100.4"),  # stealth: perplexity range, no bot UA
        _hit(BROWSER, "10.0.0.2"),  # ordinary visitor: ignored entirely
        _hit(SEARCH, "203.0.113.9", host="other.example"),  # not the client's host
        _hit(SEARCH, "203.0.113.9", path="/reset/8f3c2a9b1d4e5f60718293a4b5c6d7e8"),
        _hit(SEARCH, "203.0.113.9", host="blog.gep.com", path="/sub"),  # subdomain allowed
    ]
    result = aggregate(hits, client_domains=["gep.com"], ranges=RANGES, stats=ParseStats())
    row = result.rows[("2026-09-18", "OAI-SearchBot", "gep.com/blog/x")]
    assert (row.hits, row.s2xx, row.s304, row.s3xx, row.blocked, row.verified) == (5, 2, 1, 1, 1, 4)
    assert result.rows[("2026-09-18", "ClaudeBot", "gep.com/blog/x")].verified is None
    user_row = result.rows[("2026-09-18", "ChatGPT-User", "gep.com/blog/x")]
    assert user_row.first_seen is not None and user_row.first_seen.second == 0
    assert result.stealth == {("2026-09-18", "perplexity"): 1} and result.stealth_hits == 1
    assert result.hosts_skipped == 1 and result.sensitive_dropped == 1
    assert ("2026-09-18", "OAI-SearchBot", "blog.gep.com/sub") in result.rows
    assert result.no_host == 8  # combined logs carry no host; the project's is assumed
    assert result.verification_basis == "remote_addr"
    rendered = repr(result.rows) + repr(result.stealth)
    assert "203.0.113" not in rendered and "10.0.0" not in rendered


def test_cdn_verification_and_key_cap(monkeypatch):
    stats = ParseStats()
    stats.format = "cloudflare"
    hits = [_hit(SEARCH, None, path=f"/p{i}", verified_by_cdn=True) for i in range(5)]
    monkeypatch.setattr(ingest_module, "MAX_KEYS", 3)
    result = aggregate(hits, client_domains=["gep.com"], ranges=RANGES, stats=stats)
    assert result.keys_truncated and len(result.rows) == 3 and result.verified_hits == 3
    assert result.verification_basis == "cloudflare"


def test_empty_client_domains_never_skip_hosts():
    result = aggregate(
        [_hit(SEARCH, "203.0.113.9", host="anything.example")],
        client_domains=[],
        ranges=RANGES,
        stats=ParseStats(),
    )
    assert result.hits == 1 and result.hosts_skipped == 0
