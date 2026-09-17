"""Tests for the Perplexity Agent API connector (`perplexity/sonar` + web_search)."""

from __future__ import annotations

import json

import httpx
import pytest

from src.core.errors import IntegrationError, UpstreamClientError
from src.integrations.perplexity import PERPLEXITY_NATIVE_MODEL, PerplexityClient
from src.integrations.schemas import Engine

A = "https://a.example/one"
B = "https://www.b.example/two"
C = "https://c.example/three"


def _payload(
    *,
    results: list[dict] | None = None,
    text: str = "Answer [2][1]. More [2].",
    with_search: bool = True,
) -> dict:
    output: list[dict] = []
    if with_search:
        output.append(
            {
                "type": "search_results",
                "queries": ["gep erp"],
                "results": results
                if results is not None
                else [
                    {"id": 1, "url": A, "title": "Title A", "snippet": "s", "source": "web"},
                    {"id": 2, "url": B, "title": "Title B"},
                    {"id": 3, "url": C, "title": ""},
                    {"id": 4, "title": "no url"},
                ],
            }
        )
    output.append(
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    )
    return {"id": "resp_1", "model": "perplexity/sonar", "output": output}


class Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def _client(settings, payload: dict, status: int = 200) -> tuple[PerplexityClient, Recorder]:
    recorder = Recorder(httpx.Response(status, json=payload))
    return PerplexityClient(settings, transport=httpx.MockTransport(recorder)), recorder


def test_request_targets_the_agent_api_with_native_model_and_web_search(settings):
    assert settings.perplexity_model == PERPLEXITY_NATIVE_MODEL
    client, recorder = _client(settings, _payload())
    client.ask("what is procurement")

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.host == "api.perplexity.ai"
    assert request.url.path == "/v1/responses"
    assert request.headers["authorization"] == "Bearer test-perplexity-key"
    assert json.loads(request.content) == {
        "model": "perplexity/sonar",
        "input": "what is procurement",  # verbatim: no citation-format instruction appended
        "tools": [{"type": "web_search"}],
        "tool_choice": {"type": "web_search"},  # search is forced, never optional
    }


@pytest.mark.parametrize("legacy", ["sonar", "sonar-pro", " sonar-reasoning-pro "])
def test_retired_sonar_chat_names_map_to_the_native_agent_model(settings, legacy):
    custom = settings.model_copy(update={"perplexity_model": legacy})
    client, recorder = _client(custom, _payload())
    client.ask("q")
    assert json.loads(recorder.requests[0].content)["model"] == "perplexity/sonar"


def test_other_router_models_are_sent_verbatim(settings):
    custom = settings.model_copy(update={"perplexity_model": "openai/gpt-5.4-mini"})
    client, recorder = _client(custom, _payload())
    client.ask("q")
    assert json.loads(recorder.requests[0].content)["model"] == "openai/gpt-5.4-mini"


def test_timeout_is_long_enough_for_the_agent_api(settings):
    client, _ = _client(settings, _payload())
    client.authenticate()
    assert client._http is not None
    assert client._http.timeout.read is not None and client._http.timeout.read >= 120


def test_sources_are_citations_ordered_by_marker_first_appearance(settings):
    client, _ = _client(settings, _payload())
    answer = client.ask("what is procurement")

    assert answer.engine is Engine.PERPLEXITY
    assert answer.response_id == "resp_1"
    assert answer.model == "perplexity/sonar"
    assert answer.answer_text == "Answer [2][1]. More [2]."
    assert answer.web_triggered is True
    assert [c.url for c in answer.citations] == [B, A, C]  # [2] first, then [1], then uncited
    assert [c.position for c in answer.citations] == [1, 2, 3]
    assert [c.domain for c in answer.citations] == ["b.example", "a.example", "c.example"]
    assert answer.citations[0].title == "Title B"
    assert answer.citations[2].title is None
    assert answer.consulted_urls == []


def test_without_markers_api_order_is_kept(settings):
    client, _ = _client(settings, _payload(text="Plain answer."))
    answer = client.ask("q")
    assert [c.url for c in answer.citations] == [A, B, C]


def test_markers_that_match_no_result_fall_back_to_api_order(settings):
    client, _ = _client(settings, _payload(text="See [9] and [42]."))
    answer = client.ask("q")
    assert [c.url for c in answer.citations] == [A, B, C]


def test_duplicate_urls_are_collapsed(settings):
    results = [{"id": 1, "url": A}, {"id": 2, "url": A}, {"id": 3, "url": B}]
    client, _ = _client(settings, _payload(results=results, text="x [3][2]"))
    answer = client.ask("q")
    assert [c.url for c in answer.citations] == [B, A]


def test_no_search_results_item_means_no_web_trigger(settings):
    client, _ = _client(settings, _payload(with_search=False, text="From memory."))
    answer = client.ask("q")
    assert answer.web_triggered is False
    assert answer.citations == []
    assert answer.answer_text == "From memory."


def test_empty_results_list_means_no_web_trigger(settings):
    client, _ = _client(settings, _payload(results=[]))
    assert client.ask("q").web_triggered is False


def test_source_without_a_host_is_skipped(settings):
    results = [{"id": 1, "url": "https://"}, {"id": 2, "url": B}]
    client, _ = _client(settings, _payload(results=results, text="x"))
    assert [c.url for c in client.ask("q").citations] == [B]


def test_legacy_chat_completion_payload_still_parses(settings):
    legacy = {
        "id": "ppl_1",
        "model": "sonar-pro",
        "choices": [{"message": {"content": "ignored: no output items"}}],
        "citations": [B, A],
        "search_results": [{"title": "Title A", "url": A}],
        "output_text": "Answer text",
    }
    client, _ = _client(settings, legacy)
    answer = client.ask("q")
    assert answer.answer_text == "Answer text"
    assert [c.url for c in answer.citations] == [B, A]
    assert answer.citations[1].title == "Title A"


@pytest.mark.parametrize("output", [None, "junk", [None, 3, {"type": "other"}]])
def test_malformed_output_yields_empty_answer(settings, output):
    client, _ = _client(settings, {"id": "r", "model": "perplexity/sonar", "output": output})
    answer = client.ask("q")
    assert answer.answer_text == "" and answer.citations == []


def test_usage_row_carries_vendor_reported_cost(settings):
    from src.integrations.usage import get_usage_ledger

    payload = _payload()
    payload["usage"] = {
        "cost": {"total_cost": 0.0098, "currency": "USD"},
        "input_tokens": 4663,
        "output_tokens": 2452,
        "input_tokens_details": {"cached_tokens": 0},
        "tool_calls_details": {"search_web": {"invocation": 1, "cost_usd": 0.0025}},
    }
    client, _ = _client(settings, payload)
    client.ask("q")
    row = get_usage_ledger(settings).calls(vendor="perplexity")[-1]
    assert row.vendor_cost_usd == 0.0098 and row.modelled_cost_usd is None
    assert (row.input_tokens, row.output_tokens, row.cached_tokens) == (4663, 2452, 0)
    assert row.search_calls == 1 and row.model == "perplexity/sonar"
    assert row.actual_cost_usd == 0.0098
    assert row.estimated_cost_usd == settings.cost_perplexity_call_usd


def test_usage_row_without_usage_block_keeps_estimate(settings):
    from src.integrations.usage import get_usage_ledger

    client, _ = _client(settings, _payload())
    client.ask("q")
    row = get_usage_ledger(settings).calls(vendor="perplexity")[-1]
    assert row.vendor_cost_usd is None and row.actual_cost_usd == settings.cost_perplexity_call_usd


def test_session_is_reused_across_calls(settings):
    client, recorder = _client(settings, _payload())
    client.ask("one")
    client.ask("two")
    assert len(recorder.requests) == 2
    assert client._http is not None


def test_retired_chat_endpoint_style_403_is_a_client_error(settings):
    body = {"error": {"message": "Sonar is now the Agent API.", "code": 403}}
    client, _ = _client(settings, body, status=403)
    with pytest.raises(UpstreamClientError):
        client.ask("q")


def test_rate_limited_is_an_integration_error(settings):
    client, _ = _client(settings, {"error": "slow down"}, status=429)
    with pytest.raises(IntegrationError):
        client.ask("q")


def test_queries_snippets_and_marker_claims_are_captured(settings):
    payload = _payload(text="Coupa leads P2P [2]. GEP is strong in S2P [1]. Unrelated [7].")
    payload["output"][0]["queries"] = ["gep vs coupa", "site:gep.com s2p"]
    payload["output"][0]["results"][0]["snippet"] = "GEP SMART unifies source-to-pay."
    payload["output"][0]["results"][0]["date"] = "2026-03-01"
    payload["output"][0]["results"][1]["last_updated"] = "2025-01-15"
    client, _ = _client(settings, payload)
    answer = client.ask("q")
    assert answer.search_queries == ["gep vs coupa", "site:gep.com s2p"]
    assert [(c.url, c.sentence) for c in answer.citation_claims] == [
        (A, "GEP is strong in S2P [1]."),
        (B, "Coupa leads P2P [2]."),
    ]
    by_url = {s.url: s for s in answer.source_snippets}
    assert (
        by_url[A].snippet == "GEP SMART unifies source-to-pay." and by_url[A].date == "2026-03-01"
    )
    assert by_url[B].date == "2025-01-15" and by_url[B].title == "Title B"
    assert C not in by_url  # no snippet and no date: nothing to keep
