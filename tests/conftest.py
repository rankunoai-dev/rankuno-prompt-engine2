"""Shared pytest fixtures.

Two invariants this file exists to protect:

1. Tests never read the developer's real `.env`. Every test gets an explicit,
   hermetic `Settings` object.
2. Tests never touch a real external service. There is no network fixture here
   on purpose - connectors are mocked at the `BaseAPIClient` boundary.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from src.core.config import Environment, Settings, reset_settings_cache
from src.core.guardrails import AutoApproveProvider, GuardrailEngine
from src.core.rate_limiter import CostLedger
from src.core.registry import registry
from src.core.schemas import RiskClass, ToolMetadata


@pytest.fixture(autouse=True)
def _isolate_settings_cache() -> Iterator[None]:
    """Clear the settings singleton around every test."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """Keep tool registrations from leaking between tests."""
    registry.clear()
    yield
    registry.clear()


@pytest.fixture(autouse=True)
def _isolate_breakers() -> Iterator[None]:
    """Vendor circuit breakers are process-wide; a failure test must not trip them for others."""
    from src.integrations.base_client import _SHARED_BREAKERS

    _SHARED_BREAKERS.reset()
    yield
    _SHARED_BREAKERS.reset()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Hermetic settings: no `.env`, fake credentials, every path under tmp.

    Credentials are placeholders so connectors can authenticate; the transport
    is always mocked, so no real request ever carries them.
    """
    return Settings(
        _env_file=None,
        environment=Environment.DEVELOPMENT,
        audit_log_path=tmp_path / "audit.jsonl",
        tracker_db_path=tmp_path / "tracker.sqlite",
        reports_dir=tmp_path / "reports",
        max_session_spend_usd=1.0,
        default_requests_per_minute=600,
        default_timeout_s=2.0,
        default_max_retries=0,
        # Shared duck-typed stubs count calls without locks; one worker keeps
        # the general suite deterministic. Concurrency has its own tests.
        pipeline_max_workers=1,
        semrush_api_key=SecretStr("test-semrush-key"),
        openai_api_key=SecretStr("test-openai-key"),
        perplexity_api_key=SecretStr("test-perplexity-key"),
        gemini_api_key=SecretStr("test-gemini-key"),
        serp_api_key=SecretStr("test-serpapi-key"),
    )


@pytest.fixture
def permissive_guardrails(settings: Settings) -> GuardrailEngine:
    """Engine that approves HITL prompts. For testing the happy path only."""
    return GuardrailEngine(approval_provider=AutoApproveProvider(), settings=settings)


@pytest.fixture
def strict_guardrails(settings: Settings) -> GuardrailEngine:
    """Engine with the production default: deny unapproved HITL actions."""
    return GuardrailEngine(settings=settings)


@pytest.fixture
def ledger() -> CostLedger:
    """A small, isolated spend ledger."""
    return CostLedger(ceiling_usd=1.0)


@pytest.fixture
def read_metadata() -> ToolMetadata:
    """Metadata for a harmless read-only tool."""
    return ToolMetadata(
        name="test.reader",
        summary="Read-only test tool.",
        risk_class=RiskClass.READ,
    )


@pytest.fixture
def write_metadata() -> ToolMetadata:
    """Metadata for a tool that mutates external state."""
    return ToolMetadata(
        name="test.writer",
        summary="Write test tool.",
        risk_class=RiskClass.WRITE,
    )
