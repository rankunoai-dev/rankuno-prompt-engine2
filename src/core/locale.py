"""Where a crawl is executed from: country, language and city.

Answer engines localise. The same prompt asked from London and from Mumbai
returns different sources, so a tracker that reports one number has to say
which market that number is for. This module holds the value object; the
connectors translate it into each vendor's own parameter.

Support is uneven and the tracker states it rather than hiding it
(`Engine.honours_locale` in `src/integrations/schemas.py`):

* Google AI Overview (SerpApi): `gl`, `hl` and a canonical `location` string,
  city-level. Full support.
* ChatGPT Search (OpenAI Responses): `user_location` on the `web_search` tool,
  a two-letter country with free-text city and region and an IANA timezone.
* Perplexity (Agent API): `user_location` on the `web_search` tool, with
  country, region and city. Perplexity's own docs warn that the filter is a
  hint, not a guarantee.
* Gemini: the Developer API's `google_search` tool takes an empty object and
  has no location field at all. A Gemini sample follows the billing account's
  country whatever this locale says.

Locale is frozen once a project has crawled (ADR 0023): changing it mid-history
would mix two populations into one trend line.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field, field_validator

from src.core.schemas import StrictModel

__all__ = ["Locale"]

_MAX_TEXT: Final = 80


class Locale(StrictModel):
    """The market a crawl is executed from."""

    country: str = Field(
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2, e.g. 'US'. Sent to every engine that accepts one.",
    )
    language: str = Field(
        default="en",
        min_length=2,
        max_length=5,
        description="ISO 639-1, optionally with a region ('en', 'en-GB'). SerpApi `hl`.",
    )
    city: str | None = Field(default=None, max_length=_MAX_TEXT)
    region: str | None = Field(
        default=None, max_length=_MAX_TEXT, description="State or region, free text."
    )
    serp_location: str | None = Field(
        default=None,
        max_length=_MAX_TEXT * 2,
        description=(
            "Canonical SerpApi location string, e.g. 'Mumbai, Maharashtra, India'. "
            "SerpApi rejects names outside its own database, so this is kept verbatim "
            "and never derived from `city`."
        ),
    )
    timezone: str | None = Field(
        default=None,
        max_length=_MAX_TEXT,
        description="IANA timezone, e.g. 'Europe/London'. Sent to ChatGPT Search only.",
    )

    @field_validator("country")
    @classmethod
    def _upper(cls, value: str) -> str:
        """ISO country codes are upper case; vendors differ on what they accept."""
        return value.strip().upper()

    @field_validator("language")
    @classmethod
    def _lower(cls, value: str) -> str:
        """SerpApi's `hl` is lower case."""
        return value.strip().lower()

    @field_validator("city", "region", "serp_location", "timezone")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty box in the UI means 'not set', never an empty parameter."""
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @property
    def label(self) -> str:
        """Short human label for a badge: 'Mumbai, IN · en' or 'US · en'."""
        place = self.city or self.serp_location or self.country
        if self.city and self.city != self.country:
            place = f"{self.city}, {self.country}"
        return f"{place} · {self.language}"

    def openai_user_location(self) -> dict[str, str]:
        """`user_location` for the OpenAI Responses `web_search` tool."""
        out = {"type": "approximate", "country": self.country}
        if self.city:
            out["city"] = self.city
        if self.region:
            out["region"] = self.region
        if self.timezone:
            out["timezone"] = self.timezone
        return out

    def perplexity_user_location(self) -> dict[str, str]:
        """`user_location` for the Perplexity Agent API `web_search` tool.

        Perplexity takes latitude and longitude too, but only alongside a
        country; the tracker does not collect coordinates, so it sends none.
        """
        out = {"country": self.country}
        if self.city:
            out["city"] = self.city
        if self.region:
            out["region"] = self.region
        return out
