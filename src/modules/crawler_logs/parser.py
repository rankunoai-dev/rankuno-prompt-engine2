"""Streaming parser for access logs: Nginx/Apache combined and Cloudflare Logpush.

Bytes go in as chunks; `Hit`s come out one line at a time. Peak memory is one
chunk plus the duplicate-line hash set, whatever the body size. Gzip is
detected by magic bytes and handled member by member. No line content is ever
placed in an exception message or a log record: those carry counts only.
"""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Final
from urllib.parse import urlsplit

__all__ = ["Hit", "ParseStats", "PayloadTooLarge", "iter_hits"]

_GZIP_MAGIC: Final = b"\x1f\x8b"
_MONTHS: Final = {
    m: i + 1
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    )
}
_COMBINED: Final = re.compile(
    r"^(?:(?P<vhost>[A-Za-z0-9.\-]+(?::\d+)?) )?"
    r"(?P<xff>(?:[0-9A-Fa-f.:]+, )+(?:[0-9A-Fa-f.:]+ )?)?"
    r"(?P<remote>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "
    r'"(?P<request>(?:[^"\\]|\\.)*)" (?P<status>\d{3}|-) \S+'
    r'(?: "(?P<referer>(?:[^"\\]|\\.)*)" "(?P<ua>(?:[^"\\]|\\.)*)")?'
    r"(?P<rest>.*)$"
)
_QUOTED_IPS: Final = re.compile(r'"((?:[0-9A-Fa-f.:]+)(?:, ?[0-9A-Fa-f.:]+)*)"')
_CLF_TIME: Final = re.compile(
    r"^(\d{1,2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$"
)
_NGINX_HEX: Final = re.compile(r"\\x([0-9A-Fa-f]{2})")
_MAX_LINE: Final = 16_384
_REJECT_MIN_LINES: Final = 20


class PayloadTooLarge(RuntimeError):
    """The decompressed body exceeded the cap, or the compression ratio is absurd."""


@dataclass
class Hit:
    """One request line the funnel cares about."""

    ts: datetime
    host: str | None
    path: str
    method: str
    user_agent: str
    ip: str | None
    status: int
    verified_by_cdn: bool = False
    weight: int = 1


@dataclass
class ParseStats:
    """Counters an import reports; nothing here identifies a line."""

    format: str = "combined"
    lines: int = 0
    parsed: int = 0
    unparsed: int = 0
    duplicate_lines: int = 0
    methods_skipped: int = 0
    no_url: int = 0
    xff_seen: bool = False
    sampled: bool = False
    content_sha256: str = ""
    span_from: datetime | None = None
    span_to: datetime | None = None
    day_lines: dict[str, int] = field(default_factory=dict)

    def note_time(self, ts: datetime) -> None:
        """Track coverage from *every* parsed line, not only crawler lines."""
        self.span_from = ts if self.span_from is None or ts < self.span_from else self.span_from
        self.span_to = ts if self.span_to is None or ts > self.span_to else self.span_to
        key = ts.date().isoformat()
        self.day_lines[key] = self.day_lines.get(key, 0) + 1

    def reject_if_unusable(self) -> None:
        """A file that produced nothing usable is an error, not an empty import."""
        if self.parsed == 0:
            msg = f"No recognisable log lines ({self.lines} line(s) read)."
            raise ValueError(msg)
        if self.lines >= _REJECT_MIN_LINES and self.unparsed * 2 > self.lines:
            msg = f"{self.unparsed} of {self.lines} lines are not a recognised log format."
            raise ValueError(msg)


# -- bytes → lines ------------------------------------------------------------------


def _decompress(chunks: Iterable[bytes], *, max_bytes: int, max_ratio: int) -> Iterator[bytes]:
    """Inflate multi-member gzip while enforcing the size and ratio caps."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    seen_in = 0
    seen_out = 0
    for chunk in chunks:
        seen_in += len(chunk)
        data = chunk
        while data:
            out = inflater.decompress(data)
            seen_out += len(out)
            if seen_out > max_bytes:
                raise PayloadTooLarge(f"Decompressed log exceeds {max_bytes} bytes.")
            if seen_in and seen_out > max_ratio * max(seen_in, 1):
                raise PayloadTooLarge("Compression ratio exceeds the allowed maximum.")
            yield out
            data = inflater.unused_data
            if data:  # another gzip member follows
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            elif not inflater.eof:
                break


def _lines(chunks: Iterable[bytes], stats: ParseStats, *, max_bytes: int) -> Iterator[str]:
    """Decode chunks incrementally, hash the decompressed content, yield lines."""
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    digest = hashlib.sha256()
    buffer = ""
    total = 0
    first = True
    for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLarge(f"Log exceeds {max_bytes} bytes.")
        digest.update(chunk)
        text = decoder.decode(chunk)
        if first:
            text = text.lstrip("﻿")
            first = False
        buffer += text
        while True:
            cut = buffer.find("\n")
            if cut < 0:
                break
            yield buffer[:cut].rstrip("\r")
            buffer = buffer[cut + 1 :]
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        yield buffer.rstrip("\r")
    stats.content_sha256 = digest.hexdigest()


# -- combined ------------------------------------------------------------------------


def _clf_time(raw: str) -> datetime | None:
    m = _CLF_TIME.match(raw.strip())
    if m:
        day, mon, year, hh, mm, ss, sign, oh, om = m.groups()
        month = _MONTHS.get(mon.lower())
        if month is None:
            return None
        offset = timedelta(hours=int(oh), minutes=int(om))
        tz = timezone(offset if sign == "+" else -offset)
        try:
            return datetime(int(year), month, int(day), int(hh), int(mm), int(ss), tzinfo=tz)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _unescape(value: str) -> str:
    value = value.replace('\\"', '"')
    return _NGINX_HEX.sub(lambda m: chr(int(m.group(1), 16)), value)


def _combined(line: str, stats: ParseStats) -> Hit | None:
    m = _COMBINED.match(line)
    if m is None:
        return None
    ts = _clf_time(m.group("time"))
    if ts is None:
        return None
    request = _unescape(m.group("request"))
    bits = request.split(" ")
    method = bits[0].upper() if bits and bits[0] else ""
    target = bits[1] if len(bits) > 1 else ""
    host: str | None = m.group("vhost").split(":")[0] if m.group("vhost") else None
    if target.lower().startswith(("http://", "https://")):
        parts = urlsplit(target)
        host, target = parts.hostname or host, parts.path or "/"
    ip: str | None = m.group("remote")
    if m.group("xff"):
        ip = m.group("xff").split(",")[0].strip()
        stats.xff_seen = True
    else:
        trailing = _QUOTED_IPS.search(m.group("rest") or "")
        if trailing:
            ip = trailing.group(1).split(",")[0].strip()
            stats.xff_seen = True
    status = int(m.group("status")) if m.group("status") != "-" else 0
    stats.parsed += 1
    stats.note_time(ts)
    if not target or target in {"-", "*"} or method not in {"GET", "HEAD"}:
        if method.isalpha() and method not in {"GET", "HEAD"}:
            stats.methods_skipped += 1
        else:
            stats.no_url += 1
        return None
    return Hit(
        ts=ts.astimezone(UTC),
        host=host,
        path=_unescape(target),
        method=method,
        user_agent=_unescape(m.group("ua") or ""),
        ip=ip,
        status=status,
    )


# -- cloudflare ----------------------------------------------------------------------

_CF_REQUIRED: Final = ("ClientRequestUserAgent", "EdgeStartTimestamp")


def _cf_time(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        n = float(value)
        for threshold, divisor in ((1e17, 1e9), (1e14, 1e6), (1e11, 1e3)):
            if n >= threshold:
                return datetime.fromtimestamp(n / divisor, tz=UTC)
        return datetime.fromtimestamp(n, tz=UTC)
    return None


def _cloudflare(record: dict[str, Any], stats: ParseStats) -> Hit | None:
    missing = [k for k in _CF_REQUIRED if k not in record]
    if missing:
        msg = f"Cloudflare record is missing {', '.join(missing)}."
        raise ValueError(msg)
    ts = _cf_time(record.get("EdgeStartTimestamp"))
    if ts is None:
        return None
    stats.parsed += 1
    stats.note_time(ts)
    interval = record.get("SampleInterval")
    weight = int(interval) if isinstance(interval, int | float) and interval > 1 else 1
    if weight > 1:
        stats.sampled = True
    method = str(record.get("ClientRequestMethod") or "GET").upper()
    if method not in {"GET", "HEAD"}:
        stats.methods_skipped += 1
        return None
    path = str(record.get("ClientRequestPath") or "")
    if not path:
        uri = str(record.get("ClientRequestURI") or "")
        path = uri.split("?", 1)[0]
    if not path:
        stats.no_url += 1
        return None
    status = record.get("EdgeResponseStatus")
    return Hit(
        ts=ts,
        host=str(record["ClientRequestHost"]) if record.get("ClientRequestHost") else None,
        path=path,
        method=method,
        user_agent=str(record.get("ClientRequestUserAgent") or ""),
        ip=str(record["ClientIP"]) if record.get("ClientIP") else None,
        status=int(status) if isinstance(status, int | float) else 0,
        verified_by_cdn=bool(record.get("VerifiedBotCategory")),
        weight=weight,
    )


# -- entry point ----------------------------------------------------------------------


def iter_hits(
    chunks: Iterable[bytes],
    stats: ParseStats,
    *,
    max_bytes: int,
    max_json_bytes: int,
    max_ratio: int = 100,
) -> Iterator[Hit]:
    """Parse a log body chunk by chunk; the format is sniffed from the first line."""
    chunks = iter(chunks)
    head = next(chunks, b"")
    source: Iterable[bytes] = (c for c in (head, *chunks) if c)
    if head[:2] == _GZIP_MAGIC:
        source = _decompress(source, max_bytes=max_bytes, max_ratio=max_ratio)
    seen: set[bytes] = set()
    format_: str | None = None
    array_buffer: list[str] = []
    array_size = 0
    for line in _lines(source, stats, max_bytes=max_bytes):
        if not line.strip():
            continue
        stats.lines += 1
        if len(line) > _MAX_LINE:
            stats.unparsed += 1
            continue
        fingerprint = hashlib.sha1(line.encode("utf-8", "replace")).digest()  # noqa: S324 - dedupe, not security
        if fingerprint in seen:
            stats.duplicate_lines += 1
            continue
        seen.add(fingerprint)
        if format_ is None:
            first = line.lstrip()
            format_ = (
                "array"
                if first.startswith("[")
                else "cloudflare"
                if first.startswith("{")
                else "combined"
            )
            stats.format = "cloudflare" if format_ != "combined" else "combined"
        if format_ == "array":
            array_size += len(line)
            if array_size > max_json_bytes:
                raise PayloadTooLarge("JSON array logs must be exported as NDJSON above the cap.")
            array_buffer.append(line)
            continue
        hit = _parse_line(line, format_, stats)
        if hit is not None:
            yield hit
    if array_buffer:
        yield from _parse_array("\n".join(array_buffer), stats)
    stats.reject_if_unusable()


def _parse_line(line: str, format_: str, stats: ParseStats) -> Hit | None:
    if format_ == "cloudflare":
        try:
            record = json.loads(line)
        except ValueError:
            stats.unparsed += 1
            return None
        if not isinstance(record, dict):
            stats.unparsed += 1
            return None
        return _cloudflare(record, stats)
    hit = _combined(line, stats)
    if hit is None and stats.parsed == 0 or hit is None and not _COMBINED.match(line):
        stats.unparsed += 1
    return hit


def _parse_array(text: str, stats: ParseStats) -> Iterator[Hit]:
    try:
        records = json.loads(text)
    except ValueError as exc:
        raise ValueError("Body starts with '[' but is not a JSON array of records.") from exc
    if not isinstance(records, list):
        raise ValueError("Body starts with '[' but is not a JSON array of records.")
    stats.lines = len(records)
    for record in records:
        if not isinstance(record, dict):
            stats.unparsed += 1
            continue
        hit = _cloudflare(record, stats)
        if hit is not None:
            yield hit
