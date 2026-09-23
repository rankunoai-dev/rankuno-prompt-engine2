"""Turn parsed hits into per-day aggregates — the only thing that gets stored.

Addresses are consulted here for verification and discarded. Paths that look
like tokens or addresses are dropped. Timestamps of user-triggered fetches are
rounded to the minute so a human's click is not recorded to the second.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from src.core.domains import domain_matches, normalize_domain
from src.modules.crawler_logs.bots import LIVE_FETCH, BotSpec, classify
from src.modules.crawler_logs.normalise import is_sensitive, url_key
from src.modules.crawler_logs.parser import Hit, ParseStats
from src.modules.crawler_logs.ranges import BotRanges

__all__ = ["AggregateRow", "IngestResult", "aggregate"]

MAX_KEYS = 200_000


@dataclass
class AggregateRow:
    """Counts for one (day, bot, page)."""

    day: str
    bot: str
    url_key: str
    hits: int = 0
    s2xx: int = 0
    s304: int = 0
    s3xx: int = 0
    s4xx: int = 0
    s5xx: int = 0
    blocked: int = 0
    verified: int | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass
class IngestResult:
    """Everything one import produces, ready for the store."""

    stats: ParseStats
    rows: dict[tuple[str, str, str], AggregateRow] = field(default_factory=dict)
    stealth: dict[tuple[str, str], int] = field(default_factory=dict)
    matched: int = 0
    hits: int = 0
    verified_hits: int = 0
    stealth_hits: int = 0
    hosts_skipped: int = 0
    no_host: int = 0
    sensitive_dropped: int = 0
    keys_truncated: bool = False
    cdn_verified: bool = False

    @property
    def verification_basis(self) -> str:
        """How addresses were obtained, so a zero verified count can be explained."""
        if self.cdn_verified:
            return "cloudflare"
        if self.stats.xff_seen:
            return "xff"
        return "remote_addr" if self.stats.format == "combined" or self.hits else "none"


def _status_bucket(row: AggregateRow, status: int, weight: int) -> None:
    if status == 304:
        row.s304 += weight
    elif 200 <= status < 300:
        row.s2xx += weight
    elif 300 <= status < 400:
        row.s3xx += weight
    elif status in (403, 429):
        row.blocked += weight
        row.s4xx += weight
    elif 400 <= status < 500:
        row.s4xx += weight
    elif status >= 500:
        row.s5xx += weight


def _round(ts: datetime, spec: BotSpec) -> datetime:
    return ts.replace(second=0, microsecond=0) if spec.purpose == LIVE_FETCH else ts


def aggregate(
    hits: Iterable[Hit],
    *,
    client_domains: list[str],
    ranges: BotRanges,
    stats: ParseStats,
) -> IngestResult:
    """Fold hits into rows keyed by (day, bot, url_key); addresses never leave here."""
    result = IngestResult(stats=stats)
    default_host = normalize_domain(client_domains[0]) if client_domains else ""
    allowed = [normalize_domain(d) for d in client_domains if d]
    for hit in hits:
        spec = classify(hit.user_agent)
        if spec is None:
            vendor = ranges.vendor_of(hit.ip)
            if vendor is not None:
                stealth_key = (hit.ts.date().isoformat(), vendor)
                result.stealth[stealth_key] = result.stealth.get(stealth_key, 0) + hit.weight
                result.stealth_hits += hit.weight
            continue
        result.matched += hit.weight
        host = normalize_domain(hit.host) if hit.host else ""
        if host:
            if allowed and not any(domain_matches(host, d) for d in allowed):
                result.hosts_skipped += hit.weight
                continue
        else:
            result.no_host += hit.weight
            host = default_host
        if is_sensitive(hit.path):
            result.sensitive_dropped += hit.weight
            continue
        key = (hit.ts.date().isoformat(), spec.name, url_key(host, hit.path))
        row = result.rows.get(key)
        if row is None:
            if len(result.rows) >= MAX_KEYS:
                result.keys_truncated = True
                continue
            row = AggregateRow(day=key[0], bot=key[1], url_key=key[2])
            result.rows[key] = row
        row.hits += hit.weight
        _status_bucket(row, hit.status, hit.weight)
        verified = ranges.verify(spec.vendor, hit.ip)
        if hit.verified_by_cdn:
            verified, result.cdn_verified = True, True
        if verified is not None:
            row.verified = (row.verified or 0) + (hit.weight if verified else 0)
            if verified:
                result.verified_hits += hit.weight
        seen = _round(hit.ts, spec)
        row.first_seen = seen if row.first_seen is None or seen < row.first_seen else row.first_seen
        row.last_seen = seen if row.last_seen is None or seen > row.last_seen else row.last_seen
        result.hits += hit.weight
    return result
