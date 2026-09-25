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
from src.core.locale import Locale

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

    # -- Control plane server ----------------------------------------------
    # `PORT` is what PaaS platforms (Railway, Render, Heroku) inject; reading it
    # here keeps the "no os.environ outside config.py" rule intact.
    host: str = Field(
        default="127.0.0.1",
        description="Bind address. Loopback by default; a container needs 0.0.0.0.",
    )
    port: int = Field(default=8787, ge=1, le=65535)
    control_plane_user: str | None = Field(
        default=None, description="HTTP Basic username protecting every route but /api/health."
    )
    control_plane_password: SecretStr | None = Field(
        default=None, description="HTTP Basic password. Required with the user in production."
    )
    project_admin_password: SecretStr | None = Field(
        default=None,
        description="Recovery credential (16+ characters): unlocks writes on any project whose "
        "owner password is lost (ADR 0019). Unset or blank disables the override entirely.",
    )

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
    daily_spend_cap_usd: float = Field(
        default=5.0,
        ge=0.0,
        description="Ceiling on actual spend since 00:00 UTC, read back from the usage "
        "ledger so it survives process restarts. Applies alongside the session ceiling.",
    )
    unattended_spend_cap_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Per-action cap for BudgetedApprovalProvider. Zero (the default) "
        "means unattended runs cannot spend at all.",
    )

    # -- Crawler log imports (ADR 0022) --------------------------------------
    crawler_log_max_bytes: int = Field(
        default=50_000_000,
        ge=1_000,
        description="Cap on one uploaded access log, measured after decompression.",
    )
    crawler_log_max_json_bytes: int = Field(
        default=2_000_000,
        ge=1_000,
        description="Cap on a log posted as JSON text or as a JSON array, which cannot "
        "be streamed and costs several times its size in memory.",
    )
    crawler_log_retention_days: int = Field(
        default=400,
        ge=1,
        description="Aggregated crawler hits older than this many days are purged. "
        "Import records are kept as provenance.",
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
    openrouter_api_key: SecretStr | None = None
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

    # -- Sentiment judge (ADR 0021) ---------------------------------------
    anthropic_judge_model: str = Field(
        default="claude-haiku-4-5",
        description="Model that scores brand mentions after a crawl. Judging is "
        "classification; the cheapest current model is the right default.",
    )
    sentiment_max_sentences_per_run: int = Field(
        default=400,
        ge=0,
        description="Hard cap on mention sentences judged per crawl; the rest are "
        "recorded as unscored. Zero disables judging.",
    )
    sentiment_batch_size: int = Field(default=40, ge=1, le=100)
    serp_gl: str = Field(default="us", min_length=2, max_length=2)
    serp_hl: str = Field(default="en", min_length=2, max_length=5)
    serp_location: str = Field(default="United States")
    serp_device: str = Field(
        default="desktop",
        pattern="^(desktop|mobile|tablet)$",
        description="Organic rankings differ by device; fixed per tracker so history compares.",
    )

    def default_locale(self) -> Locale:
        """Locale for a project that does not set its own (the historical behaviour)."""
        return Locale(
            country=self.serp_gl,
            language=self.serp_hl,
            serp_location=self.serp_location or None,
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
    # Cross-crawl stability and the per-project sampling policy (ADR 0025).
    stability_window_crawls: int = Field(
        default=4,
        ge=2,
        le=20,
        description="Newest crawls pooled to judge whether a prompt x platform pair is settled.",
    )
    stability_min_crawls: int = Field(
        default=3, ge=2, le=20, description="Crawls needed before a pair can leave 'unknown'."
    )
    stretch_max: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Largest interval multiplier a stable pair may reach under the save and "
        "reallocate policies; also capped by the project's consolidation window.",
    )
    volatile_boost: int = Field(
        default=2,
        ge=0,
        le=9,
        description="Extra samples per platform for a volatile pair under the reallocate "
        "policy, paid for by the calls stretching saved.",
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
    cost_anthropic_judge_call_usd: float = Field(
        default=0.005, ge=0.0, description="Per judge batch (about 40 sentences)."
    )

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

    # -- Executive reports and alerting (ADR 0024) -------------------------
    anthropic_report_model: str = Field(
        default="claude-sonnet-5",
        description="Model that writes the executive summary. Prose for a client, not "
        "classification, so it is a step up from the judge model.",
    )
    report_max_spend_usd: float = Field(
        default=0.25,
        ge=0.0,
        description="Ceiling for one report's narrative call. Zero disables the vendor "
        "call and every report falls back to deterministic prose.",
    )
    cost_anthropic_report_call_usd: float = Field(
        default=0.02, ge=0.0, description="Estimate booked before the real token counts arrive."
    )
    report_retention_days: int = Field(
        default=400,
        ge=1,
        description="Generated PDFs older than this are purged; the report row is kept.",
    )
    report_logo_max_bytes: int = Field(
        default=2_000_000,
        ge=1_000,
        description="Cap on an uploaded logo, checked before the image is decoded.",
    )
    alerts_max_per_project_per_day: int = Field(
        default=5,
        ge=0,
        description="Hard ceiling on outbound alerts for one project in 24h. A flapping "
        "metric must not be able to spam a client's Slack. Zero disables sending.",
    )
    alert_cooldown_hours: int = Field(
        default=72,
        ge=0,
        description="The same rule, engine and subject stays quiet this long after firing.",
    )
    smtp_host: str | None = Field(default=None, description="Unset disables email delivery.")
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = Field(
        default=None, description="Envelope sender, e.g. 'RankUno <reports@agency.com>'."
    )
    smtp_starttls: bool = Field(
        default=True, description="False only for a local relay that is already encrypted."
    )
    smtp_timeout_s: float = Field(default=20.0, gt=0.0)

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

    @property
    def basic_auth_configured(self) -> bool:
        """True when both halves of the control-plane credential are set."""
        return bool(self.control_plane_user) and self.control_plane_password is not None

    @property
    def project_admin_secret(self) -> str | None:
        """The recovery password, or `None` when unset or blank (a blank `.env` line)."""
        if self.project_admin_password is None:
            return None
        return self.project_admin_password.get_secret_value() or None

    def model_post_init(self, _context: Any, /) -> None:
        """Refuse unsafe production configurations at boot rather than at call time."""
        if self.project_admin_secret is not None and len(self.project_admin_secret) < 16:
            # It opens every project, so a guessable value is worse than none at all.
            msg = "PROJECT_ADMIN_PASSWORD must be at least 16 characters, or left blank."
            raise ConfigurationError(msg)
        if self.environment is Environment.PRODUCTION and not self.guardrails_enabled:
            msg = "GUARDRAILS_ENABLED=false is not permitted in production."
            raise ConfigurationError(msg)
        if self.environment is Environment.PRODUCTION and not self.basic_auth_configured:
            # Loopback binding was the only access control this app ever had; a
            # public deployment without credentials exposes spend and deletes.
            msg = "CONTROL_PLANE_USER and CONTROL_PLANE_PASSWORD are required in production."
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
        if value is None and self.openrouter_api_key is not None:
            value = self.openrouter_api_key
        if value is None:
            msg = (
                f"Required setting '{field_name.upper()}' is not configured. "
                "Set OPENROUTER_API_KEY or the vendor setting as an environment variable, "
                "or in .env for local runs (see .env.example)."
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
