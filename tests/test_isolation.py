"""The test suite itself must never spend money or touch real data."""

from __future__ import annotations

import httpx
import pytest

from src.core.config import Settings, get_settings


def test_a_bare_settings_object_sees_no_real_key_or_real_database():
    s = Settings()  # reads .env, like the report tests that once spent real money
    for field in (
        "openrouter_api_key",
        "anthropic_api_key",
        "openai_api_key",
        "perplexity_api_key",
        "gemini_api_key",
        "serp_api_key",
        "semrush_api_key",
    ):
        value = getattr(s, field)
        assert value is None or value.get_secret_value() == "", field
    assert "isolated" in str(s.tracker_db_path)
    assert "isolated" in str(get_settings().tracker_db_path)


def test_a_real_http_request_is_refused_before_it_leaves_the_machine():
    with pytest.raises(RuntimeError, match="must not reach the network"):
        httpx.get("https://openrouter.ai/api/v1/models")


def test_the_mock_transport_still_works():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204)))
    assert client.get("https://example.test/").status_code == 204
