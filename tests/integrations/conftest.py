"""Fixtures shared by the connector tests.

`BaseAPIClient` shares one `RateLimiterRegistry` across every instance, so a
package of fast tests can drain a 30-rpm bucket and start blocking; the registry
is reset around each test. `DEFAULT_MAX_RETRIES` is pinned as belt-and-braces so
a developer's `.env` can never make a connector test retry with real backoff.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from src.integrations.base_client import _SHARED_LIMITERS


@pytest.fixture(autouse=True)
def _hermetic_connectors(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DEFAULT_MAX_RETRIES", "0")
    _SHARED_LIMITERS.reset()
    yield
    _SHARED_LIMITERS.reset()
