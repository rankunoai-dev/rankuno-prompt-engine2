"""Tests for the Gemini connector with Google Search grounding."""

from __future__ import annotations

import json

import httpx
import pytest

from src.core.errors import UpstreamClientError
from src.integrations.gemini_search import GEMINI_REDIRECT_HOST, GeminiSearchClient
from src.integrations.schemas import Engine

REDIRECT = f"https://{GEMINI_REDIRECT_HOST}/grounding-api-redirect/"
CHUNK_TITLE_DOMAIN = {"web": {"uri": f"{REDIRECT}AAA", "title": "gep.com"}}
CHUNK_EXPLICIT_DOMAIN = {
    "web": {"uri": f"{REDIRECT}BBB", "title": "Procurement guide", "domain": "www.example.com"}
}
CHUNK_PROSE_TITLE = {"web": {"uri": f"{REDIRECT}CCC", "title": "A guide to buying things"}}
CHUNK_DIRECT = {"web": {"uri": "https://blog.example.org/page", "title": "Direct link"}}


def _payload(*, grounded: bool = True) -> dict:
    candidate: dict = {
        "content": {"role": "model", "parts": [{"text": "Part one."}, {"text": "Part two."}]},
        "finishReason": "STOP",
    }
    if grounded:
        candidate["groundingMetadata"] = {
            "webSearchQueries": ["what is procurement"],
            "groundingChunks": [
                CHUNK_TITLE_DOMAIN,
                CHUNK_EXPLICIT_DOMAIN,
                CHUNK_PROSE_TITLE,
                CHUNK_DIRECT,
            ],
            "groundingSupports": [
                {"segment": {"startIndex": 40, "endIndex": 60}, "groundingChunkIndices": [0]},
                {"segment": {"startIndex": 5, "endIndex": 20}, "groundingChunkIndices": [1, 2]},
                {"segment": {"startIndex": 50}, "groundingChunkIndices": [2, "x"]},
                "not-a-support",
            ],
        }
    return {
        "responseId": "gem_1",
        "modelVersion": "gemini-3.6-flash-001",
        "candidates": [candidate],
    }


class Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def _client(settings, payload: dict, status: int = 200) -> tuple[GeminiSearchClient, Recorder]:
    recorder = Recorder(httpx.Response(status, json=payload))
    return GeminiSearchClient(settings, transport=httpx.MockTransport(recorder)), recorder


def test_request_targets_generate_content_with_header_auth_and_grounding(settings):
    client, recorder = _client(settings, _payload())
    client.ask("what is procurement")

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.url.path == f"/v1beta/models/{settings.gemini_model}:generateContent"
    assert request.headers["x-goog-api-key"] == "test-gemini-key"
    assert "key" not in request.url.params
    assert "test-gemini-key" not in str(request.url)
    body = json.loads(request.content)
    assert body["tools"] == [{"google_search": {}}]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "what is procurement"}]}]


def test_answer_is_normalised_from_the_payload(settings):
    client, _ = _client(settings, _payload())
    answer = client.ask("what is procurement")

    assert answer.engine is Engine.GEMINI
    assert answer.model == "gemini-3.6-flash-001"
    assert answer.response_id == "gem_1"
    assert answer.answer_text == "Part one.\nPart two."
    assert answer.web_triggered is True
    assert answer.consulted_urls == []


def test_citations_follow_first_supporting_segment_then_index(settings):
    client, _ = _client(settings, _payload())
    citations = client.ask("q").citations

    # idx1 and idx2 are first used at offset 5, idx0 at 40; idx3 is never supported.
    assert [c.url for c in citations] == [
        CHUNK_EXPLICIT_DOMAIN["web"]["uri"],
        CHUNK_PROSE_TITLE["web"]["uri"],
        CHUNK_TITLE_DOMAIN["web"]["uri"],
        CHUNK_DIRECT["web"]["uri"],
    ]
    assert [c.position for c in citations] == [1, 2, 3, 4]


def test_explicit_domain_wins_over_redirect_host(settings):
    client, _ = _client(settings, _payload())
    citation = client.ask("q").citations[0]
    assert citation.domain == "example.com"
    assert citation.title == "Procurement guide"
    assert citation.resolved is True


def test_domain_like_title_is_used_but_flagged_unresolved(settings):
    client, _ = _client(settings, _payload())
    citation = client.ask("q").citations[2]
    assert citation.domain == "gep.com"
    assert citation.title == "gep.com"
    assert citation.resolved is False


def test_prose_title_keeps_redirect_host_and_is_unresolved(settings):
    client, _ = _client(settings, _payload())
    citation = client.ask("q").citations[1]
    assert citation.domain == GEMINI_REDIRECT_HOST
    assert citation.title == "A guide to buying things"
    assert citation.resolved is False


def test_non_redirect_uri_is_attributed_to_its_registrable_domain(settings):
    client, _ = _client(settings, _payload())
    citation = client.ask("q").citations[3]
    assert citation.domain == "example.org"
    assert citation.resolved is True


def test_without_grounding_metadata_nothing_is_triggered(settings):
    client, _ = _client(settings, _payload(grounded=False))
    answer = client.ask("q")
    assert answer.web_triggered is False
    assert answer.citations == []
    assert answer.answer_text == "Part one.\nPart two."


def test_malformed_chunks_are_skipped(settings):
    payload = _payload()
    payload["candidates"][0]["groundingMetadata"]["groundingChunks"] = [
        {"retrievedContext": {"uri": "gs://bucket/doc"}},
        "junk",
        {"web": {"uri": ""}},
        {"web": {"uri": "https://", "title": "Broken chunk"}},
        CHUNK_DIRECT,
        CHUNK_DIRECT,
    ]
    payload["candidates"][0]["groundingMetadata"]["groundingSupports"] = []
    client, _ = _client(settings, payload)
    citations = client.ask("q").citations
    assert [c.url for c in citations] == [CHUNK_DIRECT["web"]["uri"]]


def test_missing_candidates_yield_an_empty_answer(settings):
    client, _ = _client(settings, {"promptFeedback": {"blockReason": "SAFETY"}})
    answer = client.ask("q")
    assert answer.answer_text == ""
    assert answer.web_triggered is False
    assert answer.model == settings.gemini_model
    assert answer.response_id is None


def test_session_is_reused_across_calls(settings):
    client, recorder = _client(settings, _payload())
    client.ask("a")
    client.ask("b")
    assert len(recorder.requests) == 2
    assert all(r.headers["x-goog-api-key"] == "test-gemini-key" for r in recorder.requests)


def test_forbidden_is_a_client_error(settings):
    client, _ = _client(settings, {"error": {"code": 403, "message": "denied"}}, status=403)
    with pytest.raises(UpstreamClientError) as info:
        client.ask("q")
    assert info.value.status_code == 403
    assert info.value.service == "gemini"


def test_search_queries_and_grounding_claims_are_captured(settings):
    payload = _payload()
    grounding = payload["candidates"][0]["groundingMetadata"]
    grounding["webSearchQueries"] = ["gep smart review", " gep erp "]
    grounding["groundingSupports"] = [
        {
            "segment": {"startIndex": 0, "endIndex": 20, "text": "GEP SMART is a suite."},
            "groundingChunkIndices": [0, 99],
        },
        {"segment": {"startIndex": 5}, "groundingChunkIndices": [0]},
    ]
    client, _ = _client(settings, payload)
    answer = client.ask("q")
    assert answer.search_queries == ["gep smart review", "gep erp"]
    assert len(answer.citation_claims) == 2
    assert answer.citation_claims[0].sentence == "GEP SMART is a suite."
    assert answer.citation_claims[0].url == answer.citations[0].url
