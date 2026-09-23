"""Tests for the SerpApi connector: AI Overview parsing and the page_token follow-up."""

from __future__ import annotations

import httpx
import pytest

from src.core.errors import UpstreamClientError
from src.core.locale import Locale
from src.integrations.schemas import Engine, SerpSnapshot
from src.integrations.serp_api import SerpApiClient

GEP = "https://www.gep.com/knowledge/procurement"
WIKI = "https://en.wikipedia.org/wiki/Procurement"

AI_OVERVIEW = {
    "text_blocks": [
        {"type": "paragraph", "snippet": "Procurement is the process of acquiring goods."},
        {
            "type": "list",
            "list": [
                {"title": "Sourcing", "snippet": "Find suppliers.", "reference_indexes": [0]},
                {"snippet": "Negotiate terms."},
                {"title": "no snippet"},
                "junk",
            ],
        },
        {"type": "paragraph"},
        "junk",
    ],
    "references": [
        {"title": "What is procurement?", "link": GEP, "source": "GEP", "index": 0},
        {"title": "duplicate", "link": GEP, "source": "GEP", "index": 1},
        {"title": "Procurement", "link": WIKI, "source": "Wikipedia", "index": 2},
        {"title": "no link", "source": "x", "index": 3},
        "junk",
    ],
}
RELATED_QUESTIONS = [
    {"question": "What is procurement?", "snippet": "..."},
    {"question": "Why does procurement matter?"},
    {"snippet": "no question"},
]
ORGANIC_RESULTS = [
    {"position": 1, "link": GEP},
    {"position": 2, "link": "https://blog.example.org/y"},
    {"position": 3},
]


def _serp(**extra) -> dict:
    return {
        "search_metadata": {"id": "s1", "status": "Success"},
        "related_questions": RELATED_QUESTIONS,
        "organic_results": ORGANIC_RESULTS,
        **extra,
    }


class Router:
    """Serves one canned payload per SerpApi engine and records every request."""

    def __init__(self, by_engine: dict[str, httpx.Response]) -> None:
        self.by_engine = by_engine
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.by_engine[request.url.params["engine"]]


def _client(settings, locale=None, **by_engine) -> tuple[SerpApiClient, Router]:
    router = Router(
        {
            k: v if isinstance(v, httpx.Response) else httpx.Response(200, json=v)
            for k, v in by_engine.items()
        }
    )
    client = SerpApiClient(settings, transport=httpx.MockTransport(router), locale=locale)
    return client, router


class TestInlineOverview:
    def test_request_carries_locale_and_key(self, settings):
        client, router = _client(settings, google=_serp(ai_overview=AI_OVERVIEW))
        client.search("what is procurement")

        assert len(router.requests) == 1
        request = router.requests[0]
        assert request.url.host == "serpapi.com"
        assert request.url.path == "/search.json"
        params = request.url.params
        assert params["engine"] == "google"
        assert params["q"] == "what is procurement"
        assert params["gl"] == settings.serp_gl
        assert params["hl"] == settings.serp_hl
        assert params["location"] == settings.serp_location
        assert params["api_key"] == "test-serpapi-key"

    def test_snapshot_fields(self, settings):
        client, _ = _client(settings, google=_serp(ai_overview=AI_OVERVIEW))
        snapshot = client.search("what is procurement")

        assert isinstance(snapshot, SerpSnapshot)
        assert snapshot.query == "what is procurement"
        assert snapshot.ai_overview_present is True
        assert snapshot.ai_overview_text == (
            "Procurement is the process of acquiring goods.\nFind suppliers.\nNegotiate terms."
        )
        assert [c.url for c in snapshot.ai_overview_references] == [GEP, WIKI]
        assert [c.domain for c in snapshot.ai_overview_references] == ["gep.com", "wikipedia.org"]
        assert [c.position for c in snapshot.ai_overview_references] == [1, 2]
        assert snapshot.ai_overview_references[0].title == "What is procurement?"
        assert snapshot.paa_questions == ["What is procurement?", "Why does procurement matter?"]
        assert snapshot.organic_domains == ["gep.com", "example.org"]

    def test_ask_maps_the_overview_to_an_engine_answer(self, settings):
        client, _ = _client(settings, google=_serp(ai_overview=AI_OVERVIEW))
        answer = client.ask("what is procurement")

        assert answer.engine is Engine.GOOGLE_AI_OVERVIEW
        assert answer.model == "google_ai_overview"
        assert answer.prompt == "what is procurement"
        assert answer.web_triggered is True
        assert answer.answer_text.startswith("Procurement is the process")
        assert [c.url for c in answer.citations] == [GEP, WIKI]
        assert answer.cited_domains == ["gep.com", "wikipedia.org"]
        assert answer.latency_ms >= 0.0


class TestDeferredOverview:
    def test_page_token_triggers_a_second_request_that_is_merged(self, settings):
        client, router = _client(
            settings,
            google=_serp(ai_overview={"page_token": "tok"}),
            google_ai_overview={"ai_overview": AI_OVERVIEW},
        )
        snapshot = client.search("what is procurement")

        assert len(router.requests) == 2
        second = router.requests[1].url.params
        assert second["engine"] == "google_ai_overview"
        assert second["page_token"] == "tok"
        assert second["api_key"] == "test-serpapi-key"
        assert "q" not in second

        assert snapshot.ai_overview_present is True
        assert [c.url for c in snapshot.ai_overview_references] == [GEP, WIKI]
        assert snapshot.paa_questions == ["What is procurement?", "Why does procurement matter?"]
        assert snapshot.organic_domains == ["gep.com", "example.org"]

    def test_second_request_without_overview_records_absence(self, settings):
        client, router = _client(
            settings,
            google=_serp(ai_overview={"page_token": "tok"}),
            google_ai_overview={"search_metadata": {"status": "Success"}},
        )
        snapshot = client.search("q")
        assert len(router.requests) == 2
        assert snapshot.ai_overview_present is False
        assert snapshot.ai_overview_references == []

    def test_token_alongside_inline_blocks_does_not_refetch(self, settings):
        client, router = _client(
            settings, google=_serp(ai_overview={"page_token": "tok", **AI_OVERVIEW})
        )
        snapshot = client.search("q")
        assert len(router.requests) == 1
        assert snapshot.ai_overview_present is True


class TestNoOverview:
    def test_absent_overview_is_a_recorded_outcome(self, settings):
        client, _ = _client(settings, google=_serp())
        snapshot = client.search("q")
        assert snapshot.ai_overview_present is False
        assert snapshot.ai_overview_text == ""
        assert snapshot.ai_overview_references == []
        assert snapshot.paa_questions == ["What is procurement?", "Why does procurement matter?"]

    def test_inline_snippet_links_become_references_in_reading_order(self, settings):
        overview = {
            "text_blocks": [
                {
                    "type": "paragraph",
                    "snippet": "GEP software is a procurement platform.",
                    "snippet_links": [{"text": "GEP software", "link": GEP}],
                },
                {"type": "heading", "snippet": "Steps"},
                {
                    "type": "list",
                    "list": [
                        {"snippet": "Map data.", "snippet_links": [{"text": "Wiki", "link": WIKI}]},
                        {"snippet": "Repeat.", "snippet_links": [{"link": GEP}]},
                        {"snippet": "Bad.", "snippet_links": [{"text": "x", "link": ""}, "junk"]},
                    ],
                },
            ]
        }
        client, _ = _client(settings, google=_serp(ai_overview=overview))
        snapshot = client.search("q")
        assert snapshot.ai_overview_present is True
        assert [c.url for c in snapshot.ai_overview_references] == [GEP, WIKI]
        assert snapshot.ai_overview_references[0].title == "GEP software"
        assert snapshot.ai_overview_references[1].position == 2
        assert "Map data." in snapshot.ai_overview_text

    def test_explicit_references_come_before_inline_links(self, settings):
        overview = {
            "text_blocks": [
                {"type": "paragraph", "snippet": "x", "snippet_links": [{"link": GEP}]}
            ],
            "references": [{"title": "W", "link": WIKI, "source": "Wikipedia", "index": 0}],
        }
        client, _ = _client(settings, google=_serp(ai_overview=overview))
        assert [c.url for c in client.search("q").ai_overview_references] == [WIKI, GEP]

    def test_empty_overview_object_is_not_present(self, settings):
        client, _ = _client(settings, google=_serp(ai_overview={"text_blocks": []}))
        assert client.search("q").ai_overview_present is False

    def test_ask_reports_no_trigger(self, settings):
        client, _ = _client(settings, google=_serp())
        answer = client.ask("q")
        assert answer.engine is Engine.GOOGLE_AI_OVERVIEW
        assert answer.web_triggered is False
        assert answer.citations == []
        assert answer.answer_text == ""

    def test_each_request_is_one_recorded_plan_search(self, settings):
        from src.integrations.usage import get_usage_ledger

        client, _ = _client(
            settings,
            google=_serp(ai_overview={"page_token": "tok"}),
            google_ai_overview={"ai_overview": AI_OVERVIEW},
        )
        client.search("q")
        rows = get_usage_ledger(settings).calls(vendor="serpapi")
        assert [r.operation for r in rows[-2:]] == ["google_search", "google_ai_overview"]
        assert all(r.search_calls == 1 and r.model == "google" for r in rows[-2:])
        assert all(r.modelled_cost_usd == settings.cost_serpapi_call_usd for r in rows[-2:])

    def test_key_is_loaded_once_across_searches(self, settings):
        client, router = _client(settings, google=_serp())
        client.search("a")
        client.search("b")
        assert [r.url.params["q"] for r in router.requests] == ["a", "b"]
        assert all(r.url.params["api_key"] == "test-serpapi-key" for r in router.requests)

    def test_missing_serp_sections_are_tolerated(self, settings):
        client, _ = _client(settings, google={"search_metadata": {"status": "Success"}})
        snapshot = client.search("q")
        assert snapshot.paa_questions == []
        assert snapshot.organic_domains == []


class TestErrors:
    def test_vendor_error_string_does_not_break_parsing(self, settings):
        client, _ = _client(settings, google=_serp(error="Google hasn't returned any results."))
        assert client.search("q").ai_overview_present is False
        client, _ = _client(settings, google=_serp(error="Your account has run out of searches."))
        assert client.search("q").ai_overview_present is False

    def test_invalid_key_is_a_client_error(self, settings):
        client, _ = _client(settings, google=httpx.Response(401, json={"error": "Invalid API key"}))
        with pytest.raises(UpstreamClientError) as info:
            client.search("q")
        assert info.value.status_code == 401
        assert info.value.service == "serpapi"


class TestCaptureExtras:
    def test_aio_claims_related_searches_and_organic_snippets(self, settings):
        overview = {
            "text_blocks": [
                {
                    "type": "paragraph",
                    "snippet": "GEP software is a procurement platform.",
                    "snippet_links": [{"text": "GEP", "link": GEP}],
                },
                {
                    "type": "list",
                    "list": [
                        {
                            "snippet": "Coupa is common in mid-market.",
                            "snippet_links": [{"link": WIKI}, {"link": GEP}],
                        }
                    ],
                },
            ]
        }
        serp = _serp(ai_overview=overview)
        serp["related_searches"] = [
            {"query": "gep vs coupa"},
            {"nope": 1},
            {"query": "gep pricing"},
        ]
        serp["organic_results"][0]["snippet"] = "Organic snippet text."
        client, _ = _client(settings, google=serp)
        snapshot = client.search("q")
        assert [(c.url, c.sentence) for c in snapshot.ai_overview_claims] == [
            (GEP, "GEP software is a procurement platform."),
            (WIKI, "Coupa is common in mid-market."),
            (GEP, "Coupa is common in mid-market."),
        ]
        assert snapshot.related_searches == ["gep vs coupa", "gep pricing"]
        assert snapshot.organic_results[0].snippet == "Organic snippet text."
        answer = client.ask("q")
        assert answer.citation_claims == snapshot.ai_overview_claims


def test_project_locale_overrides_the_settings_locale(settings):
    """A city-level locale reaches SerpApi as gl / hl / location."""
    locale = Locale(
        country="in", language="hi", city="Mumbai", serp_location="Mumbai, Maharashtra, India"
    )
    client, router = _client(settings, locale=locale, google=_serp(ai_overview=AI_OVERVIEW))
    client.search("procurement software")

    params = router.requests[0].url.params
    assert params["gl"] == "in"  # lower-cased for SerpApi
    assert params["hl"] == "hi"
    assert params["location"] == "Mumbai, Maharashtra, India"
    # Device stays a tracker-wide setting so history stays comparable.
    assert params["device"] == settings.serp_device


def test_locale_without_a_serp_location_falls_back_to_the_setting(settings):
    client, router = _client(settings, locale=Locale(country="fr", language="fr"), google=_serp())
    client.search("logiciel achats")

    params = router.requests[0].url.params
    assert (params["gl"], params["hl"]) == ("fr", "fr")
    assert params["location"] == settings.serp_location
