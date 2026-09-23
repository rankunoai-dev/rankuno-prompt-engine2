"""Tests for the Anthropic judge connector (ADR 0021)."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from src.core.errors import IntegrationError, UpstreamClientError
from src.integrations.anthropic_judge import AnthropicJudgeClient
from src.integrations.usage import get_usage_ledger

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array"}},
    "required": ["items"],
    "additionalProperties": False,
}


def _payload(text: str, *, stop: str = "end_turn", model: str = "claude-haiku-4-5") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": 900, "output_tokens": 120, "cache_read_input_tokens": 300},
    }


class Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


@pytest.fixture
def judge_settings(settings):
    return settings.model_copy(update={"anthropic_api_key": SecretStr("test-anthropic-key")})


def _client(settings, response: httpx.Response) -> tuple[AnthropicJudgeClient, Recorder]:
    recorder = Recorder(response)
    return AnthropicJudgeClient(settings, transport=httpx.MockTransport(recorder)), recorder


def _classify(client: AnthropicJudgeClient):
    return client.classify(
        system="You label sentences.", user="1. GEP is great.", schema=SCHEMA, max_tokens=400
    )


def test_request_shape_is_structured_and_deterministic(judge_settings):
    client, rec = _client(judge_settings, httpx.Response(200, json=_payload('{"items": []}')))
    reply = _classify(client)
    req = rec.requests[0]
    assert req.url == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "test-anthropic-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["model"] == "claude-haiku-4-5"
    assert body["temperature"] == 0
    assert "thinking" not in body
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert body["system"] == "You label sentences."
    assert body["messages"] == [{"role": "user", "content": "1. GEP is great."}]
    assert reply.complete and reply.data == {"items": []}
    assert (reply.input_tokens, reply.output_tokens, reply.cached_tokens) == (900, 120, 300)


def test_refusal_and_truncation_are_results_not_errors(judge_settings):
    client, _ = _client(judge_settings, httpx.Response(200, json=_payload("", stop="refusal")))
    reply = _classify(client)
    assert reply.stop_reason == "refusal" and reply.data is None and not reply.complete

    truncated = httpx.Response(200, json=_payload('{"items": [{"id": 1', stop="max_tokens"))
    client, _ = _client(judge_settings, truncated)
    reply = _classify(client)
    assert reply.stop_reason == "max_tokens" and reply.data is None


def test_malformed_json_on_end_turn_yields_no_data(judge_settings):
    client, _ = _client(judge_settings, httpx.Response(200, json=_payload("not json")))
    assert _classify(client).data is None
    client, _ = _client(judge_settings, httpx.Response(200, json=_payload("[1, 2]")))
    assert _classify(client).data is None  # a list is not the object the schema asked for


def test_ledger_row_carries_model_tokens_and_modelled_cost(judge_settings):
    client, _ = _client(judge_settings, httpx.Response(200, json=_payload('{"items": []}')))
    _classify(client)
    rows = get_usage_ledger(judge_settings).calls(vendor="anthropic")
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == "judge" and row.status == "ok"
    assert row.model == "claude-haiku-4-5"
    assert (row.input_tokens, row.output_tokens, row.cached_tokens) == (900, 120, 300)
    # 600 billable input at $1/M + 300 cached at $0.10/M + 120 output at $5/M
    assert row.modelled_cost_usd == pytest.approx(0.0006 + 0.00003 + 0.0006, abs=1e-8)
    assert row.estimated_cost_usd == judge_settings.cost_anthropic_judge_call_usd


def test_http_errors_follow_the_platform_classification(judge_settings):
    client, _ = _client(judge_settings, httpx.Response(429, json={"error": "rate limited"}))
    with pytest.raises(IntegrationError):
        _classify(client)
    client, _ = _client(judge_settings, httpx.Response(400, json={"error": "bad schema"}))
    with pytest.raises(UpstreamClientError):
        _classify(client)


def test_missing_key_is_a_configuration_error(settings):
    client, _ = _client(settings, httpx.Response(200, json=_payload("{}")))
    with pytest.raises(Exception, match="(?i)anthropic"):
        _classify(client)
