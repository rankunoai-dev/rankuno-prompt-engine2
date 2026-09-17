"""Google Gemini connector with Grounding with Google Search.

Two facts about this API drive the design:

* Grounding must be requested (`tools: [{"google_search": {}}]`). Without it the
  model answers from weights and there is no `groundingMetadata` — which is
  what the Phase 1 validation call did, so it proved nothing about citations.
* The `groundingChunks[].web.uri` values are Google redirect links, not the
  source pages. When the API also supplies `web.domain` the connector uses it;
  otherwise the citation is marked `resolved=False` and the module layer decides
  whether to follow the redirect through `RedirectResolver`.

The API key travels in the `x-goog-api-key` header, never in the query string,
so it cannot leak through access logs or the audit trail.

Web-trigger definition: `groundingMetadata.webSearchQueries` is non-empty.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

import httpx

from src.core.config import Settings
from src.core.domains import normalize_domain, registrable_domain
from src.core.logger import get_logger
from src.integrations.base_client import BaseAPIClient
from src.integrations.claims import sentence_at
from src.integrations.http import check_response, json_client, parse_json
from src.integrations.pricing import modelled_cost
from src.integrations.schemas import Citation, CitationClaim, Engine, EngineAnswer

__all__ = ["GEMINI_REDIRECT_HOST", "GeminiSearchClient"]

_logger = get_logger("integrations.gemini_search")

GEMINI_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


class GeminiSearchClient(BaseAPIClient):
    """Asks a prompt with Google Search grounding and returns the sources."""

    service_name: ClassVar[str] = "gemini"
    rate_limit_key: ClassVar[str] = "gemini.generate_content"
    requests_per_minute: ClassVar[int] = 60
    base_url: ClassVar[str] = "https://generativelanguage.googleapis.com/v1beta/models"

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
        """Create the HTTP session with the API-key header."""
        key = self._settings.require("gemini_api_key")
        self._http = json_client(
            self._settings.default_timeout_s,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            transport=self._transport,
        )

    def ask(self, prompt: str) -> EngineAnswer:
        """Run `prompt` with search grounding and normalise the answer."""
        if self._http is None:
            self.authenticate()
        model = self._settings.gemini_model
        url = f"{self.base_url}/{model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
        }
        started = time.perf_counter()

        def request() -> dict[str, Any]:
            response = self._http.post(url, json=body)  # type: ignore[union-attr]
            check_response(self.service_name, response)
            return parse_json(self.service_name, response)

        payload = self.call(
            "generate_content",
            request,
            estimated_cost_usd=self._settings.cost_gemini_grounded_call_usd,
        )
        answer = _parse_response(payload, prompt=prompt, model=model)
        answer.latency_ms = (time.perf_counter() - started) * 1000
        raw_meta = payload.get("usageMetadata")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        tokens = {
            "input_tokens": _count(meta.get("promptTokenCount")),
            "output_tokens": _count(meta.get("candidatesTokenCount")),
            "cached_tokens": _count(meta.get("cachedContentTokenCount")),
        }
        searches = 1 if answer.web_triggered else 0
        self.note_usage(
            model=answer.model,
            search_calls=searches,
            modelled_cost_usd=modelled_cost(
                "gemini", answer.model, search_calls=searches, **tokens
            ),
            **tokens,
        )
        _logger.info(
            "gemini_answer",
            extra={
                "web_triggered": answer.web_triggered,
                "citations": len(answer.citations),
                "unresolved": sum(not c.resolved for c in answer.citations),
            },
        )
        return answer


def _parse_response(payload: dict[str, Any], *, prompt: str, model: str) -> EngineAnswer:
    """Extract text and grounding sources, ordered by first supporting segment."""
    candidates = payload.get("candidates")
    candidate: dict[str, Any] = (
        candidates[0]
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict)
        else {}
    )

    parts = (candidate.get("content") or {}).get("parts") or []
    text = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("text"))

    grounding = candidate.get("groundingMetadata") or {}
    queries = grounding.get("webSearchQueries") or []
    chunks = grounding.get("groundingChunks") or []
    supports = grounding.get("groundingSupports") or []

    # Order chunks by the position of the first answer segment they support so
    # "rank" means "first source the reader meets", consistent with other engines.
    first_use: dict[int, int] = {}
    for support in supports:
        if not isinstance(support, dict):
            continue
        start = int((support.get("segment") or {}).get("startIndex", 0))
        for idx in support.get("groundingChunkIndices") or []:
            if isinstance(idx, int) and (idx not in first_use or start < first_use[idx]):
                first_use[idx] = start
    order = sorted(range(len(chunks)), key=lambda i: (first_use.get(i, 1 << 30), i))

    citations: list[Citation] = []
    seen: set[str] = set()
    for idx in order:
        chunk = chunks[idx]
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if not isinstance(web, dict):
            continue
        uri = str(web.get("uri") or "")
        if not uri or uri in seen:
            continue
        seen.add(uri)
        citation = _citation_from_chunk(uri, web, position=len(citations) + 1)
        if citation is not None:
            citations.append(citation)

    claims: list[CitationClaim] = []
    claimed: set[tuple[str, int]] = set()
    for support in supports:
        if not isinstance(support, dict):
            continue
        raw_segment = support.get("segment")
        segment: dict[str, Any] = raw_segment if isinstance(raw_segment, dict) else {}
        start = int(segment.get("startIndex", 0) or 0)
        end = int(segment.get("endIndex", start) or start)
        sentence = str(segment.get("text") or "").strip() or sentence_at(text, start)[0]
        for idx in support.get("groundingChunkIndices") or []:
            if not isinstance(idx, int) or idx >= len(chunks) or not isinstance(chunks[idx], dict):
                continue
            web = chunks[idx].get("web")
            uri = str(web.get("uri") or "") if isinstance(web, dict) else ""
            if uri and sentence and (uri, start) not in claimed:
                claimed.add((uri, start))
                claims.append(CitationClaim(url=uri, sentence=sentence[:600], start=start, end=end))

    return EngineAnswer(
        engine=Engine.GEMINI,
        model=str(payload.get("modelVersion") or model),
        prompt=prompt,
        answer_text=text.strip(),
        web_triggered=bool(queries),
        citations=citations,
        search_queries=[q.strip() for q in queries if isinstance(q, str) and q.strip()],
        citation_claims=claims,
        response_id=payload.get("responseId"),
    )


def _count(value: Any) -> int | None:
    """Non-negative int from a JSON number, else None."""
    return int(value) if isinstance(value, int | float) and value >= 0 else None


def _citation_from_chunk(uri: str, web: dict[str, Any], *, position: int) -> Citation | None:
    """Build a citation, preferring the API's `domain` over the redirect host."""
    title = str(web.get("title") or "") or None
    explicit_domain = normalize_domain(str(web.get("domain") or ""))
    if explicit_domain:
        return Citation(
            url=uri, domain=registrable_domain(explicit_domain), title=title, position=position
        )

    host = normalize_domain(uri)
    if host and host != GEMINI_REDIRECT_HOST:
        return Citation(url=uri, domain=registrable_domain(host), title=title, position=position)

    # Older payloads put the bare domain in `title` ("gep.com"). Use it if it
    # looks like one; otherwise record the redirect host and flag it unresolved.
    if title and "." in title and " " not in title:
        return Citation(
            url=uri,
            domain=registrable_domain(title),
            title=title,
            position=position,
            resolved=False,
        )
    if host:
        return Citation(url=uri, domain=host, title=title, position=position, resolved=False)
    return None
