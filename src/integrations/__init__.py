"""External API connectors.

Every outbound integration lives here and subclasses
`src.integrations.base_client.BaseAPIClient`. Domain modules never call an
external API directly — they depend on a connector, which is what makes quota,
retry, credential handling and mocking uniform.

Connectors:

* `semrush.py` — keyword demand (phrase_questions, phrase_all, phrase_related)
* `openai_search.py` — OpenAI Responses API + web_search ("ChatGPT Search")
* `perplexity.py` — Perplexity sonar models
* `gemini_search.py` — Gemini with Google Search grounding
* `serp_api.py` — SerpApi Google SERP + AI Overview
* `url_resolver.py` — redirect resolution under SSRF and robots policies
"""

from src.integrations.base_client import BaseAPIClient
from src.integrations.gemini_search import GeminiSearchClient
from src.integrations.openai_search import OpenAISearchClient
from src.integrations.perplexity import PerplexityClient
from src.integrations.schemas import (
    Citation,
    Engine,
    EngineAnswer,
    KeywordRecord,
    KeywordSource,
    SerpSnapshot,
)
from src.integrations.semrush import SemrushClient
from src.integrations.serp_api import SerpApiClient
from src.integrations.url_resolver import RedirectResolver, ResolvedUrl

__all__ = [
    "BaseAPIClient",
    "Citation",
    "Engine",
    "EngineAnswer",
    "GeminiSearchClient",
    "KeywordRecord",
    "KeywordSource",
    "OpenAISearchClient",
    "PerplexityClient",
    "RedirectResolver",
    "ResolvedUrl",
    "SemrushClient",
    "SerpApiClient",
    "SerpSnapshot",
]
