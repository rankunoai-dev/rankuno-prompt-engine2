"""One canonical key for a page, used on both sides of the funnel join.

A log line says `/Blog/x/index.html`; a citation says
`https://www.example.com/Blog/x/?utm_source=openai`. Nothing else in the
repository makes those equal, so this module exists. Paths keep their case
(origins are case-sensitive); hosts do not.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final
from urllib.parse import quote, unquote, urlsplit

from src.core.domains import normalize_domain

__all__ = ["is_asset", "is_sensitive", "url_key", "url_key_ci", "url_key_from_url"]

_INDEX_FILES: Final = frozenset({"index.html", "index.htm", "index.php", "default.aspx"})
_ASSET_EXT: Final = frozenset(
    {
        ".css", ".js", ".mjs", ".map", ".json", ".xml", ".rss", ".atom",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif", ".bmp",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp4", ".webm", ".mp3", ".wav", ".zip", ".gz", ".tar",
    }
)  # fmt: skip
_ASSET_NAMES: Final = frozenset({"robots.txt", "favicon.ico", "ads.txt", "manifest.json"})
_SAFE_PATH: Final = "/:@!$&'()*+,;=-._~"
# Hex digests and UUIDs; then opaque base64url-ish blobs, which carry digits and
# mixed case ("eyJhbGciOiJIUzI1NiJ9"). A hyphenated lowercase slug with a year in
# it ("top-10-procurement-trends-2026") matches neither and stays a page.
_HEX: Final = re.compile(r"^[0-9a-fA-F]{20,}$")
_UUID: Final = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_OPAQUE: Final = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Za-z0-9_-]{20,}$")
_MAX_KEY_LEN: Final = 512


def _canonical_path(path: str) -> str:
    raw = path.split("?", 1)[0].split("#", 1)[0] or "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    decoded = unicodedata.normalize("NFC", unquote(raw))
    parts = decoded.split("/")
    if parts and parts[-1].lower() in _INDEX_FILES:
        parts[-1] = ""
    cleaned = "/".join(parts)
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    if len(cleaned) > 1:
        cleaned = cleaned.rstrip("/") or "/"
    return quote(cleaned, safe=_SAFE_PATH)


def url_key(host: str, path: str) -> str:
    """`host/path` with host lowercased and `www.`/port removed, path canonical."""
    clean_host = normalize_domain(host.split(":", 1)[0]) if host else ""
    return f"{clean_host}{_canonical_path(path)}"


def url_key_from_url(url: str) -> str:
    """`url_key` for a full URL (a citation)."""
    parts = urlsplit(url.strip())
    return url_key(parts.hostname or "", parts.path or "/")


def url_key_ci(key: str) -> str:
    """Case-folded key, for spotting near-misses that differ only by case."""
    return key.casefold()


def is_asset(path: str) -> bool:
    """True for files that cannot be a cited page: styles, scripts, images, feeds."""
    name = _canonical_path(path).rsplit("/", 1)[-1].lower()
    if name in _ASSET_NAMES or name.startswith("sitemap"):
        return True
    dot = name.rfind(".")
    return dot > 0 and name[dot:] in _ASSET_EXT


def is_sensitive(path: str) -> bool:
    """True when a path segment looks like a token or an address.

    Live-fetch crawlers follow links humans paste — password resets, unsubscribe
    links, profile pages — and those must never become stored keys.
    """
    if len(path) > _MAX_KEY_LEN or "@" in path:
        return True
    decoded = unquote(path.split("?", 1)[0])
    return any(
        _HEX.match(seg) or _UUID.match(seg) or _OPAQUE.match(seg)
        for seg in decoded.split("/")
        if seg
    )
