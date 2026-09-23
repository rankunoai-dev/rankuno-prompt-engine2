"""One key for one page, whichever side of the join it comes from."""

from __future__ import annotations

import pytest

from src.modules.crawler_logs.normalise import (
    is_asset,
    is_sensitive,
    url_key,
    url_key_ci,
    url_key_from_url,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.gep.com/software/gep-smart?utm_source=openai", "gep.com/software/gep-smart"),
        ("http://GEP.com:443/software/gep-smart/", "gep.com/software/gep-smart"),
        ("https://gep.com/software/gep-smart#faq", "gep.com/software/gep-smart"),
        ("https://gep.com/blog/index.html", "gep.com/blog"),
        ("https://gep.com/", "gep.com/"),
        ("https://gep.com", "gep.com/"),
        ("https://gep.com/caf%C3%A9", "gep.com/caf%C3%A9"),
        ("https://gep.com/café", "gep.com/caf%C3%A9"),
        ("https://gep.com/a//b///c/", "gep.com/a/b/c"),
        ("https://blog.gep.com/Post-One", "blog.gep.com/Post-One"),  # case kept
    ],
)
def test_citation_urls_and_log_paths_meet_on_one_key(url, expected):
    assert url_key_from_url(url) == expected
    host, _, path = expected.partition("/")
    assert url_key(host, "/" + path) == expected  # the log side gives the same key


def test_key_is_idempotent_and_case_folding_is_separate():
    key = url_key_from_url("https://www.gep.com/Blog/X/index.php?x=1")
    host, _, path = key.partition("/")
    assert url_key(host, "/" + path) == key
    assert url_key_ci(key) == "gep.com/blog/x"
    assert url_key_ci("gep.com/blog/x") == url_key_ci("gep.com/Blog/X")


@pytest.mark.parametrize(
    ("path", "asset"),
    [
        ("/assets/app.css", True),
        ("/img/logo.PNG", True),
        ("/robots.txt", True),
        ("/sitemap-posts.xml", True),
        ("/favicon.ico", True),
        ("/whitepapers/procurement-2026.pdf", False),  # citable
        ("/blog/how-to", False),
        ("/", False),
    ],
)
def test_assets_are_never_pages(path, asset):
    assert is_asset(path) is asset


def test_sensitive_paths_are_recognised():
    assert is_sensitive("/reset/8f3c2a9b1d4e5f60718293a4b5c6d7e8")
    assert is_sensitive("/unsubscribe/john.doe@example.com")
    assert is_sensitive("/x" * 300)
    assert is_sensitive("/verify/eyJhbGciOiJIUzI1NiJ9")  # JWT-ish blob
    assert is_sensitive("/orders/8f3c2a9b-1d4e-5f60-7182-93a4b5c6d7e8")  # UUID
    assert not is_sensitive("/blog/how-to-implement-gep")
    assert not is_sensitive("/blog/top-10-procurement-trends-2026")
    assert not is_sensitive("/software/gep-smart")
