"""The streaming parser against every log dialect the design review listed."""

from __future__ import annotations

import gzip
import json
from datetime import UTC

import pytest

from src.modules.crawler_logs.parser import Hit, ParseStats, PayloadTooLarge, iter_hits

UA = "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)"
NGINX = (
    '203.0.113.9 - - [18/Sep/2026:06:59:57 +0000] "GET /software/gep-smart HTTP/1.1" 200 5120 '
    f'"-" "{UA}"'
)


def parse(text: str | bytes, **caps: int) -> tuple[list[Hit], ParseStats]:
    stats = ParseStats()
    data = text.encode() if isinstance(text, str) else text
    chunks = [data[i : i + 7] for i in range(0, len(data), 7)]  # tiny chunks: boundary safety
    limits = {"max_bytes": 10_000_000, "max_json_bytes": 1_000_000, **caps}
    hits = list(iter_hits(chunks, stats, **limits))
    return hits, stats


def test_nginx_combined_basic_line_and_utc_conversion():
    hits, stats = parse(NGINX + "\n")
    assert stats.format == "combined" and stats.parsed == 1 and stats.unparsed == 0
    h = hits[0]
    assert (h.ip, h.path, h.method, h.status, h.host) == (
        "203.0.113.9",
        "/software/gep-smart",
        "GET",
        200,
        None,
    )
    assert h.ts.tzinfo is UTC and h.ts.hour == 6
    assert stats.content_sha256 and stats.day_lines == {"2026-09-18": 1}


def test_timezone_offsets_iso_time_and_month_names_without_locale():
    west = NGINX.replace("+0000", "-0700")
    east = NGINX.replace("18/Sep/2026:06:59:57 +0000", "2026-09-18T23:30:00+05:30")
    hits, _ = parse(west + "\n" + east + "\n")
    assert hits[0].ts.hour == 13  # 06:59 -07:00 is 13:59 UTC
    assert hits[1].ts.hour == 18 and hits[1].ts.day == 18


def test_escaped_quotes_hex_escapes_ipv6_and_trailing_fields():
    apache = NGINX.replace(f'"{UA}"', '"Mozilla \\"quoted\\" OAI-SearchBot/1.0"') + " 1234"  # %D
    nginx_hex = NGINX.replace("/software/gep-smart", "/q\\x22uote").replace(
        "203.0.113.9", "2001:db8::1"
    )
    hits, stats = parse(apache + "\n" + nginx_hex + "\n")
    assert stats.parsed == 2
    assert hits[0].user_agent == 'Mozilla "quoted" OAI-SearchBot/1.0'
    assert hits[1].path == '/q"uote' and hits[1].ip == "2001:db8::1"


def test_request_line_oddities_count_but_do_not_fail():
    dash = NGINX.replace('"GET /software/gep-smart HTTP/1.1"', '"-"')
    http09 = NGINX.replace('"GET /software/gep-smart HTTP/1.1"', '"GET /"')
    star = NGINX.replace('"GET /software/gep-smart HTTP/1.1"', '"OPTIONS * HTTP/1.1"')
    post = NGINX.replace('"GET /software/gep-smart HTTP/1.1"', '"POST /login HTTP/1.1"')
    hits, stats = parse("\n".join([dash, http09, star, post]) + "\n")
    assert stats.parsed == 4 and stats.unparsed == 0
    assert [h.path for h in hits] == ["/"]
    assert stats.no_url == 1 and stats.methods_skipped == 2  # "-" has no method; OPTIONS/POST do


def test_absolute_uri_and_vhost_prefix_supply_the_host():
    absolute = NGINX.replace("GET /software/gep-smart", "GET https://blog.gep.com/post HTTP/1.1")
    vhost = "www.gep.com:443 " + NGINX
    hits, _ = parse(absolute + "\n" + vhost + "\n")
    assert (hits[0].host, hits[0].path) == ("blog.gep.com", "/post")
    assert hits[1].host == "www.gep.com"


def test_x_forwarded_for_leading_and_trailing_take_the_client_address():
    rest = NGINX.split(" - - ", 1)[1]
    two_proxies = "198.51.100.7, 10.0.0.2 10.0.0.1 - - " + rest
    one_proxy = "198.51.100.9, 10.0.0.1 - - " + rest
    trailing = NGINX + ' "198.51.100.8, 10.0.0.3"'
    hits, stats = parse(two_proxies + "\n" + one_proxy + "\n" + trailing + "\n")
    assert [h.ip for h in hits] == ["198.51.100.7", "198.51.100.9", "198.51.100.8"]
    assert stats.xff_seen and stats.parsed == 3


def test_duplicate_lines_bom_and_crlf():
    body = "﻿" + NGINX + "\r\n" + NGINX + "\r\n"
    hits, stats = parse(body)
    assert len(hits) == 1 and stats.duplicate_lines == 1 and stats.lines == 2


def test_gzip_single_and_multi_member():
    single = gzip.compress((NGINX + "\n").encode())
    multi = gzip.compress((NGINX + "\n").encode()) + gzip.compress(
        (NGINX.replace("gep-smart", "other") + "\n").encode()
    )
    assert len(parse(single)[0]) == 1
    hits, _ = parse(multi)
    assert [h.path for h in hits] == ["/software/gep-smart", "/software/other"]


def test_caps_on_decompressed_bytes_and_ratio():
    bomb = gzip.compress(b"x" * 500_000)
    with pytest.raises(PayloadTooLarge):
        parse(bomb, max_bytes=100_000)
    with pytest.raises(PayloadTooLarge):
        parse((NGINX + "\n") * 400, max_bytes=10_000)


def _cf(**over: object) -> str:
    record = {
        "ClientIP": "203.0.113.9",
        "ClientRequestHost": "www.gep.com",
        "ClientRequestPath": "/software/gep-smart",
        "ClientRequestURI": "/software/gep-smart?utm=1",
        "ClientRequestUserAgent": UA,
        "EdgeStartTimestamp": 1789714797000000000,  # ns
        "EdgeResponseStatus": 200,
    }
    record.update(over)
    return json.dumps(record)


def test_cloudflare_ndjson_timestamp_units_and_signals():
    lines = "\n".join(
        [
            _cf(),
            _cf(EdgeStartTimestamp=1789714797),  # seconds
            _cf(EdgeStartTimestamp=1789714797000),  # milliseconds
            _cf(EdgeStartTimestamp="2026-09-18T06:59:57Z"),
            _cf(VerifiedBotCategory="AI Search", SampleInterval=10),
            _cf(ClientRequestPath="", ClientRequestURI="/from-uri?x=1"),
            _cf(EdgeResponseStatus=403),
        ]
    )
    hits, stats = parse(lines + "\n")
    assert stats.format == "cloudflare" and stats.parsed == 7
    assert len({h.ts for h in hits[:4]}) == 1 and hits[0].ts.isoformat().startswith("2026-09-18T06")
    assert hits[4].verified_by_cdn and hits[4].weight == 10 and stats.sampled
    assert hits[5].path == "/from-uri" and hits[0].host == "www.gep.com"
    assert hits[6].status == 403


def test_cloudflare_missing_field_names_it_and_arrays_are_capped():
    with pytest.raises(ValueError, match="EdgeStartTimestamp"):
        parse(json.dumps({"ClientRequestUserAgent": UA}) + "\n")
    array = "[" + _cf() + ",\n" + _cf(EdgeResponseStatus=304) + "]"
    hits, stats = parse(array)
    assert len(hits) == 2 and stats.parsed == 2
    with pytest.raises(PayloadTooLarge):
        parse(array, max_json_bytes=50)


def test_unusable_bodies_are_rejected_without_echoing_lines():
    with pytest.raises(ValueError, match="No recognisable"):
        parse("")
    junk = "\n".join(f"secret-{i} 203.0.113.{i} GPTBot" for i in range(25))
    with pytest.raises(ValueError) as info:
        parse(junk + "\n")
    assert "secret-" not in str(info.value) and "203.0.113" not in str(info.value)
    # a few junk lines among real ones are counted, not fatal
    hits, stats = parse("garbage\n" + NGINX + "\n")
    assert len(hits) == 1 and stats.unparsed == 1
