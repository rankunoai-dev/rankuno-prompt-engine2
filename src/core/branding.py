"""White-label identity for a client-facing report (ADR 0024).

An agency sends these PDFs to its own clients, so the document carries the
client's mark and the agency's, and nothing of ours. This module holds the
value object and the one piece of logic worth sharing: turning an operator's
hex colour into the small palette the page actually uses.

Two rules are enforced here rather than left to the renderer:

* A logo is referenced by an opaque id, never by a caller-supplied path. The
  renderer resolves the id inside the reports directory, so a crafted
  `logo_id` cannot reach a file elsewhere on the volume.
* Every colour is validated to `#rrggbb`. ReportLab accepts almost anything
  and fails deep inside a draw call otherwise.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import Field, field_validator

from src.core.schemas import StrictModel

__all__ = ["Brand", "LOGO_ID_PATTERN", "contrasting_ink"]

_HEX: Final = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

LOGO_ID_PATTERN: Final = re.compile(r"^[a-f0-9]{16}\.(png|jpg)$")
"""A stored logo's file name: hex id plus a known extension, nothing else."""

_DEFAULT_PRIMARY: Final = "#1f3a5f"
_MAX_TEXT: Final = 120


def _expand(value: str) -> str:
    """Normalise `#abc` to `#aabbcc` and lower-case it."""
    text = value.strip().lower()
    if len(text) == 4:
        return "#" + "".join(ch * 2 for ch in text[1:])
    return text


def contrasting_ink(hex_colour: str) -> str:
    """Black or white, whichever stays readable on `hex_colour`.

    Relative luminance per WCAG; the threshold is the usual 0.5 compromise.
    A client's brand colour can be anything from navy to lime, and the cover
    band prints its title on top of it.
    """
    text = _expand(hex_colour)
    red, green, blue = (int(text[i : i + 2], 16) / 255 for i in (1, 3, 5))
    channels = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (red, green, blue)
    ]
    luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    return "#000000" if luminance > 0.5 else "#ffffff"


class Brand(StrictModel):
    """How one project's reports are dressed."""

    client_name: str | None = Field(
        default=None,
        max_length=_MAX_TEXT,
        description="Name on the cover. Defaults to the project's brand when unset.",
    )
    agency_name: str | None = Field(
        default=None,
        max_length=_MAX_TEXT,
        description="Who prepared the report; printed in the footer of every page.",
    )
    primary_colour: str = Field(
        default=_DEFAULT_PRIMARY,
        description="Hex colour for the cover band, headings and chart series.",
    )
    logo_id: str | None = Field(
        default=None,
        max_length=32,
        description="Stored logo file name; set by the upload endpoint, never by hand.",
    )
    footer_note: str | None = Field(
        default=None,
        max_length=200,
        description="Confidentiality line or similar, printed beside the page number.",
    )
    show_spend: bool = Field(
        default=False,
        description="Include the vendor-cost appendix. Off: a client report should not "
        "show what the agency pays per crawl.",
    )

    @field_validator("primary_colour")
    @classmethod
    def _check_colour(cls, value: str) -> str:
        """Accept `#rgb` or `#rrggbb` only, and store the long lower-case form."""
        if not _HEX.match(value.strip()):
            msg = f"{value!r} is not a hex colour like #1f3a5f."
            raise ValueError(msg)
        return _expand(value)

    @field_validator("logo_id")
    @classmethod
    def _check_logo(cls, value: str | None) -> str | None:
        """Reject anything that is not a name this application itself minted."""
        if value is None:
            return None
        if not LOGO_ID_PATTERN.match(value):
            msg = "logo_id must be a stored logo name; upload the logo instead of setting it."
            raise ValueError(msg)
        return value

    @field_validator("client_name", "agency_name", "footer_note")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty box in the UI means 'not set'."""
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @property
    def ink(self) -> str:
        """Readable text colour on top of `primary_colour`."""
        return contrasting_ink(self.primary_colour)
