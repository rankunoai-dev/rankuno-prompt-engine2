"""One live call per engine for one prompt, under a hard spend ceiling (ADR 0011).

Fixtures modelled on vendor docs missed four real connector faults in cycle
0008; this script is the cheap truth test to run after any connector change.
It prints model, web trigger, citations, whether the brand appears in the text,
and the full error text on failure. Nothing is stored.

Usage (from the repository root, with the venv python and a filled `.env`):
    python scripts/live_check.py                      # all four engines, ~$0.10
    python scripts/live_check.py --engine PERPLEXITY  # one engine
    python scripts/live_check.py --prompt "..." --brand GEP --ceiling 0.25
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.core.config import Settings, get_settings  # noqa: E402
from src.core.rate_limiter import CostLedger  # noqa: E402
from src.integrations.gemini_search import GeminiSearchClient  # noqa: E402
from src.integrations.openai_search import OpenAISearchClient  # noqa: E402
from src.integrations.perplexity import PerplexityClient  # noqa: E402
from src.integrations.schemas import Engine, EngineAnswer  # noqa: E402
from src.integrations.serp_api import SerpApiClient  # noqa: E402
from src.integrations.usage import usage_context  # noqa: E402

DEFAULT_PROMPT = "How do I implement GEP procurement software and integrate it with my ERP?"


def _cost(settings: Settings, engine: Engine) -> float:
    return {
        Engine.CHATGPT_SEARCH: settings.cost_openai_search_call_usd,
        Engine.PERPLEXITY: settings.cost_perplexity_call_usd,
        Engine.GEMINI: settings.cost_gemini_grounded_call_usd,
        Engine.GOOGLE_AI_OVERVIEW: settings.cost_serpapi_call_usd,
    }[engine]


def _ask(settings: Settings, engine: Engine, prompt: str) -> EngineAnswer:
    builders: dict[Engine, Callable[[], EngineAnswer]] = {
        Engine.CHATGPT_SEARCH: lambda: OpenAISearchClient(settings).ask(prompt),
        Engine.PERPLEXITY: lambda: PerplexityClient(settings).ask(prompt),
        Engine.GEMINI: lambda: GeminiSearchClient(settings).ask(prompt),
        Engine.GOOGLE_AI_OVERVIEW: lambda: SerpApiClient(settings).search_and_ask(prompt)[1],
    }
    return builders[engine]()


def build_parser() -> argparse.ArgumentParser:
    """CLI schema."""
    parser = argparse.ArgumentParser(description="Live one-call check per engine.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--brand", default="GEP", help="Brand word to look for in the text.")
    parser.add_argument("--engine", action="append", choices=[e.value for e in Engine], default=[])
    parser.add_argument("--ceiling", type=float, default=0.5, help="Max estimated spend, USD.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the checks; exit 1 if any engine failed or returned no sources."""
    args = build_parser().parse_args(argv)
    settings = get_settings()
    ledger = CostLedger(ceiling_usd=args.ceiling)
    engines = [Engine(e) for e in args.engine] or list(Engine)
    failures = 0
    for engine in engines:
        print(f"\n=== {engine.value} ===")
        try:
            ledger.charge(_cost(settings, engine))
            with usage_context(source="live_check", engine=engine.value):
                answer = _ask(settings, engine, args.prompt)
        except Exception as exc:  # noqa: BLE001 - diagnostic: show everything
            failures += 1
            print(f"ERROR {type(exc).__name__}: {str(exc)[:700]}")
            continue
        print(f"model: {answer.model} | latency: {answer.latency_ms:.0f} ms")
        print(f"web_triggered: {answer.web_triggered} | citations: {len(answer.citations)}")
        for citation in answer.citations[:6]:
            print(f"  #{citation.position} {citation.domain}  {citation.url[:90]}")
        print(f"brand in text: {args.brand.lower() in answer.answer_text.lower()}")
        print(
            f"search queries: {len(answer.search_queries)} {answer.search_queries[:4]} | "
            f"claims: {len(answer.citation_claims)} | snippets: {len(answer.source_snippets)}"
        )
        for claim in answer.citation_claims[:3]:
            print(f"  claim -> {claim.url[:60]}: {claim.sentence[:110]!r}")
        print(f"answer: {answer.answer_text[:180]!r}")
        if not answer.citations:
            failures += 1
    print(f"\nestimated spend: ${ledger.spent_usd:.2f} (ceiling ${args.ceiling:.2f})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
