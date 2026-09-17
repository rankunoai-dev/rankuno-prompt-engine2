"""Tests for robots.txt evaluation."""

from __future__ import annotations

from src.core.robots import DEFAULT_USER_AGENT, RobotsRules

RULES = """
User-agent: *
Disallow: /private/
Crawl-delay: 5

User-agent: RankUnoPromptTracker
Disallow: /tracker-blocked/
"""


def test_specific_group_wins_over_wildcard():
    rules = RobotsRules("example.com", RULES)
    assert rules.can_fetch("/tracker-blocked/x") is False
    # The wildcard's disallow does not apply once a specific group matches.
    assert rules.can_fetch("/private/x") is True
    assert rules.can_fetch("/public/") is True


def test_absolute_urls_are_matched_on_the_same_host_only():
    rules = RobotsRules("example.com", RULES)
    assert rules.can_fetch("https://example.com/public/page?x=1") is True
    assert rules.can_fetch("https://example.com/tracker-blocked/") is False
    assert rules.can_fetch("https://other.example/public/") is False


def test_unavailable_robots_fails_closed():
    rules = RobotsRules("example.com", None)
    assert rules.can_fetch("/") is False
    assert rules.crawl_delay_s() is None


def test_missing_robots_is_permissive():
    rules = RobotsRules.permissive("example.com")
    assert rules.can_fetch("/anything") is True


def test_crawl_delay_builds_single_slot_bucket():
    rules = RobotsRules("example.com", "User-agent: *\nCrawl-delay: 10\n")
    assert rules.crawl_delay_s() == 10.0
    bucket = rules.bucket()
    assert bucket.capacity == 1
    assert bucket.refill_per_second == 0.1


def test_default_bucket_when_no_crawl_delay():
    rules = RobotsRules("example.com", "User-agent: *\nAllow: /\n")
    assert rules.crawl_delay_s() is None
    bucket = rules.bucket(default_requests_per_minute=12)
    assert bucket.capacity == 12


def test_user_agent_default_is_identifiable():
    assert "RankUno" in DEFAULT_USER_AGENT
    assert RobotsRules("x", "").user_agent == DEFAULT_USER_AGENT
