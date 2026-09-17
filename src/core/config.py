"""Typed application configuration loaded from the environment.

Rules enforced here:

* No module anywhere else may read `os.environ` directly. Everything goes
  through `get_settings()` so that configuration is typed, validated once, and
  greppable.
* Secrets are held as `SecretStr` so they cannot be accidentally printed or
  serialised into an audit log.
* Production refuses to boot with guardrails disabled.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.errors import ConfigurationError

__all__ = ["Environment", "Settings", "get_settings", "reset_settings_cache"]

REPO_ROOT = Path(__file__).resolve().parents[2]


class Environment(StrEnum):
    """Deployment target. Governs how strict the guardrails are."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """All runtime configuration for the platform.

    Field names map to upper-cased environment variables (see `.env.example`).
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application -------------------------------------------------------
    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    log_format: str = Field(default="json", pattern="^(json|text)$")
    audit_log_path: Path = REPO_ROOT / "logs" / "audit.jsonl"

    # -- Guardrails --------------------------------------------------------
    guardrails_enabled: bool = Field(
        default=True,
        description="Master switch. Disabling is permitted in development only.",
    )
    require_approval_for_writes: bool = True
    require_approval_for_spend: bool = True
    max_session_spend_usd: float = Field(
        default=5.0,
        ge=0.0,
        description="Hard ceiling on cumulative spend for one process.",
    )
    unattended_spend_cap_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Per-action cap for BudgetedApprovalProvider. Zero (the default) "
        "means unattended runs cannot spend at all.",
    )

    # -- Rate limiting -----------------------------------------------------
    default_requests_per_minute: int = Field(default=60, gt=0)
    default_max_retries: int = Field(default=3, ge=0, le=10)
    default_timeout_s: float = Field(default=30.0, gt=0.0)

    # -- Circuit breaker (per vendor) ---------------------------------------
    circuit_failure_threshold: int = Field(
        default=5, ge=1, description="Consecutive transient failures that open a vendor breaker."
    )
    circuit_cooldown_s: float = Field(
        default=120.0, gt=0.0, description="Seconds an open breaker waits before one trial call."
    )

    # -- LLM providers -----------------------------------------------------
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    perplexity_api_key: SecretStr | None = None

    # -- AI search engines (prompt tracker) --------------------------------
    openai_search_model: str = Field(
        default="gpt-4o-mini",
        description="Model used with the Responses API `web_search` tool.",
    )
    perplexity_model: str = Field(
        default="perplexity/sonar",
        description="Perplexity's own model on the Agent API. Third-party models routed "
        "through Perplexity are deliberately not used: they would not measure Perplexity. "
        "Retired Sonar chat names (sonar, sonar-pro) are mapped to perplexity/sonar.",
    )
    gemini_model: str = Field(default="gemini-3.6-flash")
    serp_gl: str = Field(default="us", min_length=2, max_length=2)
    serp_hl: str = Field(default="en", min_length=2, max_length=5)
    serp_location: str = Field(default="United States")
    serp_device: str = Field(
        default="desktop",
        pattern="^(desktop|mobile|tablet)$",
        description="Organic rankings differ by device; fixed per tracker so history compares.",
    )
    samples_per_engine: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Answers are non-deterministic; each prompt is sampled up to this many "
        "times per engine and the citation *rate* is stored.",
    )
    adaptive_sampling: bool = Field(
        default=True,
        description="Stop sampling early once `min_samples` answers agree on whether the "
        "client was cited. Saves roughly a third of engine calls on stable prompts.",
    )
    min_samples: int = Field(
        default=2, ge=1, le=10, description="Samples taken before adaptive early-stop may apply."
    )
    pipeline_max_workers: int = Field(
        default=4, ge=1, le=16, description="Parallel engine calls per run."
    )
    max_engine_calls_per_run: int = Field(
        default=0, ge=0, description="Hard cap on engine calls per run. Zero = unlimited."
    )
    reuse_within_hours: int = Field(
        default=0,
        ge=0,
        description="Reuse a prompt/engine snapshot captured within this many hours instead "
        "of calling again. Zero = always call. Makes re-runs after a crash free.",
    )

    # -- Per-call cost estimates (USD), charged to CostLedger ----------------
    cost_openai_search_call_usd: float = Field(default=0.03, ge=0.0)
    cost_perplexity_call_usd: float = Field(default=0.02, ge=0.0)
    cost_gemini_grounded_call_usd: float = Field(default=0.04, ge=0.0)
    cost_serpapi_call_usd: float = Field(default=0.01, ge=0.0)
    cost_semrush_unit_usd: float = Field(default=0.000005, ge=0.0)

    # -- Semrush ----------------------------------------------------------
    semrush_database: str = Field(default="us", min_length=2, max_length=5)
    semrush_display_limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Rows per Semrush call. Semrush bills per row and defaults to "
        "10,000 rows when unset, so this is always sent explicitly.",
    )
    semrush_max_units_per_run: int = Field(default=20_000, ge=0)

    # -- Prompt tracker storage -------------------------------------------
    tracker_db_path: Path = REPO_ROOT / "data" / "prompt_tracker.sqlite"
    reports_dir: Path = REPO_ROOT / "reports"

    # -- Google Search Console --------------------------------------------
    google_search_console_client_email: str | None = None
    google_search_console_private_key: SecretStr | None = None

    # -- Google Ads --------------------------------------------------------
    google_ads_developer_token: SecretStr | None = None
    google_ads_client_id: str | None = None
    google_ads_client_secret: SecretStr | None = None
    google_ads_refresh_token: SecretStr | None = None

    # -- SEO data providers ------------------------------------------------
    # Accepts both SERP_API_KEY (this repo's convention) and SERPAPI_KEY (the
    # vendor's own convention, used in the Phase 1 validation scripts).
    serp_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("serp_api_key", "serpapi_key"),
    )
    ahrefs_api_key: SecretStr | None = None
    semrush_api_key: SecretStr | None = None

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Normalise and validate the log level."""
        normalised = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalised not in allowed:
            msg = f"LOG_LEVEL must be one of {sorted(allowed)}, got '{value}'"
            raise ValueError(msg)
        return normalised

    def model_post_init(self, _context: Any, /) -> None:
        """Refuse unsafe production configurations at boot rather than at call time."""
        if self.environment is Environment.PRODUCTION and not self.guardrails_enabled:
            msg = "GUARDRAILS_ENABLED=false is not permitted in production."
            raise ConfigurationError(msg)

    def require(self, field_name: str) -> str:
        """Return a required credential, or fail loudly with an actionable message.

        Args:
            field_name: Name of a settings field holding a credential.

        Returns:
            The plain-text value.

        Raises:
            ConfigurationError: If the field is unset or is not a known field.
        """
        if field_name not in type(self).model_fields:
            msg = f"'{field_name}' is not a known setting."
            raise ConfigurationError(msg)

        value = getattr(self, field_name)
        if value is None:
            msg = (
                f"Required setting '{field_name.upper()}' is not configured. "
                f"Add it to your .env file (see .env.example)."
            )
            raise ConfigurationError(msg)
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that `.env` is parsed exactly once and every module observes an
    identical view of configuration.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache. Intended for tests only."""
    get_settings.cache_clear()
