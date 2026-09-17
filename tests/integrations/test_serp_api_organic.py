"""Tests for organic-result parsing and the device parameter on the SerpApi connector."""

from __future__ import annotations

import httpx

from src.integrations.schemas import Engine, OrganicResult
from src.integrations.serp_api import SerpApiClient

ORGANIC = [
    {"position": 1, "link": "https://www.gep.com/software/procurement", "title": "GEP"},
    {"position": 2, "link": "https://blog.coupa.com/x", "title": "Coupa"},
    {"link": "https://no-position.example/page"},  # falls back to list index (3)
    {"position": 4, "title": "no link"},
    {"position": 5, "link": ""},
    "junk",
]


def _client(settings, payload: dict, *, status: int = 200) -> tuple[SerpApiClient, list]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=payload)

    return SerpApiClient(settings, transport=httpx.MockTransport(handler)), requests


def test_organic_results_are_parsed_with_positions_and_domains(settings):
    client, _ = _client(settings, {"organic_results": ORGANIC})
    snapshot = client.search("procurement software")

    assert [type(r) for r in snapshot.organic_results] == [OrganicResult] * 3
    assert [r.position for r in snapshot.organic_results] == [1, 2, 3]
    expected = ["gep.com", "coupa.com", "no-position.example"]
    assert [r.domain for r in snapshot.organic_results] == expected
    assert snapshot.organic_results[0].title == "GEP"
    assert snapshot.organic_results[2].title is None
    assert snapshot.organic_domains == expected
    assert snapshot.ai_overview_present is False


def test_device_is_sent_and_recorded(settings):
    settings = settings.model_copy(update={"serp_device": "mobile"})
    client, requests = _client(settings, {"organic_results": ORGANIC})
    snapshot = client.search("procurement software")

    assert requests[0].url.params["device"] == "mobile"
    assert snapshot.device == "mobile"


def test_search_and_ask_returns_both_views_from_one_request(settings):
    client, requests = _client(settings, {"organic_results": ORGANIC})
    snapshot, answer = client.search_and_ask("procurement software")

    assert len(requests) == 1
    assert snapshot.organic_results[0].domain == "gep.com"
    assert answer.engine is Engine.GOOGLE_AI_OVERVIEW
    assert answer.web_triggered is False
    assert answer.citations == []


def test_missing_or_malformed_organic_block_yields_empty_list(settings):
    client, _ = _client(settings, {"organic_results": "nope"})
    assert client.search("x").organic_results == []
    client, _ = _client(settings, {})
    assert client.search("x").organic_results == []
