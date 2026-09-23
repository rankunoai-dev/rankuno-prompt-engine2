"""Verification of a crawler hit against its vendor's published IP ranges.

The snapshot in `ranges.json` is bundled and dated; `scripts/refresh_bot_ranges.py`
refreshes it. Verification is per vendor union, not per list: vendors move
agents between lists, and a strict per-list check would produce spurious
"unverified" counts. Addresses are looked up and forgotten; nothing here
returns or stores one.
"""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from src.modules.crawler_logs.schemas import RangesSnapshot

__all__ = ["BUNDLED_PATH", "BotRanges", "bundled_ranges"]

BUNDLED_PATH: Final = Path(__file__).resolve().parent / "ranges.json"

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class BotRanges:
    """Published crawler address ranges, grouped by vendor."""

    def __init__(self, document: dict[str, Any]) -> None:
        """Index a snapshot document (see `ranges.json`)."""
        self._nets: dict[str, list[Network]] = {}
        self._newest: dict[str, str] = {}
        fetched = document.get("fetched_at")
        self.fetched_at = datetime.fromisoformat(str(fetched)) if fetched else None
        for vendor, entry in dict(document.get("vendors") or {}).items():
            nets: list[Network] = []
            for source in entry.get("sources", []):
                created = str(source.get("creationTime") or "")
                if created > self._newest.get(vendor, ""):
                    self._newest[vendor] = created
                for prefix in source.get("prefixes", []):
                    try:
                        nets.append(ipaddress.ip_network(str(prefix), strict=False))
                    except ValueError:
                        continue
            self._nets[vendor] = nets

    @property
    def vendors(self) -> frozenset[str]:
        """Vendors with at least one range."""
        return frozenset(v for v, nets in self._nets.items() if nets)

    def verify(self, vendor: str, ip: str | None) -> bool | None:
        """True/False when the vendor publishes ranges; None when it does not."""
        nets = self._nets.get(vendor)
        if not nets:
            return None
        if not ip:
            return False
        try:
            address = ipaddress.ip_address(ip.strip())
        except ValueError:
            return False
        return any(address in net for net in nets)

    def vendor_of(self, ip: str | None) -> str | None:
        """Which vendor's ranges contain `ip`, if any — for stealth-crawl counting."""
        if not ip:
            return None
        try:
            address = ipaddress.ip_address(ip.strip())
        except ValueError:
            return None
        for vendor, nets in self._nets.items():
            if any(address in net for net in nets):
                return vendor
        return None

    def snapshot(self) -> RangesSnapshot:
        """When the bundled lists were generated, per vendor."""
        return RangesSnapshot(fetched_at=self.fetched_at, vendors=dict(self._newest))


@lru_cache(maxsize=1)
def bundled_ranges() -> BotRanges:
    """The snapshot shipped with the package (empty if the file is missing)."""
    if not BUNDLED_PATH.exists():
        return BotRanges({})
    return BotRanges(json.loads(BUNDLED_PATH.read_text(encoding="utf-8")))
