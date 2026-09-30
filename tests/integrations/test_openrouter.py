"""OpenRouter connector, routing and key isolation (ADR 0026).

Response fixtures follow the shapes the live probe of 2026-09-29 returned:
Perplexity gives zero-width annotation spans and numbered [n] markers; OpenAI
and Google give real character spans.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from src.core.errors import ConfigurationError, IntegrationError, UpstreamClientError
from src.integrations.anthropic_judge import AnthropicJudgeClient
from src.integrations.openrouter import (
    OpenRouterClient,
    OpenRouterEngineClient,
    engine_route,
    judge_client,
    openrouter_claude_model,
    openrouter_engine_model,
)
from src.integrations.schemas import Engine
from src.integrations.usage import get_usage_ledger

G2 = "https://www.g2.com/categories/supplier-risk"
GEP = "https://www.gep.com/software/supplier-risk"
COUPA = "https://www.coupa.com/products/supplier-risk"


class Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


@pytest.fixture
def or_settings(settings):
    """Only the OpenRouter key: the situation the operator is in on Railway."""
    return settings.model_copy(
        update={
            "openrouter_api_key": SecretStr("sk-or-v1-testkey"),
            "openai_api_key": None,
            "perplexity_api_key": None,
            "gemini_api_key": None,
            "anthropic_api_key": None,
        }
    )


def _completion(
    content: str,
    annotations: list[dict] | None = None,
    *,
    model: str = "perplexity/sonar",
    finish: str = "stop",
    cost: float = 0.00563,
    searches: int | None = None,
) -> dict:
    usage: dict = {"prompt_tokens": 15, "completion_tokens": 617, "cost": cost}
    if searches is not None:
        usage["server_tool_use_details"] = {"web_search_requests": searches}
    message: dict = {"role": "assistant", "content": content}
    if annotations is not None:
        message["annotations"] = annotations
    return {
        "id": "gen-123",
        "model": model,
        "provider": "Perplexity",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": usage,
    }


def _ann(url: str, title: str, start: int = 0, end: int = 0) -> dict:
    return {
        "type": "url_citation",
        "url_citation": {"url": url, "title": title, "start_index": start, "end_index": end},
    }


# -- routing ------------------------------------------------------------------------


def test_auto_route_uses_the_vendor_key_when_set_and_openrouter_otherwise(settings, or_settings):
    for engine in (Engine.CHATGPT_SEARCH, Engine.PERPLEXITY, Engine.GEMINI):
        assert engine_route(settings, engine) == "direct"  # conftest sets vendor keys
        assert engine_route(or_settings, engine) == "openrouter"
    # Google AI Overview is SerpApi, not a language model: always direct.
    assert engine_route(or_settings, Engine.GOOGLE_AI_OVERVIEW) == "direct"


def test_the_route_can_be_forced_either_way(settings, or_settings):
    forced = settings.model_copy(
        update={"llm_route": "openrouter", "openrouter_api_key": SecretStr("sk-or-v1-x")}
    )
    assert engine_route(forced, Engine.GEMINI) == "openrouter"
    assert engine_route(forced, Engine.GOOGLE_AI_OVERVIEW) == "direct"
    direct = or_settings.model_copy(update={"llm_route": "direct"})
    assert engine_route(direct, Engine.PERPLEXITY) == "direct"


def test_no_key_at_all_stays_direct_so_the_failure_names_the_vendor(settings):
    bare = settings.model_copy(update={"openai_api_key": None, "openrouter_api_key": None})
    assert engine_route(bare, Engine.CHATGPT_SEARCH) == "direct"


def test_model_ids_on_the_openrouter_route(settings):
    # gpt-4o-mini has no native search on OpenRouter (probe: HTTP 404).
    assert openrouter_engine_model(settings, Engine.CHATGPT_SEARCH) == "openai/gpt-5-mini"
    s = settings.model_copy(update={"openai_search_model": "gpt-5"})
    assert openrouter_engine_model(s, Engine.CHATGPT_SEARCH) == "openai/gpt-5"
    s = settings.model_copy(update={"openai_search_model": "openai/o4-mini"})
    assert openrouter_engine_model(s, Engine.CHATGPT_SEARCH) == "openai/o4-mini"
    assert openrouter_engine_model(settings, Engine.PERPLEXITY) == "perplexity/sonar"
    s = settings.model_copy(update={"perplexity_model": "sonar-pro"})
    assert openrouter_engine_model(s, Engine.PERPLEXITY) == "perplexity/sonar-pro"
    assert openrouter_engine_model(settings, Engine.GEMINI) == "google/gemini-3.6-flash"
    s = settings.model_copy(update={"gemini_model": "models/gemini-3.7-flash"})
    assert openrouter_engine_model(s, Engine.GEMINI) == "google/gemini-3.7-flash"
    with pytest.raises(ValueError, match="not a language model"):
        openrouter_engine_model(settings, Engine.GOOGLE_AI_OVERVIEW)


def test_claude_ids_map_to_openrouter_names():
    assert openrouter_claude_model("claude-haiku-4-5") == "anthropic/claude-haiku-4.5"
    assert openrouter_claude_model("claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert openrouter_claude_model("anthropic/claude-opus-5") == "anthropic/claude-opus-5"


# -- key isolation --------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["serp_api_key", "semrush_api_key", "openai_api_key", "perplexity_api_key", "gemini_api_key"],
)
def test_a_missing_vendor_key_never_borrows_the_openrouter_key(or_settings, field):
    missing = or_settings.model_copy(update={field: None})
    with pytest.raises(ConfigurationError, match=field.upper()):
        missing.require(field)


def test_a_blank_key_counts_as_missing(settings):
    blank = settings.model_copy(update={"serp_api_key": SecretStr("")})
    with pytest.raises(ConfigurationError, match="SERP_API_KEY"):
        blank.require("serp_api_key")


def test_the_openrouter_client_reads_only_the_openrouter_key(settings):
    no_key = settings.model_copy(update={"openrouter_api_key": None})
    rec = Recorder(httpx.Response(200, json=_completion("x")))
    client = OpenRouterEngineClient(Engine.PERPLEXITY, no_key, transport=httpx.MockTransport(rec))
    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
        client.ask("q")
    assert rec.requests == []


# -- answers ------------------------------------------------------------------------------


def _engine_client(settings, engine, payload):
    rec = Recorder(httpx.Response(200, json=payload))
    return OpenRouterEngineClient(engine, settings, transport=httpx.MockTransport(rec)), rec


def test_perplexity_request_and_marker_based_claims(or_settings):
    text = (
        "The strongest suites are Ivalua and Coupa [1][2]. "
        "GEP SMART is often praised for supplier risk [3]. "
        "Choose by ERP fit."
    )
    payload = _completion(
        text,
        [_ann(G2, "G2 category"), _ann(COUPA, "Coupa"), _ann(GEP, "GEP"), _ann(G2, "dup")],
    )
    client, rec = _engine_client(or_settings, Engine.PERPLEXITY, payload)
    answer = client.ask("best supplier risk software?")

    req = rec.requests[0]
    assert req.url == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-or-v1-testkey"
    body = json.loads(req.content)
    assert body["model"] == "perplexity/sonar"
    assert "plugins" not in body and "reasoning" not in body  # Perplexity always searches
    assert body["usage"] == {"include": True}
    assert body["messages"] == [{"role": "user", "content": "best supplier risk software?"}]

    assert answer.engine is Engine.PERPLEXITY and answer.model == "perplexity/sonar"
    assert answer.web_triggered and answer.response_id == "gen-123"
    assert [c.url for c in answer.citations] == [G2, COUPA, GEP]  # provider order, deduped
    assert [c.position for c in answer.citations] == [1, 2, 3]
    by_url = {c.url: c.sentence for c in answer.citation_claims}
    assert by_url[GEP].startswith("GEP SMART is often praised")
    assert by_url[COUPA].startswith("The strongest suites")
    # Measured on the probe: these do not come through OpenRouter.
    assert answer.consulted_urls == [] and answer.search_queries == []
    assert answer.source_snippets == []


def test_openai_and_gemini_force_native_search_and_use_spans(or_settings):
    text = "Coupa leads mid-market P2P. GEP SMART suits large enterprises."
    start = text.index("GEP")
    payload = _completion(
        text,
        [_ann(COUPA, "Coupa", 0, 26), _ann(GEP, "GEP", start, len(text))],
        model="openai/gpt-5-mini",
        cost=0.02484,
        searches=2,
    )
    for engine in (Engine.CHATGPT_SEARCH, Engine.GEMINI):
        client, rec = _engine_client(or_settings, engine, payload)
        answer = client.ask("q")
        body = json.loads(rec.requests[0].content)
        assert body["plugins"] == [{"id": "web", "engine": "native"}]
        assert body["reasoning"] == {"effort": "low"}
        assert body["max_tokens"] >= 4000  # reasoning models need room to answer
        assert answer.engine is engine
        assert [c.url for c in answer.citations] == [COUPA, GEP]
        claims = {c.url: c.sentence for c in answer.citation_claims}
        assert claims[GEP].startswith("GEP SMART suits")


def test_a_search_without_citations_still_counts_as_web_triggered(or_settings):
    payload = _completion("An answer.", [], model="openai/gpt-5-mini", searches=2)
    client, _ = _engine_client(or_settings, Engine.CHATGPT_SEARCH, payload)
    answer = client.ask("q")
    assert answer.web_triggered and answer.citations == []


def test_no_annotations_and_no_search_is_not_web_triggered(or_settings):
    payload = _completion("From memory.", None, model="google/gemini-3.6-flash")
    client, _ = _engine_client(or_settings, Engine.GEMINI, payload)
    answer = client.ask("q")
    assert not answer.web_triggered and answer.citations == []


def test_content_as_parts_and_bad_annotations_are_tolerated(or_settings):
    payload = _completion("", [{"type": "file"}, _ann("", "empty"), _ann(GEP, "GEP")])
    payload["choices"][0]["message"]["content"] = [{"type": "text", "text": "GEP [1]."}]
    client, _ = _engine_client(or_settings, Engine.PERPLEXITY, payload)
    answer = client.ask("q")
    assert answer.answer_text == "GEP [1]."
    assert [c.url for c in answer.citations] == [GEP]


def test_the_ledger_records_openrouter_billed_cost(or_settings):
    payload = _completion("x [1].", [_ann(GEP, "GEP")], cost=0.00563)
    client, _ = _engine_client(or_settings, Engine.PERPLEXITY, payload)
    client.ask("q")
    rows = get_usage_ledger(or_settings).calls(vendor="openrouter")
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == "chat.completions" and row.status == "ok"
    assert row.vendor_cost_usd == pytest.approx(0.00563)
    assert row.model == "perplexity/sonar"
    assert (row.input_tokens, row.output_tokens) == (15, 617)
    assert row.estimated_cost_usd == or_settings.cost_perplexity_call_usd


def test_http_errors_follow_the_platform_classification(or_settings):
    for status, error in ((429, IntegrationError), (404, UpstreamClientError)):
        rec = Recorder(httpx.Response(status, json={"error": {"message": "nope"}}))
        client = OpenRouterEngineClient(
            Engine.GEMINI, or_settings, transport=httpx.MockTransport(rec)
        )
        with pytest.raises(error):
            client.ask("q")


def test_only_language_model_platforms_can_be_built():
    with pytest.raises(ValueError, match="cannot be asked through OpenRouter"):
        OpenRouterEngineClient(Engine.GOOGLE_AI_OVERVIEW)


# -- the judge -----------------------------------------------------------------------------

SCHEMA = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}


def _judge(settings, payload):
    rec = Recorder(httpx.Response(200, json=payload))
    return OpenRouterClient(settings, transport=httpx.MockTransport(rec)), rec


def test_classify_sends_claude_with_a_json_schema(or_settings):
    payload = _completion('{"items": []}', model="anthropic/claude-haiku-4.5", cost=0.0011)
    client, rec = _judge(or_settings, payload)
    reply = client.classify(system="rubric", user="1. GEP is fine.", schema=SCHEMA, max_tokens=400)
    body = json.loads(rec.requests[0].content)
    assert rec.requests[0].url.host == "openrouter.ai"
    assert body["model"] == "anthropic/claude-haiku-4.5"
    assert body["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": "rubric"}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert reply.complete and reply.data == {"items": []}
    assert client.model == "anthropic/claude-haiku-4.5"
    row = get_usage_ledger(or_settings).calls(vendor="openrouter")[0]
    assert row.operation == "judge" and row.vendor_cost_usd == pytest.approx(0.0011)


def test_classify_honours_a_model_override_and_strips_code_fences(or_settings):
    fenced = "```json\n" + '{"headline": "ok"}' + "\n```"
    client, rec = _judge(or_settings, _completion(fenced, model="anthropic/claude-sonnet-5"))
    reply = client.classify(
        system="s", user="u", schema=SCHEMA, max_tokens=900, model="claude-sonnet-5"
    )
    assert json.loads(rec.requests[0].content)["model"] == "anthropic/claude-sonnet-5"
    assert reply.data == {"headline": "ok"}


@pytest.mark.parametrize(
    ("finish", "text", "stop"),
    [
        ("length", '{"items": [', "max_tokens"),
        ("content_filter", "", "refusal"),
        ("stop", "nope", "end_turn"),
    ],
)
def test_classify_turns_bad_endings_into_results(or_settings, finish, text, stop):
    client, _ = _judge(or_settings, _completion(text, finish=finish))
    reply = client.classify(system="s", user="u", schema=SCHEMA, max_tokens=100)
    assert reply.stop_reason == stop and reply.data is None and not reply.complete


def test_judge_client_prefers_anthropic_then_openrouter(settings, or_settings):
    both = or_settings.model_copy(update={"anthropic_api_key": SecretStr("sk-ant-x")})
    assert isinstance(judge_client(both), AnthropicJudgeClient)
    assert type(judge_client(or_settings)) is OpenRouterClient
    forced = both.model_copy(update={"llm_route": "openrouter"})
    assert type(judge_client(forced)) is OpenRouterClient
    assert judge_client(or_settings.model_copy(update={"openrouter_api_key": None})) is None
