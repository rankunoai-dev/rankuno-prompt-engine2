"""OpenRouter: one key and one credit balance for every language-model platform (ADR 0026).

OpenRouter forwards a chat-completions request to the named provider. For
OpenAI, Google and Perplexity models it runs the provider's own web search
when asked for `engine: "native"`, so a ChatGPT, Gemini or Perplexity answer
fetched here is still that provider's answer, grounded by that provider's
search. What changes is the envelope: every provider's reply is normalised to
text plus `url_citation` annotations. The extras the direct connectors read
(ChatGPT's consulted pages, fan-out queries, Perplexity's source dates) do not
survive, so those fields stay empty on this route. That was measured, not
assumed: see the probe in build log 0023.

Two things this client does that the direct connectors cannot:

* The reply carries OpenRouter's billed cost (`usage.cost`), which is written
  to the ledger as the vendor-reported cost, so spend reports show money, not
  estimates.
* The same transport serves the sentiment judge and the report narrative
  (`classify`), so every LLM call shares one rate limiter, one breaker and one
  ledger vendor.

Google AI Overview (SerpApi) and Semrush are not language models and never
route here; their own keys are required.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, ClassVar

import httpx

from src.core.config import Settings
from src.core.domains import registrable_domain
from src.core.logger import get_logger
from src.integrations.anthropic_judge import StructuredReply
from src.integrations.base_client import BaseAPIClient
from src.integrations.claims import claim_sentence
from src.integrations.http import check_response, json_client, parse_json
from src.integrations.schemas import Citation, CitationClaim, Engine, EngineAnswer

__all__ = [
    "OPENROUTER_ENGINES",
    "OpenRouterClient",
    "OpenRouterEngineClient",
    "engine_route",
    "judge_client",
    "openrouter_claude_model",
    "openrouter_engine_model",
]

_logger = get_logger("integrations.openrouter")

OPENROUTER_ENGINES = frozenset({Engine.CHATGPT_SEARCH, Engine.PERPLEXITY, Engine.GEMINI})
"""Platforms that are language models and can be reached through OpenRouter."""

_DIRECT_KEY = {
    Engine.CHATGPT_SEARCH: "openai_api_key",
    Engine.PERPLEXITY: "perplexity_api_key",
    Engine.GEMINI: "gemini_api_key",
}
# Models whose provider search OpenRouter refuses in native mode (probe, 2026-09-29:
# openai/gpt-4o-mini -> 404 "does not support native web search").
_NO_NATIVE_SEARCH = frozenset({"gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1-nano"})
_MARKER = re.compile(r"\[(\d{1,3})\]")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)
_MAX_ANSWER_TOKENS = 4000


def _has(settings: Settings, field: str) -> bool:
    value = getattr(settings, field, None)
    return value is not None and bool(value.get_secret_value())


def engine_route(settings: Settings, engine: Engine) -> str:
    """`"direct"` or `"openrouter"` for one platform, per `LLM_ROUTE`.

    * `openrouter`: every language-model platform uses OpenRouter.
    * `direct`: every platform uses its own vendor key.
    * `auto` (default): the vendor key when it is set, OpenRouter otherwise.

    Platforms that are not language models are always direct.
    """
    if engine not in OPENROUTER_ENGINES:
        return "direct"
    mode = settings.llm_route
    if mode == "direct":
        return "direct"
    if mode == "openrouter":
        return "openrouter"
    if _has(settings, _DIRECT_KEY[engine]) or not _has(settings, "openrouter_api_key"):
        return "direct"
    return "openrouter"


def openrouter_engine_model(settings: Settings, engine: Engine) -> str:
    """The OpenRouter model id for a platform, from the configured (or project) model.

    A value that already names a provider (`openai/gpt-5-mini`) is used as is.
    A bare vendor id gains its provider prefix. OpenAI models with no native
    search on OpenRouter fall back to `OPENROUTER_CHATGPT_MODEL`.
    """
    if engine is Engine.CHATGPT_SEARCH:
        configured = settings.openai_search_model.strip()
        if "/" in configured:
            return configured
        if configured in _NO_NATIVE_SEARCH or not configured:
            return settings.openrouter_chatgpt_model
        return f"openai/{configured}"
    if engine is Engine.PERPLEXITY:
        configured = settings.perplexity_model.strip() or "sonar"
        return configured if "/" in configured else f"perplexity/{configured}"
    if engine is Engine.GEMINI:
        configured = settings.gemini_model.strip().removeprefix("models/") or "gemini-3.6-flash"
        return configured if "/" in configured else f"google/{configured}"
    msg = f"{engine.value} is not a language model and has no OpenRouter route."
    raise ValueError(msg)


def openrouter_claude_model(model: str) -> str:
    """`claude-haiku-4-5` -> `anthropic/claude-haiku-4.5`; ids with a provider pass through."""
    name = model.strip()
    if "/" in name:
        return name
    return "anthropic/" + re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", name)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text", "output_text")
        )
    return ""


def _usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    return usage if isinstance(usage, dict) else {}


class OpenRouterClient(BaseAPIClient):
    """Chat completions on OpenRouter: structured classification for the judge and report."""

    service_name: ClassVar[str] = "openrouter"
    rate_limit_key: ClassVar[str] = "openrouter.chat"
    requests_per_minute: ClassVar[int] = 60
    base_url: ClassVar[str] = "https://openrouter.ai/api/v1/chat/completions"

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
        """Create the HTTP session with the OpenRouter key; no other key is ever read."""
        key = self._settings.require("openrouter_api_key")
        self._http = json_client(
            self._settings.default_timeout_s,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "X-Title": "RankUno prompt tracker",
            },
            transport=self._transport,
        )

    def _post(self, operation: str, body: dict[str, Any], estimated: float) -> dict[str, Any]:
        if self._http is None:
            self.authenticate()
        assert self._http is not None  # noqa: S101 - narrowed by authenticate()

        def request() -> dict[str, Any]:
            response = self._http.post(self.base_url, json=body)  # type: ignore[union-attr]
            check_response(self.service_name, response)
            return parse_json(self.service_name, response)

        return self.call(operation, request, estimated_cost_usd=estimated)

    def _note(self, payload: dict[str, Any], model: str) -> dict[str, Any]:
        """Write tokens, searches and OpenRouter's billed cost to the ledger row."""
        usage = _usage(payload)
        details = usage.get("prompt_tokens_details")
        tools = usage.get("server_tool_use_details")
        cost = usage.get("cost")
        self.note_usage(
            model=str(payload.get("model") or model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int((details or {}).get("cached_tokens") or 0)
            if isinstance(details, dict)
            else 0,
            search_calls=int((tools or {}).get("web_search_requests") or 0)
            if isinstance(tools, dict)
            else 0,
            vendor_cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        )
        return usage

    # -- the judge interface (same shape as AnthropicJudgeClient) ----------------------

    @property
    def model(self) -> str:
        """The judge model, as an OpenRouter id."""
        return openrouter_claude_model(self._settings.anthropic_judge_model)

    def classify(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        operation: str = "judge",
        model: str | None = None,
        estimated_cost_usd: float | None = None,
    ) -> StructuredReply:
        """Ask for a JSON reply matching `schema`; a refusal or truncation is a result."""
        chosen = openrouter_claude_model(model) if model else self.model
        body = {
            "model": chosen,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            },
            "usage": {"include": True},
        }
        started = time.perf_counter()
        payload = self._post(
            operation,
            body,
            self._settings.cost_anthropic_judge_call_usd
            if estimated_cost_usd is None
            else estimated_cost_usd,
        )
        usage = self._note(payload, chosen)
        choice = (payload.get("choices") or [{}])[0]
        finish = (
            str(choice.get("finish_reason") or "error") if isinstance(choice, dict) else "error"
        )
        stop = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}.get(
            finish, finish
        )
        message = choice.get("message") if isinstance(choice, dict) else None
        text = _FENCE.sub("", _text((message or {}).get("content")).strip())
        data: dict[str, Any] | None = None
        if stop == "end_turn" and text:
            try:
                parsed = json.loads(text)
                data = parsed if isinstance(parsed, dict) else None
            except ValueError:
                data = None
        details = usage.get("prompt_tokens_details")
        return StructuredReply(
            data=data,
            stop_reason=stop,
            model=str(payload.get("model") or chosen),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int((details or {}).get("cached_tokens") or 0)
            if isinstance(details, dict)
            else 0,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class OpenRouterEngineClient(OpenRouterClient):
    """One answer engine (ChatGPT, Perplexity or Gemini) asked through OpenRouter."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the platform; the model follows the settings the run was built with."""
        if engine not in OPENROUTER_ENGINES:
            msg = f"{engine.value} cannot be asked through OpenRouter."
            raise ValueError(msg)
        super().__init__(settings, transport=transport)
        self.engine = engine

    @property
    def engine_model(self) -> str:
        """The OpenRouter model id this platform is asked with."""
        return openrouter_engine_model(self._settings, self.engine)

    def ask(self, prompt: str) -> EngineAnswer:
        """Ask with the provider's own web search and normalise to an `EngineAnswer`."""
        model = self.engine_model
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": _MAX_ANSWER_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
            "usage": {"include": True},
        }
        if self.engine is not Engine.PERPLEXITY:
            # Perplexity always searches; the others must be told to, and told to use
            # their own search rather than OpenRouter's Exa fallback.
            body["plugins"] = [{"id": "web", "engine": "native"}]
            # Reasoning models spend the token budget thinking; keep it small so the
            # answer is not cut off (probe: 512 of 602 tokens went on reasoning).
            body["reasoning"] = {"effort": "low"}
        started = time.perf_counter()
        payload = self._post("chat.completions", body, _estimate(self._settings, self.engine))
        usage = self._note(payload, model)
        answer = _parse_answer(payload, engine=self.engine, prompt=prompt, model=model)
        tools = usage.get("server_tool_use_details")
        searches = (
            int((tools or {}).get("web_search_requests") or 0) if isinstance(tools, dict) else 0
        )
        answer.web_triggered = bool(answer.citations) or searches > 0
        answer.latency_ms = (time.perf_counter() - started) * 1000
        _logger.info(
            "openrouter_answer",
            extra={
                "engine": self.engine.value,
                "model": answer.model,
                "citations": len(answer.citations),
                "searches": searches,
                "cost": usage.get("cost"),
            },
        )
        return answer


def _estimate(settings: Settings, engine: Engine) -> float:
    return {
        Engine.CHATGPT_SEARCH: settings.cost_openai_search_call_usd,
        Engine.PERPLEXITY: settings.cost_perplexity_call_usd,
        Engine.GEMINI: settings.cost_gemini_grounded_call_usd,
    }[engine]


def _parse_answer(
    payload: dict[str, Any], *, engine: Engine, prompt: str, model: str
) -> EngineAnswer:
    """Text, citations and sentence claims from a chat-completions reply.

    Citations come from `url_citation` annotations, ordered by where they first
    appear. When the provider gives character spans (OpenAI, Google), each claim
    is the sentence at the span. When it does not (Perplexity returns zero spans
    and numbered `[n]` markers instead), a marker `[n]` ties its sentence to the
    n-th annotation.
    """
    choices = payload.get("choices") or [{}]
    choice: dict[str, Any] = choices[0] if isinstance(choices[0], dict) else {}
    raw_message = choice.get("message")
    message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
    text = _text(message.get("content")).strip()
    raw: list[tuple[int, str, str | None]] = []
    ordered: list[str] = []
    for order, ann in enumerate(message.get("annotations") or []):
        if not isinstance(ann, dict) or ann.get("type") != "url_citation":
            continue
        nested = ann.get("url_citation")
        cit: dict[str, Any] = nested if isinstance(nested, dict) else ann
        url = cit.get("url")
        if not isinstance(url, str) or not url:
            continue
        start = int(cit.get("start_index") or 0)
        end = int(cit.get("end_index") or 0)
        title = cit.get("title") if isinstance(cit.get("title"), str) else None
        ordered.append(url)
        # Zero spans carry no position: keep the provider's order instead.
        raw.append((start if end > start else 10**9 + order, url, title))

    citations: list[Citation] = []
    seen: set[str] = set()
    for _, url, title in sorted(raw, key=lambda r: r[0]):
        if url in seen:
            continue
        seen.add(url)
        domain = registrable_domain(url)
        if domain:
            citations.append(
                Citation(url=url, domain=domain, title=title, position=len(citations) + 1)
            )

    claims: list[CitationClaim] = []
    claimed: set[tuple[str, int]] = set()
    spans = [(pos, url) for pos, url, _ in raw if pos < 10**9]
    if spans:
        for index, url in spans:
            sentence, start, end = claim_sentence(text, index)
            if sentence and (url, start) not in claimed:
                claimed.add((url, start))
                claims.append(CitationClaim(url=url, sentence=sentence, start=start, end=end))
    else:
        for match in _MARKER.finditer(text):
            n = int(match.group(1))
            if not 1 <= n <= len(ordered):
                continue
            url = ordered[n - 1]
            sentence, start, end = claim_sentence(text, match.start())
            if sentence and (url, start) not in claimed:
                claimed.add((url, start))
                claims.append(CitationClaim(url=url, sentence=sentence, start=start, end=end))

    return EngineAnswer(
        engine=engine,
        model=str(payload.get("model") or model),
        prompt=prompt,
        answer_text=text,
        web_triggered=bool(citations),
        citations=citations,
        citation_claims=claims,
        response_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
    )


def judge_client(settings: Settings) -> Any:
    """The client for sentiment and the report narrative, or None when neither key is set.

    The Anthropic key wins when present (direct, no intermediary). Otherwise the
    OpenRouter key serves the same Claude model through OpenRouter.
    """
    from src.integrations.anthropic_judge import AnthropicJudgeClient  # noqa: PLC0415

    if settings.llm_route != "openrouter" and _has(settings, "anthropic_api_key"):
        return AnthropicJudgeClient(settings)
    if _has(settings, "openrouter_api_key"):
        return OpenRouterClient(settings)
    return None
