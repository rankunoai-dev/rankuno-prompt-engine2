"""Anthropic Messages API connector used as a judge (ADR 0021).

The tracker never asks Claude a question of its own; it asks Claude to label
text the engines already produced. This client therefore exposes one
operation, `classify`: a system rubric, a user payload, and a JSON schema the
reply must satisfy (`output_config.format`). Everything else, the rubric, the
batching and what the labels mean, lives in the caller.

Design stance:

* Raw HTTP through the shared helper, like every other connector, so the
  rate limiter, breaker, retries and ledger are the same code path.
* No thinking, temperature 0, a small `max_tokens`: this is classification.
* A refusal or a truncated reply is a *result*, not an exception. The caller
  decides what "unscored" means; the crawl must never fail because of it.
* Judging is a read: it changes nothing anywhere and needs no approval.
"""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar

import httpx
from pydantic import Field

from src.core.config import Settings
from src.core.logger import get_logger
from src.core.schemas import StrictModel
from src.integrations.base_client import BaseAPIClient
from src.integrations.http import check_response, json_client, parse_json
from src.integrations.pricing import modelled_cost

__all__ = ["AnthropicJudgeClient", "StructuredReply"]

_logger = get_logger("integrations.anthropic_judge")
_API_VERSION = "2023-06-01"


class StructuredReply(StrictModel):
    """What one `classify` call produced."""

    data: dict[str, Any] | None = Field(
        default=None, description="Parsed JSON when the reply was complete and well formed."
    )
    stop_reason: str = Field(description="end_turn | max_tokens | refusal | error | ...")
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)

    @property
    def complete(self) -> bool:
        """True when the reply carries usable JSON."""
        return self.data is not None and self.stop_reason == "end_turn"


class AnthropicJudgeClient(BaseAPIClient):
    """Structured classification over the Messages API."""

    service_name: ClassVar[str] = "anthropic"
    rate_limit_key: ClassVar[str] = "anthropic.messages"
    requests_per_minute: ClassVar[int] = 50
    base_url: ClassVar[str] = "https://api.anthropic.com/v1/messages"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a client; see `BaseAPIClient`."""
        super().__init__(settings)
        self._transport = transport
        self._http: httpx.Client | None = None

    def authenticate(self) -> None:
        """Create the HTTP session with the API key header."""
        key = self._settings.require("anthropic_api_key")
        self._http = json_client(
            self._settings.default_timeout_s,
            headers={
                "x-api-key": key,
                "anthropic-version": _API_VERSION,
                "Content-Type": "application/json",
            },
            transport=self._transport,
        )

    @property
    def model(self) -> str:
        """The configured judge model."""
        return self._settings.anthropic_judge_model

    def classify(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        operation: str = "judge",
    ) -> StructuredReply:
        """Ask for a JSON reply matching `schema`; never raises for a refusal or truncation.

        Transport and vendor errors still raise `IntegrationError` /
        `UpstreamClientError` through `call()`, after the standard retries, so
        the caller can mark the batch unscored and move on.
        """
        if self._http is None:
            self.authenticate()
        assert self._http is not None  # noqa: S101 - narrowed by authenticate()
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        started = time.perf_counter()

        def request() -> dict[str, Any]:
            response = self._http.post(self.base_url, json=body)  # type: ignore[union-attr]
            check_response(self.service_name, response)
            return parse_json(self.service_name, response)

        payload = self.call(
            operation, request, estimated_cost_usd=self._settings.cost_anthropic_judge_call_usd
        )
        reply = _parse_reply(payload, fallback_model=self.model)
        reply.latency_ms = (time.perf_counter() - started) * 1000
        self.note_usage(
            model=reply.model,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cached_tokens=reply.cached_tokens,
            modelled_cost_usd=modelled_cost(
                "anthropic",
                reply.model,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cached_tokens=reply.cached_tokens,
            ),
        )
        _logger.info(
            "judge_reply",
            extra={
                "stop_reason": reply.stop_reason,
                "complete": reply.complete,
                "output_tokens": reply.output_tokens,
            },
        )
        return reply


def _parse_reply(payload: dict[str, Any], *, fallback_model: str) -> StructuredReply:
    """Turn a Messages response into a `StructuredReply`; malformed JSON yields no data."""
    raw_usage = payload.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    stop = str(payload.get("stop_reason") or "error")
    text = "".join(
        str(block.get("text") or "")
        for block in payload.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
    data: dict[str, Any] | None = None
    if stop == "end_turn" and text.strip():
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else None
        except ValueError:
            data = None
    return StructuredReply(
        data=data,
        stop_reason=stop,
        model=str(payload.get("model") or fallback_model),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cached_tokens=int(usage.get("cache_read_input_tokens") or 0),
    )
