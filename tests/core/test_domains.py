"""Tests for domain normalisation and matching."""

from __future__ import annotations

import pytest

from src.core.domains import domain_matches, normalize_domain, registrable_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.GEP.com/solutions/", "gep.com"),
        ("www.gep.com", "gep.com"),
        ("gep.com", "gep.com"),
        ("HTTP://Blog.Gep.Com", "blog.gep.com"),
        ("https://gep.com.", "gep.com"),
        ("", ""),
        ("   ", ""),
        ("https://", ""),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://blog.gep.com/x", "gep.com"),
        ("news.bbc.co.uk", "bbc.co.uk"),
        ("shop.example.com.au", "example.com.au"),
        ("gep.com", "gep.com"),
        ("localhost", "localhost"),
        ("93.184.216.34", "93.184.216.34"),
        ("a.b.c.d.example.org", "example.org"),
    ],
)
def test_registrable_domain(raw, expected):
    assert registrable_domain(raw) == expected


def test_domain_matches_subdomains_but_not_lookalikes():
    assert domain_matches("https://blog.gep.com/post", "gep.com")
    assert domain_matches("www.gep.com", "https://gep.com")
    assert not domain_matches("notgep.com", "gep.com")
    assert not domain_matches("gep.com.evil.example", "gep.com")


def test_domain_matches_handles_blanks():
    assert not domain_matches("", "gep.com")
    assert not domain_matches("gep.com", "")
