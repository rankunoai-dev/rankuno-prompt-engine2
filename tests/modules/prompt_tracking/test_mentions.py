"""Tests for brand-mention detection and sentence extraction."""

from __future__ import annotations

from src.modules.prompt_tracking.mentions import CLIENT, detect_mentions, split_sentences
from src.modules.prompt_tracking.schemas import ClientProfile

ANSWER = (
    "For enterprise source-to-pay automation, **GEP SMART** is widely recognized [1]. "
    "Coupa and SAP Ariba are strong alternatives.\n"
    "- Oracle Fusion also competes here.\n"
    "Geppetto is unrelated! Contact gep for a demo?"
)


def _client(**overrides) -> ClientProfile:
    base = {
        "brand_name": "GEP",
        "aliases": ["GEP SMART", "NEXXE"],
        "domains": ["gep.com"],
        "competitor_domains": ["coupa.com", "sap.com", "oracle.com"],
        "lob": "L",
        "seed_keywords": ["k"],
    }
    return ClientProfile(**{**base, **overrides})


def test_split_sentences_strips_markup_and_bullets():
    sentences = split_sentences(ANSWER)
    assert sentences[0].startswith("For enterprise source-to-pay automation, GEP SMART")
    assert "[1]" not in sentences[0] and "**" not in sentences[0]
    assert "Oracle Fusion also competes here." in sentences
    assert not any(s.startswith("- ") for s in sentences)


def test_client_mentions_prefer_the_longest_alias_and_respect_boundaries():
    mentions = detect_mentions(ANSWER, _client())
    client = [m for m in mentions if m.entity == CLIENT]
    assert [m.term for m in client] == ["GEP SMART", "GEP"]
    assert client[0].snippet.startswith("For enterprise source-to-pay automation")
    assert client[1].snippet == "Contact gep for a demo?"
    assert not any("Geppetto" in m.snippet and m.entity == CLIENT for m in mentions)


def test_competitor_mentions_use_names_or_domain_labels():
    by_label = detect_mentions(ANSWER, _client())
    competitors = {m.entity: m.term for m in by_label if m.entity != CLIENT}
    assert competitors == {"coupa.com": "coupa", "sap.com": "sap", "oracle.com": "oracle"}

    named = detect_mentions(ANSWER, _client(competitor_names=["SAP Ariba", "Coupa"]))
    competitors = {m.entity: m.term for m in named if m.entity != CLIENT}
    assert competitors == {"SAP Ariba": "SAP Ariba", "Coupa": "Coupa"}


def test_short_domain_labels_are_not_used_as_terms():
    client = _client(competitor_domains=["hp.com", "coupa.com"])
    assert client.competitor_terms() == {"coupa": "coupa.com"}


def test_empty_text_and_no_match():
    assert detect_mentions("", _client()) == []
    assert detect_mentions("Nothing relevant here.", _client()) == []


def test_snippet_is_capped():
    long = "GEP " + "x" * 1000 + "."
    mentions = detect_mentions(long, _client())
    assert len(mentions[0].snippet) == 600
