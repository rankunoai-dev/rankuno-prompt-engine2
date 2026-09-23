"""Tests for the OpenAI Responses API connector ("ChatGPT Search")."""

from __future__ import annotations

import json

import httpx
import pytest

from src.core.errors import UpstreamClientError
from src.core.locale import Locale
from src.integrations.openai_search import OpenAISearchClient
from src.integrations.schemas import Engine

GEP = "https://www.gep.com/blog/procurement"
WIKI = "https://en.wikipedia.org/wiki/Procurement"
UNCITED = "https://example.org/never-cited"


def _payload(*, with_search: bool = True, model: str | None = "gpt-4o-mini-2024-07-18") -> dict:
    output = []
    if with_search:
        output.append(
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "procurement",
                    "sources": [
                        {"type": "url", "url": GEP},
                        {"type": "url", "url": UNCITED},
                        {"type": "url", "url": GEP},
                        {"type": "url"},
                    ],
                },
            }
        )
    output.append(
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Procurement is sourcing goods. It is strategic.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "start_index": 30,
                            "end_index": 46,
                            "url": WIKI,
                            "title": "Procurement - Wikipedia",
                        },
                        {
                            "type": "url_citation",
                            "start_index": 0,
                            "end_index": 29,
                            "url": GEP,
                            "title": "What is procurement? | GEP",
                        },
                        {
                            "type": "url_citation",
                            "start_index": 40,
                            "end_index": 46,
                            "url": GEP,
                            "title": "duplicate",
                        },
                        {"type": "file_citation", "file_id": "f1"},
                    ],
                },
                {"type": "refusal", "refusal": "n/a"},
                {"type": "output_text", "text": "Second paragraph.", "annotations": []},
            ],
        }
    )
    payload: dict = {"id": "resp_123", "output": output}
    if model:
        payload["model"] = model
    return payload


class Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def _client(
    settings, response: httpx.Response, locale: Locale | None = None
) -> tuple[OpenAISearchClient, Recorder]:
    recorder = Recorder(response)
    client = OpenAISearchClient(settings, transport=httpx.MockTransport(recorder), locale=locale)
    return client, recorder


def test_request_targets_responses_api_with_web_search_tool(settings):
    client, recorder = _client(settings, httpx.Response(200, json=_payload()))
    client.ask("what is procurement")

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.host == "api.openai.com"
    assert request.url.path == "/v1/responses"
    assert request.headers["authorization"] == "Bearer test-openai-key"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body["model"] == settings.openai_search_model
    assert body["input"] == "what is procurement"
    assert body["tools"] == [
        {"type": "web_search", "user_location": {"type": "approximate", "country": "US"}}
    ]
    assert body["tool_choice"] == {"type": "web_search"}  # search is forced, never optional
    assert body["include"] == ["web_search_call.action.sources"]


def test_answer_is_normalised_from_the_payload(settings):
    client, _ = _client(settings, httpx.Response(200, json=_payload()))
    answer = client.ask("what is procurement")

    assert answer.engine is Engine.CHATGPT_SEARCH
    assert answer.prompt == "what is procurement"
    assert answer.model == "gpt-4o-mini-2024-07-18"
    assert answer.response_id == "resp_123"
    assert answer.web_triggered is True
    assert (
        answer.answer_text == "Procurement is sourcing goods. It is strategic.\nSecond paragraph."
    )
    assert answer.latency_ms >= 0.0


def test_citations_are_ordered_by_first_appearance_and_deduped(settings):
    client, _ = _client(settings, httpx.Response(200, json=_payload()))
    answer = client.ask("what is procurement")

    assert [c.url for c in answer.citations] == [GEP, WIKI]
    assert [c.position for c in answer.citations] == [1, 2]
    assert [c.domain for c in answer.citations] == ["gep.com", "wikipedia.org"]
    assert answer.citations[0].title == "What is procurement? | GEP"
    assert all(c.resolved for c in answer.citations)
    assert answer.cited_domains == ["gep.com", "wikipedia.org"]


def test_consulted_urls_come_from_search_sources_deduped(settings):
    client, _ = _client(settings, httpx.Response(200, json=_payload()))
    answer = client.ask("what is procurement")
    assert answer.consulted_urls == [GEP, UNCITED]


def test_without_web_search_call_nothing_is_triggered(settings):
    payload = _payload(with_search=False)
    payload["output"][0]["content"][0]["annotations"] = []
    client, _ = _client(settings, httpx.Response(200, json=payload))
    answer = client.ask("what is procurement")

    assert answer.web_triggered is False
    assert answer.citations == []
    assert answer.consulted_urls == []
    assert answer.answer_text.startswith("Procurement is sourcing goods.")


def test_model_falls_back_to_settings_when_payload_omits_it(settings):
    client, _ = _client(settings, httpx.Response(200, json=_payload(model=None)))
    assert client.ask("q").model == settings.openai_search_model


def test_citation_without_a_host_is_skipped(settings):
    payload = _payload()
    annotations = payload["output"][1]["content"][0]["annotations"]
    annotations.insert(
        0, {"type": "url_citation", "start_index": 0, "url": "https://", "title": "broken"}
    )
    annotations.insert(0, {"type": "url_citation", "start_index": 1, "title": "no url"})
    client, _ = _client(settings, httpx.Response(200, json=payload))
    assert [c.url for c in client.ask("q").citations] == [GEP, WIKI]


def test_bare_string_sources_and_missing_output_are_tolerated(settings):
    payload = {
        "id": "resp_2",
        "output": [
            {"type": "web_search_call", "action": {"sources": [GEP, ""]}},
            {"type": "reasoning"},
            "not-an-item",
        ],
    }
    client, _ = _client(settings, httpx.Response(200, json=payload))
    answer = client.ask("q")
    assert answer.web_triggered is True
    assert answer.consulted_urls == [GEP]
    assert answer.answer_text == ""

    client, _ = _client(settings, httpx.Response(200, json={"id": "resp_3"}))
    assert client.ask("q").web_triggered is False


def test_unauthorised_is_a_client_error(settings):
    client, _ = _client(
        settings, httpx.Response(401, json={"error": {"message": "Incorrect API key"}})
    )
    with pytest.raises(UpstreamClientError) as info:
        client.ask("q")
    assert info.value.status_code == 401
    assert info.value.service == "openai"


def test_usage_row_carries_tokens_searches_and_modelled_cost(settings):
    from src.integrations.usage import get_usage_ledger, usage_context

    payload = _payload()
    payload["usage"] = {
        "input_tokens": 1200,
        "output_tokens": 300,
        "input_tokens_details": {"cached_tokens": 200},
    }
    client, _ = _client(settings, httpx.Response(200, json=payload))
    with usage_context(source="test", run_id="run-x", prompt_id="p1", engine="CHATGPT_SEARCH"):
        client.ask("q")
    row = get_usage_ledger(settings).calls(vendor="openai")[-1]
    assert row.status == "ok" and row.operation == "responses.create"
    assert (row.source, row.run_id, row.prompt_id, row.engine) == (
        "test",
        "run-x",
        "p1",
        "CHATGPT_SEARCH",
    )
    assert row.model == "gpt-4o-mini-2024-07-18"
    assert (row.input_tokens, row.output_tokens, row.cached_tokens) == (1200, 300, 200)
    assert row.search_calls == 1
    assert row.estimated_cost_usd == settings.cost_openai_search_call_usd
    assert row.modelled_cost_usd == pytest.approx(
        (1000 * 0.15 + 200 * 0.075 + 300 * 0.60) / 1_000_000 + 0.010, abs=1e-6
    )
    assert row.latency_ms >= 0


def test_failed_call_is_recorded_as_error(settings):
    from src.integrations.usage import get_usage_ledger

    client, _ = _client(settings, httpx.Response(401, json={"error": "nope"}))
    with pytest.raises(UpstreamClientError):
        client.ask("q")
    row = get_usage_ledger(settings).calls(vendor="openai")[-1]
    assert row.status == "error" and "401" in (row.error or "")
    assert row.estimated_cost_usd == settings.cost_openai_search_call_usd


def test_session_is_created_once(settings):
    client, recorder = _client(settings, httpx.Response(200, json=_payload()))
    client.ask("a")
    client.ask("b")
    assert len(recorder.requests) == 2
    assert client._http is not None  # noqa: SLF001


def test_search_queries_and_claims_are_captured(settings):
    payload = _payload()
    payload["output"][0]["action"]["query"] = "gep smart erp integration"
    payload["output"][0]["action"]["queries"] = ["gep smart erp integration", "gep sap adapter"]
    client, _ = _client(settings, httpx.Response(200, json=payload))
    answer = client.ask("q")
    assert answer.search_queries == ["gep smart erp integration", "gep sap adapter"]
    assert answer.citation_claims and answer.citation_claims[0].url in (GEP, WIKI)
    first = answer.citation_claims[0]
    assert first.sentence and first.sentence in answer.answer_text
    assert answer.answer_text[first.start : first.end].strip().startswith(first.sentence[:10])


def test_claims_span_multiple_text_parts(settings):
    payload = _payload(with_search=False)
    payload["output"] = [
        {
            "type": "message",
            "content": [
                {"type": "output_text", "text": "First part. Ends here.", "annotations": []},
                {
                    "type": "output_text",
                    "text": "Second part cites GEP.",
                    "annotations": [
                        {"type": "url_citation", "url": GEP, "start_index": 0, "end_index": 5}
                    ],
                },
            ],
        }
    ]
    answer = _client(settings, httpx.Response(200, json=payload))[0].ask("q")
    assert [c.sentence for c in answer.citation_claims] == ["Second part cites GEP."]
    assert answer.search_queries == []


def test_locale_is_sent_as_the_tool_user_location(settings):
    locale = Locale(
        country="gb", language="en-gb", city="London", region="England", timezone="Europe/London"
    )
    client, recorder = _client(settings, httpx.Response(200, json=_payload()), locale)
    client.ask("what is procurement")

    tool = json.loads(recorder.requests[0].content)["tools"][0]
    assert tool == {
        "type": "web_search",
        "user_location": {
            "type": "approximate",
            "country": "GB",  # upper-cased for the vendor
            "city": "London",
            "region": "England",
            "timezone": "Europe/London",
        },
    }
