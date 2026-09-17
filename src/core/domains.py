"""Domain normalisation for citation matching.

Design stance: "is the client cited?" must be answered the same way for every
engine, so the normalisation lives once, here, and every connector and module
uses it. Engines return URLs in wildly different shapes (bare hosts, tracking
redirects, `www.` prefixes, deep paths); comparing raw strings — as the
prototype did with `target_domain in citation_url` — produces false positives
(`gep.com` matches `notgep.com`) and false negatives (`www.gep.com` vs `gep.com`).

A dependency on a public-suffix library was considered and rejected for now:
the handful of multi-label suffixes B2B clients actually use is small enough to
list, and the list is the one thing a reviewer needs to see.
"""

from __future__ import annotations

from urllib.parse import urlsplit

__all__ = ["domain_matches", "normalize_domain", "registrable_domain"]

# Second-level public suffixes under which the registrable domain has three
# labels (e.g. `example.co.uk`). Extend deliberately; each entry is a decision.
_MULTI_LABEL_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.in",
        "net.in",
        "org.in",
        "co.jp",
        "ne.jp",
        "or.jp",
        "com.br",
        "com.mx",
        "com.ar",
        "com.sg",
        "com.hk",
        "com.tw",
        "com.cn",
        "co.nz",
        "co.za",
        "co.kr",
        "com.tr",
        "com.sa",
        "com.eg",
        "com.ng",
    }
)


def normalize_domain(url_or_host: str) -> str:
    """Return the lower-cased hostname of a URL or bare host, without `www.`.

    Args:
        url_or_host: `https://www.GEP.com/x`, `www.gep.com` or `gep.com`.

    Returns:
        `gep.com` for each of the examples above. Empty string if no host could
        be found, so callers can filter rather than branch on exceptions.
    """
    candidate = url_or_host.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"//{candidate}"
    host = (urlsplit(candidate).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def registrable_domain(url_or_host: str) -> str:
    """Collapse a host to its registrable domain (`blog.gep.com` -> `gep.com`).

    Literal IP addresses and single-label hosts are returned unchanged.
    """
    host = normalize_domain(url_or_host)
    labels = host.split(".")
    if len(labels) <= 2 or all(label.isdigit() for label in labels):
        return host
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def domain_matches(candidate: str, target: str) -> bool:
    """True if `candidate` is `target` or one of its subdomains.

    Both inputs may be URLs or bare hosts. Matching is on label boundaries, so
    `notgep.com` never matches `gep.com`.
    """
    cand = normalize_domain(candidate)
    tgt = normalize_domain(target)
    if not cand or not tgt:
        return False
    return cand == tgt or cand.endswith(f".{tgt}")
