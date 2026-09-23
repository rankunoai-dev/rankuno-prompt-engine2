"""OpenAI Responses API connector with the `web_search` tool — "ChatGPT Search".

Why the Responses API and not Chat Completions: a plain `gpt-4o-mini` chat
completion never touches the web, so it cannot produce citations — the Phase 1
"200 OK" against `/v1/chat/completions` validated the key, not search. The
`*-search-preview` chat models do search, but OpenAI may retire preview models
on two weeks' notice, which is unacceptable for a scheduled tracker.

The tool is forced via `tool_choice` so every sample actually searches, as the
ChatGPT Search product does; a run where the model skipped the tool is not a
measurement of search citations.

Web-trigger definition for this engine: the response contains at least one
`web_search_call` item. Citations are the `url_citation` annotations on the
output text, ordered by where they first appear in the answer. The tool's
`sources` list (everything consulted) is kept separately.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

import httpx

from src.core.config import Settings
from src.core.domains import registrable_domain
from src.core.locale import Locale
from src.core.logger import get_logger
from src.integrations.base_client import BaseAPIClient
from src.integrations.claims import claim_sentence
from src.integrations.http import check_response, json_client, parse_json
from src.integrations.pricing import modelled_cost
from src.integrations.schemas import Citation, CitationClaim, Engine, EngineAnswer

__all__ = ["OpenAISearchClient"]

_logger = get_logger("integrations.openai_search")


class OpenAISearchClient(BaseAPIClient):
    """Asks a prompt with live web search and returns the cited sources."""

    service_name: ClassVar[str] = "openai"
    rate_limit_key: ClassVar[str] = "openai.responses"
    requests_per_minute: ClassVar[int] = 60
    base_url: ClassVar[str] = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        locale: Locale | None = None,
    ) -> None:
        """Build a client; see `BaseAPIClient`. `locale` defaults to the settings one."""
        super().__init__(settings)
        self._locale = locale or self._settings.default_locale()
        self._transport = transport
        self._http: httpx.Client | None = None

    def authenticate(self) -> None:
        """Create the HTTP session with the bearer token."""
        key = self._settings.require("openai_api_key")
        self._http = json_client(
            self._settings.default_timeout_s,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            transport=self._transport,
        )

    def ask(self, prompt: str) -> EngineAnswer:
        """Run `prompt` with web search enabled and normalise the answer."""
        if self._http is None:
            self.authenticate()
        assert self._http is not None  # noqa: S101 - narrowed by authenticate()
        model = self._settings.openai_search_model
        body = {
            "model": model,
            "input": prompt,
            "tools": [{"type": "web_search", "user_location": self._locale.openai_user_location()}],
            # ChatGPT Search always searches; without forcing the tool the model
            # answers "how do I" prompts from memory and returns no citations.
            "tool_choice": {"type": "web_search"},
            "include": ["web_search_call.action.sources"],
        }
        started = time.perf_counter()

        def request() -> dict[str, Any]:
            response = self._http.post(self.base_url, json=body)  # type: ignore[union-attr]
            check_response(self.service_name, response)
            return parse_json(self.service_name, response)

        payload = self.call(
            "responses.create",
            request,
            estimated_cost_usd=self._settings.cost_openai_search_call_usd,
        )
        answer = _parse_response(payload, prompt=prompt, model=model)
        answer.latency_ms = (time.perf_counter() - started) * 1000
        usage = _dict(payload.get("usage"))
        details = _dict(usage.get("input_tokens_details"))
        searches = sum(
            1 for i in _list(payload.get("output")) if i.get("type") == "web_search_call"
        )
        tokens = {
            "input_tokens": _int(usage.get("input_tokens")),
            "output_tokens": _int(usage.get("output_tokens")),
            "cached_tokens": _int(details.get("cached_tokens")),
        }
        self.note_usage(
            model=answer.model,
            search_calls=searches,
            modelled_cost_usd=modelled_cost(
                "openai", answer.model, search_calls=searches, **tokens
            ),
            **tokens,
        )
        _logger.info(
            "openai_answer",
            extra={"web_triggered": answer.web_triggered, "citations": len(answer.citations)},
        )
        return answer


def _parse_response(payload: dict[str, Any], *, prompt: str, model: str) -> EngineAnswer:
    """Extract text, web-search evidence and citations from a Responses payload."""
    web_triggered = False
    consulted: list[str] = []
    queries: list[str] = []
    text_parts: list[str] = []
    raw_citations: list[tuple[int, str, str | None]] = []

    for item in _list(payload.get("output")):
        kind = item.get("type")
        if kind == "web_search_call":
            web_triggered = True
            action = item.get("action") or {}
            single = action.get("query")
            if isinstance(single, str) and single.strip():
                queries.append(single.strip())
            for query in action.get("queries") or []:
                if isinstance(query, str) and query.strip():
                    queries.append(query.strip())
            for source in _list(action.get("sources")):
                url = source.get("url") if isinstance(source, dict) else source
                if isinstance(url, str) and url:
                    consulted.append(url)
        elif kind == "message":
            for content in _list(item.get("content")):
                if content.get("type") != "output_text":
                    continue
                base = sum(len(p) + 1 for p in text_parts)  # parts are joined with "\n"
                text_parts.append(str(content.get("text", "")))
                for ann in _list(content.get("annotations")):
                    if ann.get("type") != "url_citation":
                        continue
                    url = ann.get("url")
                    if isinstance(url, str) and url:
                        raw_citations.append(
                            (base + int(ann.get("start_index", 0)), url, ann.get("title"))
                        )

    citations: list[Citation] = []
    seen: set[str] = set()
    for _, url, title in sorted(raw_citations, key=lambda c: c[0]):
        if url in seen:
            continue
        seen.add(url)
        domain = registrable_domain(url)
        if not domain:
            continue
        citations.append(Citation(url=url, domain=domain, title=title, position=len(citations) + 1))

    joined = "\n".join(text_parts)
    claims: list[CitationClaim] = []
    claimed: set[tuple[str, int]] = set()
    for index, url, _ in raw_citations:
        sentence, start, end = claim_sentence(joined, index)
        if sentence and (url, start) not in claimed:
            claimed.add((url, start))
            claims.append(CitationClaim(url=url, sentence=sentence, start=start, end=end))

    return EngineAnswer(
        engine=Engine.CHATGPT_SEARCH,
        model=str(payload.get("model") or model),
        prompt=prompt,
        answer_text=joined.strip(),
        web_triggered=web_triggered,
        citations=citations,
        consulted_urls=list(dict.fromkeys(consulted)),
        search_queries=list(dict.fromkeys(queries)),
        citation_claims=claims,
        response_id=payload.get("id"),
    )


def _list(value: Any) -> list[dict[str, Any]]:
    """Coerce an optional JSON array of objects into a list of dicts."""
    if not isinstance(value, list):
        return []
    return [v if isinstance(v, dict) else {"url": v} for v in value]


def _dict(value: Any) -> dict[str, Any]:
    """`value` if it is a JSON object, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    """Non-negative int from a JSON number, else None."""
    return int(value) if isinstance(value, int | float) and value >= 0 else None
