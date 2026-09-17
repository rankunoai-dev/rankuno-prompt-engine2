"""Semrush Analytics API v3 connector — seed keyword demand.

Semrush is the *only* source of search-volume grounding in the pipeline, and it
bills per returned row. Two properties of the API shape this connector:

* Every call sends `display_limit` explicitly. Unset, Semrush returns up to
  10,000 rows and bills for all of them.
* Errors arrive as HTTP 200 with a text body such as `ERROR 50 :: NOTHING FOUND`.
  Treating "200 OK" as success — which the Phase 1 validation did — would parse
  an error message as a keyword.

Unit accounting is enforced per run via `SEMRUSH_MAX_UNITS_PER_RUN`. Published
rates (units per row) are declared next to each report so a reviewer can check
them against the vendor's price list in one glance.
"""

from __future__ import annotations

import csv
from typing import ClassVar, Final

import httpx

from src.core.config import Settings
from src.core.errors import IntegrationError, UpstreamClientError
from src.core.logger import get_logger
from src.integrations.base_client import BaseAPIClient
from src.integrations.http import check_response, json_client
from src.integrations.schemas import KeywordRecord, KeywordSource

__all__ = ["SemrushClient"]

_logger = get_logger("integrations.semrush")

# Units billed per returned row, per Semrush's Analytics API price list.
_UNITS_PER_ROW: Final[dict[KeywordSource, int]] = {
    KeywordSource.PHRASE_QUESTIONS: 40,
    KeywordSource.PHRASE_ALL: 10,
    KeywordSource.PHRASE_RELATED: 40,
}
_REPORT_TYPE: Final[dict[KeywordSource, str]] = {
    KeywordSource.PHRASE_QUESTIONS: "phrase_questions",
    KeywordSource.PHRASE_ALL: "phrase_all",
    KeywordSource.PHRASE_RELATED: "phrase_related",
}
_EXPORT_COLUMNS = "Ph,Nq,Cp,Co,Nr"
_NOTHING_FOUND = "ERROR 50 :: NOTHING FOUND"


class SemrushClient(BaseAPIClient):
    """Keyword-report connector for the Semrush Analytics API v3."""

    service_name: ClassVar[str] = "semrush"
    rate_limit_key: ClassVar[str] = "semrush.api"
    requests_per_minute: ClassVar[int] = 60
    base_url: ClassVar[str] = "https://api.semrush.com/"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a client.

        Args:
            settings: Configuration override, primarily for tests.
            transport: httpx transport override so tests never touch the network.
        """
        super().__init__(settings)
        self._api_key: str | None = None
        self._units_consumed = 0
        self._http = json_client(self._settings.default_timeout_s, transport=transport)

    @property
    def units_consumed(self) -> int:
        """Semrush API units billed by this client instance so far."""
        return self._units_consumed

    def authenticate(self) -> None:
        """Load the API key. Fails with an actionable message when unset."""
        self._api_key = self._settings.require("semrush_api_key").strip()

    def estimate_units(self, source: KeywordSource, rows: int) -> int:
        """Units a report of `rows` lines will bill."""
        return _UNITS_PER_ROW[source] * rows

    def phrase_questions(
        self, phrase: str, *, display_limit: int | None = None
    ) -> list[KeywordRecord]:
        """Question-form queries containing `phrase`, with volume and CPC."""
        return self._keyword_report(KeywordSource.PHRASE_QUESTIONS, phrase, display_limit)

    def phrase_all(self, phrase: str) -> KeywordRecord | None:
        """Volume, CPC and competition for `phrase` itself, or None if unknown."""
        rows = self._keyword_report(KeywordSource.PHRASE_ALL, phrase, 1)
        return rows[0] if rows else None

    def phrase_related(
        self, phrase: str, *, display_limit: int | None = None
    ) -> list[KeywordRecord]:
        """Semantically related keywords for `phrase`."""
        return self._keyword_report(KeywordSource.PHRASE_RELATED, phrase, display_limit)

    # -- internals ---------------------------------------------------------

    def _keyword_report(
        self, source: KeywordSource, phrase: str, display_limit: int | None
    ) -> list[KeywordRecord]:
        """Run one keyword report under the unit budget and parse its CSV."""
        if self._api_key is None:
            self.authenticate()
        limit = self._settings.semrush_display_limit if display_limit is None else display_limit
        if limit < 1:
            msg = "display_limit must be at least 1."
            raise ValueError(msg)

        projected = self._units_consumed + self.estimate_units(source, limit)
        ceiling = self._settings.semrush_max_units_per_run
        if projected > ceiling:
            raise IntegrationError(
                self.service_name,
                f"Refusing {_REPORT_TYPE[source]} for '{phrase}': would reach {projected} units "
                f"against a per-run ceiling of {ceiling}.",
            )

        params = {
            "type": _REPORT_TYPE[source],
            "key": self._api_key,
            "phrase": phrase,
            "database": self._settings.semrush_database,
            "export_columns": _EXPORT_COLUMNS,
            "display_limit": str(limit),
        }

        def request() -> str:
            response = self._http.get(self.base_url, params=params)
            check_response(self.service_name, response)
            return response.text

        unit_usd = self._settings.cost_semrush_unit_usd
        body = self.call(
            _REPORT_TYPE[source],
            request,
            estimated_cost_usd=self.estimate_units(source, limit) * unit_usd,
        )
        records = self._parse(source, body)
        billed = self.estimate_units(source, len(records))
        self._units_consumed += billed
        self.note_usage(units=billed, modelled_cost_usd=round(billed * unit_usd, 6))
        _logger.info(
            "semrush_report",
            extra={"report": _REPORT_TYPE[source], "rows": len(records), "units": billed},
        )
        return records

    def _parse(self, source: KeywordSource, body: str) -> list[KeywordRecord]:
        """Turn Semrush's semicolon CSV (or in-band error) into records."""
        text = body.strip()
        if not text:
            return []
        if text.startswith("ERROR"):
            if text.startswith(_NOTHING_FOUND):
                return []
            raise UpstreamClientError(self.service_name, 200, text[:200])

        lines = text.splitlines()
        records: list[KeywordRecord] = []
        for row in csv.reader(lines[1:], delimiter=";"):
            if len(row) < 5 or not row[0].strip():
                continue
            records.append(
                KeywordRecord(
                    keyword=row[0].strip(),
                    search_volume=_to_int(row[1]),
                    cpc=_to_float(row[2]),
                    competition=min(1.0, _to_float(row[3])),
                    num_results=_to_int(row[4]),
                    database=self._settings.semrush_database,
                    source=source,
                )
            )
        return records


def _to_int(raw: str) -> int:
    """Parse a Semrush numeric cell, treating blanks as zero."""
    try:
        return int(float(raw.strip() or 0))
    except ValueError:
        return 0


def _to_float(raw: str) -> float:
    """Parse a Semrush decimal cell, treating blanks as zero."""
    try:
        return float(raw.strip() or 0.0)
    except ValueError:
        return 0.0
