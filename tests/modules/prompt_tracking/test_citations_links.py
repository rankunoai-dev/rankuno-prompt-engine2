"""Tests for citation links, consulted URLs and mentions folded into a snapshot."""

from __future__ import annotations

from src.integrations.schemas import Citation, Engine, EngineAnswer
from src.modules.prompt_tracking.citations import build_snapshot, merge_links
from src.modules.prompt_tracking.schemas import ClientProfile

GEP = "https://www.gep.com/software/procurement-software"
COUPA = "https://www.coupa.com/products/procurement"
GARTNER = "https://www.gartner.com/reviews/market/procurement-software"


def _answer(citations: list[tuple[int, str]], text: str, consulted: list[str] | None = None):
    return EngineAnswer(
        engine=Engine.CHATGPT_SEARCH,
        model="gpt",
        prompt="p",
        answer_text=text,
        web_triggered=True,
        citations=[
            Citation(url=url, domain=url.split("/")[2].removeprefix("www."), position=pos)
            for pos, url in citations
        ],
        consulted_urls=consulted or [],
    )


def _client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART"],
        domains=["gep.com"],
        competitor_domains=["coupa.com"],
        lob="L",
        seed_keywords=["procurement software"],
    )


def test_merge_links_keeps_best_position_per_url():
    a = _answer([(1, COUPA), (2, GEP)], "")
    b = _answer([(1, GEP), (3, GARTNER)], "")
    merged = merge_links([a, b])
    assert [(c.position, c.url) for c in merged] == [(1, COUPA), (1, GEP), (3, GARTNER)]


def test_snapshot_carries_links_client_urls_consulted_and_mentions():
    a = _answer(
        [(1, COUPA), (2, GEP)],
        "Coupa leads the market. GEP SMART offers strong source-to-pay automation.",
        consulted=[GARTNER, GEP],
    )
    b = _answer(
        [(1, GEP)],
        "GEP SMART offers strong source-to-pay automation. Coupa is cheaper.",
        consulted=[GARTNER],
    )
    snap = build_snapshot(Engine.CHATGPT_SEARCH, [a, b], _client())

    assert [c.url for c in snap.citation_links] == [COUPA, GEP]
    assert snap.client_urls == [GEP]
    assert snap.consulted_urls == [GARTNER, GEP]
    assert snap.mention_detected is True
    assert snap.mention_rate == 1.0
    assert [m.snippet for m in snap.mention_snippets] == [
        "GEP SMART offers strong source-to-pay automation."
    ]  # identical sentences de-duplicated
    assert snap.mention_snippets[0].term == "GEP SMART"
    assert snap.competitor_mentions == {"coupa.com": 2}
    assert snap.competitor_citations == {"coupa.com": 1}


def test_mention_without_citation_and_citation_without_mention():
    mentioned_only = _answer([(1, COUPA)], "GEP is often recommended.")
    cited_only = _answer([(1, GEP)], "The leading platform links here.")
    snap = build_snapshot(Engine.CHATGPT_SEARCH, [mentioned_only, cited_only], _client())
    assert snap.client_cited_samples == 1
    assert snap.mention_rate == 0.5
    assert snap.client_urls == [GEP]


def test_all_failed_samples_have_empty_link_and_mention_fields():
    snap = build_snapshot(Engine.GEMINI, [], _client(), failed_samples=2)
    assert snap.citation_links == []
    assert snap.mention_detected is False
    assert snap.mention_rate == 0.0
    assert snap.competitor_mentions == {}
