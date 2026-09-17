"""SerpApi connector — Google AI Overviews, organic results and People Also Ask.

Google frequently loads the AI Overview asynchronously. In that case the first
SerpApi response carries only `ai_overview.page_token`, which expires within a
minute, and the overview must be fetched with a second request against the
`google_ai_overview` engine. This connector chains the two calls immediately so
the caller never sees a half-empty overview.

One SERP call yields two measurements: the AI Overview (an `EngineAnswer`) and
the classic organic ranking (`SerpSnapshot.organic_results`). `search_and_ask()`
returns both so the pipeline pays for the call once.

Locale (`gl`, `hl`, `location`, `device`) is fixed from settings for every call:
both AI Overviews and organic ranks vary by location and device, and a tracker
that drifts between them measures noise, not movement.

Web-trigger definition: an AI Overview was rendered for the query. "No AI
Overview" is a legitimate, recorded outcome distinct from "AI Overview shown but
client not cited".
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

import httpx

from src.core.config import Settings
from src.core.domains import registrable_domain
from src.core.logger import get_logger
from src.integrations.base_client import BaseAPIClient
from src.integrations.http import check_response, json_client, parse_json
from src.integrations.schemas import (
    Citation,
    CitationClaim,
    Engine,
    EngineAnswer,
    OrganicResult,
    SerpSnapshot,
)

__all__ = ["SerpApiClient"]

_logger = get_logger("integrations.serp_api")
_MIN_TIMEOUT_S = 60.0  # the AI Overview follow-up regularly takes over 30 s


class SerpApiClient(BaseAPIClient):
    """Fetches a Google SERP, its AI Overview and its organic ranking for a query."""

    service_name: ClassVar[str] = "serpapi"
    rate_limit_key: ClassVar[str] = "serpapi.search"
    requests_per_minute: ClassVar[int] = 60
    base_url: ClassVar[str] = "https://serpapi.com/search.json"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a client; see `BaseAPIClient`."""
        super().__init__(settings)
        self._api_key: str | None = None
        self._http = json_client(
            max(self._settings.default_timeout_s, _MIN_TIMEOUT_S), transport=transport
        )

    def authenticate(self) -> None:
        """Load the API key."""
        self._api_key = self._settings.require("serp_api_key")

    def search(self, query: str) -> SerpSnapshot:
        """Run a Google search and return the SERP features we track."""
        if self._api_key is None:
            self.authenticate()
        params = {
            "engine": "google",
            "q": query,
            "gl": self._settings.serp_gl,
            "hl": self._settings.serp_hl,
            "location": self._settings.serp_location,
            "device": self._settings.serp_device,
            "api_key": self._api_key,
        }
        payload = self.call(
            "google_search",
            lambda: self._get(params),
            estimated_cost_usd=self._settings.cost_serpapi_call_usd,
        )
        self._note_search()

        overview = payload.get("ai_overview")
        if (
            isinstance(overview, dict)
            and overview.get("page_token")
            and "text_blocks" not in overview
        ):
            overview = self._fetch_overview(str(overview["page_token"]))
        snapshot = _parse_snapshot(
            query,
            payload,
            overview if isinstance(overview, dict) else None,
            device=self._settings.serp_device,
        )
        _logger.info(
            "serp_snapshot",
            extra={
                "aio": snapshot.ai_overview_present,
                "references": len(snapshot.ai_overview_references),
                "organic": len(snapshot.organic_results),
                "paa": len(snapshot.paa_questions),
            },
        )
        return snapshot

    def search_and_ask(self, prompt: str) -> tuple[SerpSnapshot, EngineAnswer]:
        """One SERP call, two views: the raw snapshot and the AI Overview as an engine answer."""
        started = time.perf_counter()
        snapshot = self.search(prompt)
        answer = EngineAnswer(
            engine=Engine.GOOGLE_AI_OVERVIEW,
            model="google_ai_overview",
            prompt=prompt,
            answer_text=snapshot.ai_overview_text,
            web_triggered=snapshot.ai_overview_present,
            citations=snapshot.ai_overview_references,
            citation_claims=snapshot.ai_overview_claims,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return snapshot, answer

    def ask(self, prompt: str) -> EngineAnswer:
        """Engine-shaped view of the AI Overview for `prompt`."""
        return self.search_and_ask(prompt)[1]

    # -- internals ---------------------------------------------------------

    def _get(self, params: dict[str, str | None]) -> dict[str, Any]:
        """One GET against SerpApi with status and JSON checks."""
        response = self._http.get(self.base_url, params=params)
        check_response(self.service_name, response)
        payload = parse_json(self.service_name, response)
        error = payload.get("error")
        if isinstance(error, str) and error and "hasn't returned any results" not in error:
            _logger.warning("serpapi_reported_error", extra={"error": error[:200]})
        return payload

    def _fetch_overview(self, page_token: str) -> dict[str, Any] | None:
        """Second request for an asynchronously rendered AI Overview."""
        params = {
            "engine": "google_ai_overview",
            "page_token": page_token,
            "api_key": self._api_key,
        }
        payload = self.call(
            "google_ai_overview",
            lambda: self._get(params),
            estimated_cost_usd=self._settings.cost_serpapi_call_usd,
        )
        self._note_search()
        overview = payload.get("ai_overview")
        return overview if isinstance(overview, dict) else None

    def _note_search(self) -> None:
        """Each SerpApi request is one plan search; its price is the configured per-search rate."""
        self.note_usage(
            model="google",
            search_calls=1,
            modelled_cost_usd=self._settings.cost_serpapi_call_usd,
        )


def _parse_snapshot(
    query: str, payload: dict[str, Any], overview: dict[str, Any] | None, *, device: str
) -> SerpSnapshot:
    """Normalise the parts of a SerpApi payload the tracker records."""
    references: list[Citation] = []
    text_blocks: list[str] = []
    inline_links: list[tuple[str, str, str]] = []  # (url, anchor, block text), reading order
    if overview:
        for block in overview.get("text_blocks") or []:
            if not isinstance(block, dict):
                continue
            if block.get("snippet"):
                text_blocks.append(str(block["snippet"]))
            inline_links.extend(_snippet_links(block))
            for item in block.get("list") or []:
                if isinstance(item, dict) and item.get("snippet"):
                    text_blocks.append(str(item["snippet"]))
                if isinstance(item, dict):
                    inline_links.extend(_snippet_links(item))
        seen: set[str] = set()
        for ref in overview.get("references") or []:
            if not isinstance(ref, dict):
                continue
            link = str(ref.get("link") or "")
            domain = registrable_domain(link)
            if not link or not domain or link in seen:
                continue
            seen.add(link)
            references.append(
                Citation(
                    url=link,
                    domain=domain,
                    title=str(ref.get("title") or "") or None,
                    position=len(references) + 1,
                )
            )

    paa = [
        str(q["question"])
        for q in payload.get("related_questions") or []
        if isinstance(q, dict) and q.get("question")
    ]
    # Google now embeds sources as links inside the text blocks and often
    # omits the separate `references` list; those inline links are the
    # citations the reader actually sees, in reading order.
    claims: list[CitationClaim] = []
    for link, anchor, block_text in inline_links:
        domain = registrable_domain(link)
        if not domain:
            continue
        if block_text:
            claims.append(CitationClaim(url=link, sentence=block_text[:600], start=0, end=0))
        if link in seen:
            continue
        seen.add(link)
        references.append(
            Citation(url=link, domain=domain, title=anchor or None, position=len(references) + 1)
        )
    related = [
        str(r["query"])
        for r in payload.get("related_searches") or []
        if isinstance(r, dict) and r.get("query")
    ]
    present = bool(overview) and bool(text_blocks or references)
    return SerpSnapshot(
        query=query,
        device=device,
        ai_overview_present=present,
        ai_overview_text="\n".join(text_blocks).strip(),
        ai_overview_references=references,
        paa_questions=paa,
        related_searches=related,
        ai_overview_claims=claims,
        organic_results=_parse_organic(payload.get("organic_results")),
    )


def _parse_organic(raw: Any) -> list[OrganicResult]:
    """Organic results with Google's own position, falling back to list order."""
    results: list[OrganicResult] = []
    if not isinstance(raw, list):
        return results
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "")
        domain = registrable_domain(link)
        if not link or not domain:
            continue
        position = item.get("position")
        results.append(
            OrganicResult(
                position=position if isinstance(position, int) and position >= 1 else index,
                url=link,
                domain=domain,
                title=str(item.get("title") or "") or None,
                snippet=str(item.get("snippet") or "")[:1000] or None,
            )
        )
    return results


def _snippet_links(block: dict[str, Any]) -> list[tuple[str, str, str]]:
    """`(url, anchor text, block text)` for each inline source link in a block or list item."""
    out: list[tuple[str, str, str]] = []
    block_text = str(block.get("snippet") or "")
    for link in block.get("snippet_links") or []:
        if isinstance(link, dict) and isinstance(link.get("link"), str) and link["link"]:
            out.append((link["link"], str(link.get("text") or ""), block_text))
    return out
