"""robots.txt evaluation (RFC 9309) for outbound fetches.

Design stance: this module is pure. It never performs network I/O — the
connector layer fetches `robots.txt` (through `BaseAPIClient`, like every other
request) and hands the text here. Keeping parsing separate from fetching keeps
`core` free of HTTP dependencies and makes the rules trivially testable.

The parser is the standard library's `RobotFileParser`, which implements the
longest-match precedence and `Crawl-delay` extension we need. The wrapper exists
to give it a strict, typed, logged surface and a fail-closed default when a
site's robots file could not be retrieved.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from src.core.logger import get_logger
from src.core.rate_limiter import TokenBucket

__all__ = ["DEFAULT_USER_AGENT", "RobotsRules"]

_logger = get_logger("core.robots")

DEFAULT_USER_AGENT = "RankUnoPromptTracker/0.1 (+https://rankuno.com)"


class RobotsRules:
    """Fetch permissions for one host, derived from its robots.txt."""

    def __init__(
        self, host: str, rules_text: str | None, *, user_agent: str = DEFAULT_USER_AGENT
    ) -> None:
        """Parse robots rules for `host`.

        Args:
            host: Hostname the rules belong to. Used for logging and matching.
            rules_text: Body of `robots.txt`. `None` means the file could not be
                fetched; the rules then deny everything (fail closed). An empty
                string means the file exists and permits everything.
            user_agent: Product token used when matching groups.
        """
        self.host = host.lower()
        self.user_agent = user_agent
        self._unavailable = rules_text is None
        self._parser = RobotFileParser()
        if rules_text is not None:
            self._parser.parse(rules_text.splitlines())

    @classmethod
    def permissive(cls, host: str) -> RobotsRules:
        """Rules for a host that publishes no robots.txt (HTTP 404): allow all."""
        return cls(host, "")

    def can_fetch(self, url_or_path: str) -> bool:
        """Return True if the policy allows fetching `url_or_path`.

        Args:
            url_or_path: Absolute URL or a path beginning with `/`. Absolute URLs
                on a different host are refused — rules never transfer across
                hosts.
        """
        if self._unavailable:
            _logger.warning("robots_unavailable_denying", extra={"host": self.host})
            return False

        path = url_or_path
        if "://" in url_or_path:
            parts = urlsplit(url_or_path)
            if (parts.hostname or "").lower() != self.host:
                return False
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"

        allowed = self._parser.can_fetch(self.user_agent, path)
        if not allowed:
            _logger.info("robots_disallowed", extra={"host": self.host, "path": path})
        return allowed

    def crawl_delay_s(self) -> float | None:
        """Declared `Crawl-delay` for our user agent, in seconds, if any."""
        if self._unavailable:
            return None
        delay = self._parser.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None

    def bucket(self, *, default_requests_per_minute: int = 30) -> TokenBucket:
        """Build a per-host token bucket honouring the declared crawl delay.

        A host that declares `Crawl-delay: 10` gets one request per ten seconds;
        one that declares nothing gets the conservative default.
        """
        delay = self.crawl_delay_s()
        if delay and delay > 0:
            return TokenBucket.from_crawl_delay(f"robots.{self.host}", delay)
        return TokenBucket.per_minute(f"robots.{self.host}", default_requests_per_minute)
