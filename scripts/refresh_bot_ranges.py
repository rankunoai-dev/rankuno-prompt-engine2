r"""Refresh the bundled crawler IP ranges (`src/modules/crawler_logs/ranges.json`).

Ten small GETs to the vendors' published lists, through the platform's
connector rules (rate bucket, breaker, ledger row at zero cost, SSRF policy).
Run it when a client's verified count looks wrong; commit the result.

    .venv\Scripts\python.exe scripts\refresh_bot_ranges.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import Settings, get_settings  # noqa: E402
from src.core.url_safety import UrlSafetyPolicy  # noqa: E402
from src.integrations.base_client import BaseAPIClient  # noqa: E402
from src.integrations.http import PinnedTransport, check_response, json_client  # noqa: E402
from src.integrations.usage import usage_context  # noqa: E402
from src.modules.crawler_logs.ranges import BUNDLED_PATH  # noqa: E402

SOURCES: dict[str, list[str]] = {
    "openai": [
        "https://openai.com/gptbot.json",
        "https://openai.com/searchbot.json",
        "https://openai.com/chatgpt-user.json",
    ],
    "perplexity": [
        "https://www.perplexity.ai/perplexitybot.json",
        "https://www.perplexity.ai/perplexity-user.json",
    ],
    "google": [
        "https://developers.google.com/static/crawling/ipranges/common-crawlers.json",
        "https://developers.google.com/static/crawling/ipranges/special-crawlers.json",
        "https://developers.google.com/static/crawling/ipranges/user-triggered-fetchers.json",
        "https://developers.google.com/static/crawling/ipranges/user-triggered-fetchers-google.json",
    ],
    "apple": ["https://search.developer.apple.com/applebot.json"],
}
_MAX_HOPS = 3
_REDIRECTS = frozenset({301, 302, 307, 308})


class BotRangeClient(BaseAPIClient):
    """Fetches one published range list. No credential, no cost."""

    service_name: ClassVar[str] = "web.bot_ranges"
    rate_limit_key: ClassVar[str] = "web.bot_ranges"
    requests_per_minute: ClassVar[int] = 30

    def __init__(self, settings: Settings | None = None) -> None:
        """Build the client under the strict SSRF policy."""
        super().__init__(settings)
        self._policy = UrlSafetyPolicy()

    def authenticate(self) -> None:
        """Public files: nothing to do."""
        return

    def fetch(self, url: str) -> dict[str, Any]:
        """One list as a dict with `creationTime` and `prefixes`.

        Vendors move these files (Google's did in 2025); a same-host redirect is
        followed with every hop re-validated, like `RedirectResolver`.
        """
        current = url
        for _ in range(_MAX_HOPS):
            safe = self._policy.validate(current)

            def request(safe: Any = safe) -> httpx.Response:
                with json_client(
                    self._settings.default_timeout_s, transport=PinnedTransport(safe)
                ) as client:
                    response = client.get(safe.url, follow_redirects=False)
                if response.status_code not in _REDIRECTS:
                    check_response(self.service_name, response)
                return response

            response = self.call("ranges", request)
            if response.status_code in _REDIRECTS:
                current = str(response.headers.get("location", ""))
                if current.startswith("/"):
                    current = f"{safe.scheme}://{safe.host}{current}"
                continue
            payload = response.json()
            if not isinstance(payload, dict):
                raise httpx.HTTPError("range list is not a JSON object")
            return payload
        raise httpx.HTTPError(f"too many redirects for {url}")


def main() -> int:
    """Rewrite the snapshot; print one line per list."""
    client = BotRangeClient(get_settings())
    document: dict[str, Any] = {"fetched_at": datetime.now(UTC).isoformat(), "vendors": {}}
    for vendor, urls in SOURCES.items():
        sources = []
        for url in urls:
            with usage_context(source="bot_ranges"):
                payload = client.fetch(url)
            prefixes = [
                p.get("ipv4Prefix") or p.get("ipv6Prefix")
                for p in payload.get("prefixes", [])
                if isinstance(p, dict)
            ]
            prefixes = [p for p in prefixes if p]
            sources.append(
                {"url": url, "creationTime": payload.get("creationTime"), "prefixes": prefixes}
            )
            print(f"{vendor:11} {len(prefixes):4d} prefixes  {url}")
        document["vendors"][vendor] = {"sources": sources}
    BUNDLED_PATH.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {BUNDLED_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
