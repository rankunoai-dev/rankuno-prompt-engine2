"""Sentence extraction for claim attribution."""

from __future__ import annotations

from src.integrations.claims import claim_sentence, sentence_at, sentences_with_marker

TEXT = (
    "GEP SMART is a source-to-pay suite. Coupa leads mid-market P2P [2]. "
    "Ariba is common in SAP shops [1][2].\n- Step one: map data [3].\n## Heading\n"
    "Final words"
)


def test_sentence_at_returns_the_containing_sentence_and_span():
    sentence, start, end = sentence_at(TEXT, TEXT.index("Coupa") + 3)
    assert sentence == "Coupa leads mid-market P2P [2]."
    assert TEXT[start:end].strip() == "Coupa leads mid-market P2P [2]."
    first, s0, e0 = sentence_at(TEXT, 0)
    assert first == "GEP SMART is a source-to-pay suite." and s0 == 0
    last, _, e_last = sentence_at(TEXT, len(TEXT) - 1)
    assert last == "Final words" and e_last == len(TEXT)


def test_sentence_at_strips_bullets_and_headings_and_handles_bounds():
    bullet, _, _ = sentence_at(TEXT, TEXT.index("Step one"))
    assert bullet == "Step one: map data [3]."
    heading, _, _ = sentence_at(TEXT, TEXT.index("Heading"))
    assert heading == "Heading"
    assert sentence_at("", 5) == ("", 0, 0)
    assert sentence_at("abc", 99)[0] == "abc"
    assert sentence_at("abc", -4)[0] == "abc"


def test_sentences_with_marker_finds_each_sentence_once():
    two = sentences_with_marker(TEXT, 2)
    assert [s for s, _, _ in two] == [
        "Coupa leads mid-market P2P [2].",
        "Ariba is common in SAP shops [1][2].",
    ]
    assert [s for s, _, _ in sentences_with_marker(TEXT, 3)] == ["Step one: map data [3]."]
    assert sentences_with_marker(TEXT, 9) == []
    assert sentences_with_marker("x [12] y [1]", 1) == [("x [12] y [1]", 0, 12)]


def test_claim_sentence_resolves_trailing_markdown_links_to_the_sentence_before():
    text = (
        "GEP integrates with SAP via BAPIs. ([gep.com](https://www.gep.com/x?utm_source=openai)) "
        "Coupa uses flat files ([coupa.com](https://coupa.com/y))."
    )
    link_at = text.index("([gep.com]")
    sentence, start, _ = claim_sentence(text, link_at + 2)
    assert sentence == "GEP integrates with SAP via BAPIs." and start == 0
    inline, _, _ = claim_sentence(text, text.index("Coupa"))
    assert inline == "Coupa uses flat files."
    assert claim_sentence("", 0) == ("", 0, 0)
