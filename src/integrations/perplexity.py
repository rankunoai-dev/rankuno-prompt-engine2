"""Perplexity connector on the Agent API (`/v1/responses`) with Perplexity's own model.

History (ADR 0004, amended in ADR 0011): `/chat/completions` with `sonar-pro`
was retired by Perplexity ("Sonar is now the Agent API", HTTP 403). The Agent
API is a model router; a parallel session pointed it at `google/gemini-3.6-flash`,
which measured Gemini, not Perplexity, and without the `web_search` tool the
API returns no sources at all, so every prompt read "Perplexity: not cited".

This connector sends `perplexity/sonar` (Perplexity's own model) with the
`web_search` tool forced through `tool_choice` (left on `auto`, the model
skipped the search on the branded test prompt). The API answers with a
`search_results` output item (the sources the product shows in its "Sources"
panel) followed by the message.

Citation definition: the search results, in the order the model cites them
with `[n]` markers in the text when such markers are present, otherwise in the
API's order. The prompt is sent verbatim; no citation-format instruction is
appended, so the query is never altered by the tracker.

Web-trigger definition: a non-empty `search_results` item.
"""

from __future__ import annotations

import re
import time
from typing import Any, ClassVar

import httpx

from src.core.config import Settings
from src.core.domains import registrable_domain
from src.core.logger import get_logger
from src.integrations.base_client import BaseAPIClient
from src.integrations.claims import sentences_with_marker
from src.integrations.http import check_response, json_client, parse_json
from src.integrations.schemas import Citation, CitationClaim, Engine, EngineAnswer, SourceSnippet

__all__ = ["PERPLEXITY_NATIVE_MODEL", "PerplexityClient"]

_logger = get_logger("integrations.perplexity")

PERPLEXITY_NATIVE_MODEL = "perplexity/sonar"
_LEGACY_MODELS = {"sonar", "sonar-pro", "sonar-reasoning", "sonar-reasoning-pro"}
_MARKER = re.compile(r"\[(\d{1,3})\]")
_MIN_TIMEOUT_S = 120.0  # the Agent API routinely takes 45-60 s per answer


class PerplexityClient(BaseAPIClient):
    """Asks a prompt on Perplexity and returns its sources in citation order."""

    service_name: ClassVar[str] = "perplexity"
    rate_limit_key: ClassVar[str] = "perplexity.responses"
    requests_per_minute: ClassVar[int] = 50
    base_url: ClassVar[str] = "https://api.perplexity.ai/v1/responses"

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
        """Create the HTTP session with the bearer token and a long enough timeout."""
        key = self._settings.require("perplexity_api_key")
        self._http = json_client(
            max(self._settings.default_timeout_s, _MIN_TIMEOUT_S),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            transport=self._transport,
        )

    @property
    def model(self) -> str:
        """Configured model; retired Sonar chat names map to Perplexity's Agent API model."""
        configured = self._settings.perplexity_model.strip()
        if configured in _LEGACY_MODELS:
            return PERPLEXITY_NATIVE_MODEL
        return configured

    def ask(self, prompt: str) -> EngineAnswer:
        """Run `prompt` with web search and normalise the answer."""
        if self._http is None:
            self.authenticate()
        model = self.model
        body = {
            "model": model,
            "input": prompt,
            "tools": [{"type": "web_search"}],
            # With tool_choice "auto" the model often answers from memory and the
            # payload has no sources; the Perplexity product always searches.
            "tool_choice": {"type": "web_search"},
        }
        started = time.perf_counter()

        def request() -> dict[str, Any]:
            response = self._http.post(self.base_url, json=body)  # type: ignore[union-attr]
            check_response(self.service_name, response)
            return parse_json(self.service_name, response)

        payload = self.call(
            "v1.responses", request, estimated_cost_usd=self._settings.cost_perplexity_call_usd
        )
        answer = _parse_response(payload, prompt=prompt, model=model)
        answer.latency_ms = (time.perf_counter() - started) * 1000
        self.note_usage(model=answer.model, **_usage_fields(payload))
        _logger.info(
            "perplexity_answer",
            extra={
                "model": answer.model,
                "web_triggered": answer.web_triggered,
                "citations": len(answer.citations),
            },
        )
        return answer


def _dict(value: Any) -> dict[str, Any]:
    """`value` if it is a JSON object, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _usage_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Tokens, search invocations and the vendor-reported USD cost from `usage`."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    cost = _dict(usage.get("cost"))
    search = _dict(_dict(usage.get("tool_calls_details")).get("search_web"))
    details = _dict(usage.get("input_tokens_details"))
    total = cost.get("total_cost")

    def _int(value: Any) -> int | None:
        return int(value) if isinstance(value, int | float) and value >= 0 else None

    return {
        "input_tokens": _int(usage.get("input_tokens")),
        "output_tokens": _int(usage.get("output_tokens")),
        "cached_tokens": _int(details.get("cached_tokens")),
        "search_calls": _int(search.get("invocation")) or 0,
        "vendor_cost_usd": float(total) if isinstance(total, int | float) and total >= 0 else None,
    }


def _parse_response(payload: dict[str, Any], *, prompt: str, model: str) -> EngineAnswer:
    """Extract answer text and ordered sources from an Agent API payload."""
    text_parts: list[str] = []
    results: list[dict[str, Any]] = []
    queries: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "search_results":
            results.extend(r for r in item.get("results") or [] if isinstance(r, dict))
            for query in item.get("queries") or []:
                if isinstance(query, str) and query.strip():
                    queries.append(query.strip())
        elif item.get("type") == "message":
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text_parts.append(str(content.get("text") or ""))
    text = "\n".join(text_parts).strip()
    if not text and isinstance(payload.get("output_text"), str):
        text = payload["output_text"].strip()

    # Legacy chat-completions shape, kept so a recorded payload still parses.
    if not results:
        for url in payload.get("citations") or []:
            if isinstance(url, str) and url:
                results.append({"url": url})
        for entry in payload.get("search_results") or []:
            if isinstance(entry, dict) and isinstance(entry.get("url"), str):
                results.append(entry)

    by_url: dict[str, dict[str, Any]] = {}
    for result in results:
        url = result.get("url")
        if not isinstance(url, str) or not url:
            continue
        entry = by_url.setdefault(url, {})
        for key, value in result.items():  # first occurrence wins; later ones fill gaps
            if value not in (None, ""):
                entry.setdefault(key, value)

    ordered = list(by_url)
    by_id = {int(r["id"]): u for u, r in by_url.items() if str(r.get("id", "")).isdigit()}
    cited_ids = list(dict.fromkeys(int(m) for m in _MARKER.findall(text)))
    if cited_ids:
        marked = [by_id[i] for i in cited_ids if i in by_id]
        if marked:
            ordered = marked + [u for u in ordered if u not in marked]

    claims: list[CitationClaim] = []
    for rid, url in by_id.items():
        for sentence, start, end in sentences_with_marker(text, rid):
            claims.append(CitationClaim(url=url, sentence=sentence, start=start, end=end))
    snippets = [
        SourceSnippet(
            url=url,
            snippet=str(r.get("snippet") or "")[:1000],
            title=str(r.get("title") or "") or None,
            date=str(r.get("date") or r.get("last_updated") or "") or None,
        )
        for url, r in by_url.items()
        if r.get("snippet") or r.get("date") or r.get("last_updated")
    ]

    citations: list[Citation] = []
    for url in ordered:
        domain = registrable_domain(url)
        if not domain:
            continue
        title = str(by_url[url].get("title") or "") or None
        citations.append(Citation(url=url, domain=domain, title=title, position=len(citations) + 1))

    return EngineAnswer(
        engine=Engine.PERPLEXITY,
        model=str(payload.get("model") or model),
        prompt=prompt,
        answer_text=text,
        web_triggered=bool(citations),
        citations=citations,
        consulted_urls=[],
        search_queries=list(dict.fromkeys(queries)),
        citation_claims=claims,
        source_snippets=snippets,
        response_id=payload.get("id"),
    )
