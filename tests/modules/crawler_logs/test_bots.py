"""Crawler catalogue: every token classifies, every look-alike does not."""

from __future__ import annotations

import pytest

from src.integrations.schemas import Engine
from src.modules.crawler_logs.bots import CATALOGUE, catalogue_out, classify, spec_for


@pytest.mark.parametrize("spec", CATALOGUE, ids=lambda s: s.name)
def test_every_catalogued_token_classifies_to_itself(spec):
    ua = f"Mozilla/5.0 AppleWebKit/537.36 (compatible; {spec.token}/1.2; +https://example.com/bot)"
    assert classify(ua) is spec
    assert classify(ua.lower()) is spec  # case-insensitive


@pytest.mark.parametrize(
    "ua",
    [
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
        "Amazon CloudFront",
        "AdsBot-Google (+http://www.google.com/adsbot.html)",
        "Google-InspectionTool/1.0",
        "Storebot-Google/1.0",
        "Mozilla/5.0 (compatible; Google-Extended)",  # a robots token, not a UA
        "Mozilla/5.0 (compatible; NotGPTBotter/1.0)",
        "",
    ],
)
def test_look_alikes_and_browsers_never_classify(ua):
    assert classify(ua) is None


def test_specific_tokens_win_over_general_ones():
    assert classify("Mozilla/5.0 (compatible; ChatGPT-User/1.0)").name == "ChatGPT-User"
    assert classify("Mozilla/5.0 (compatible; OAI-SearchBot/1.0)").name == "OAI-SearchBot"
    assert classify("Mozilla/5.0 (compatible; Perplexity-User/1.0)").name == "Perplexity-User"
    assert classify("Mozilla/5.0 (compatible; Googlebot-Image/1.0)").name == "Googlebot"
    assert classify("Mozilla/5.0 (compatible; GoogleOther-Video)").name == "GoogleOther"


def test_engine_mapping_and_verifiability():
    assert spec_for("OAI-SearchBot").engine is Engine.CHATGPT_SEARCH
    assert spec_for("PerplexityBot").engine is Engine.PERPLEXITY
    assert spec_for("GPTBot").engine is None  # training never maps to an audited engine
    assert spec_for("Googlebot").engine is None
    assert spec_for("GPTBot").verifiable and spec_for("Googlebot").verifiable
    assert not spec_for("ClaudeBot").verifiable and not spec_for("CCBot").verifiable
    assert spec_for("nope") is None
    out = catalogue_out()
    assert len(out) == len(CATALOGUE) and {o.purpose for o in out} == {
        "training",
        "index",
        "live_fetch",
    }
