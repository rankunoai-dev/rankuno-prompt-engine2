"""What a public deployment relies on: Basic auth, durable spend cap, bounded
queue, a health check that notices a broken store, and settings-driven binding.
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.core.config import Settings
from src.core.errors import BudgetExceededError, ConfigurationError
from src.core.rate_limiter import CostLedger
from src.integrations.usage import ApiCall, UsageLedger
from src.modules.control_plane.app import create_app
from src.modules.control_plane.auth import credentials_match
from src.modules.control_plane.jobs import JobManager, QueueFull
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import RunRequest
from tests.modules.control_plane.conftest import CLIENT, NOW
from tests.modules.control_plane.test_runner import FakePipeline


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _client(store, db, settings):
    runner = ProjectRunner(store, db, settings=settings, pipeline=FakePipeline(db=db))
    jobs = JobManager(runner, store, autostart=False, clock=lambda: NOW)
    return TestClient(create_app(store, db, runner, jobs, settings=lambda: settings))


# -- Basic auth -----------------------------------------------------------------


def test_credentials_match_is_strict_about_shape():
    assert credentials_match(_basic("ana", "s3cret")["Authorization"], "ana", "s3cret")
    assert not credentials_match(_basic("ana", "wrong")["Authorization"], "ana", "s3cret")
    assert not credentials_match(_basic("bob", "s3cret")["Authorization"], "ana", "s3cret")
    assert not credentials_match(None, "ana", "s3cret")
    assert not credentials_match("Bearer abc", "ana", "s3cret")
    assert not credentials_match("Basic not-base64!!", "ana", "s3cret")
    assert not credentials_match("Basic " + base64.b64encode(b"nocolon").decode(), "ana", "x")


def test_every_route_but_health_requires_credentials(store, db, settings):
    guarded = settings.model_copy(
        update={"control_plane_user": "ana", "control_plane_password": SecretStr("s3cret")}
    )
    with _client(store, db, guarded) as client:
        assert client.get("/api/health").status_code == 200  # the platform's probe
        for path in ("/", "/api/projects", "/api/options", "/docs", "/openapi.json"):
            res = client.get(path)
            assert res.status_code == 401, path
            assert res.headers["www-authenticate"].startswith("Basic")
        assert client.post("/api/projects", json={"name": "x", "client": CLIENT}).status_code == 401
        assert client.get("/api/projects", headers=_basic("ana", "s3cret")).status_code == 200
        assert client.get("/api/projects", headers=_basic("ana", "nope")).status_code == 401


def test_without_credentials_the_app_is_open_as_before(store, db, settings):
    assert not settings.basic_auth_configured
    with _client(store, db, settings) as client:
        assert client.get("/api/projects").status_code == 200


def test_production_refuses_to_boot_without_credentials(tmp_path):
    kwargs = {"_env_file": None, "environment": "production", "tracker_db_path": tmp_path / "x"}
    with pytest.raises(ConfigurationError, match="CONTROL_PLANE_USER"):
        Settings(**kwargs)
    ok = Settings(**kwargs, control_plane_user="ana", control_plane_password="pw")
    assert ok.basic_auth_configured


def test_host_and_port_come_from_settings_not_os_environ(monkeypatch, tmp_path):
    monkeypatch.setenv("HOST", "0.0.0.0")  # noqa: S104 - the whole point of the test
    monkeypatch.setenv("PORT", "6543")
    s = Settings(_env_file=None, tracker_db_path=tmp_path / "x")
    assert (s.host, s.port) == ("0.0.0.0", 6543)  # noqa: S104
    # and the repo rule still holds: nothing outside config.py reads the environment
    offenders = [
        p
        for p in Path("src").rglob("*.py")
        if p.name != "config.py" and "os.environ" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


# -- durable spend cap ------------------------------------------------------------


def test_daily_cap_counts_spend_recorded_before_this_process_started():
    """The restart case: in-memory is zero, the ledger says today is already spent."""
    ledger = CostLedger(5.0, daily_ceiling_usd=1.0, spent_today=lambda: 0.95)
    with pytest.raises(BudgetExceededError):
        ledger.charge(0.10)
    assert ledger.charge(0.04) == 0.04  # under the daily cap, and under the session one
    assert ledger.remaining_usd == pytest.approx(0.01)


def test_daily_cap_resets_at_utc_midnight_and_release_gives_headroom_back():
    days = iter([date(2026, 9, 21), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 22)])
    durable = iter([0.9, 0.0])  # yesterday's spend, then a fresh day
    ledger = CostLedger(
        5.0, daily_ceiling_usd=1.0, spent_today=lambda: next(durable), today=lambda: next(days)
    )
    ledger.charge(0.05)
    with pytest.raises(BudgetExceededError):
        ledger.charge(0.10)
    assert ledger.charge(0.50) == 0.55  # new day: baseline re-read as 0.0
    ledger.release(0.30)
    assert ledger.spent_usd == pytest.approx(0.25)
    with pytest.raises(ValueError, match="negative"):
        ledger.release(-1)


def test_daily_cap_is_inert_without_a_provider():
    ledger = CostLedger(1.0, daily_ceiling_usd=0.0)  # no spent_today → no daily check
    assert ledger.charge(0.5) == 0.5


def test_spent_since_sums_actual_cost_and_skips_demo_rows(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    rows = [
        ApiCall(
            vendor="openai",
            operation="o",
            ts=NOW,
            source="control_plane",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.01,
        ),
        ApiCall(
            vendor="perplexity",
            operation="o",
            ts=NOW,
            source="control_plane",
            estimated_cost_usd=0.02,
            vendor_cost_usd=0.009,
        ),
        ApiCall(
            vendor="openai",
            operation="o",
            ts=NOW,
            source="control_plane",
            estimated_cost_usd=0.03,
            status="error",
        ),  # failed: not spent
        ApiCall(
            vendor="openai",
            operation="o",
            ts=NOW,
            source="demo",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.03,
        ),  # seeded: excluded
        ApiCall(
            vendor="openai",
            operation="o",
            ts=NOW - timedelta(days=2),
            source="control_plane",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.03,
        ),
    ]
    for row in rows:
        ledger.record(row)
    assert ledger.spent_since(NOW - timedelta(hours=1)) == pytest.approx(0.019)
    assert ledger.spent_since(NOW - timedelta(hours=1), exclude_sources=()) == pytest.approx(0.049)
    assert ledger.spent_since(datetime(2030, 1, 1, tzinfo=UTC)) == 0.0


# -- bounded queue ------------------------------------------------------------------


def test_queue_bound_returns_429(store, db, settings, project):
    runner = ProjectRunner(store, db, settings=settings, pipeline=FakePipeline(db=db))
    jobs = JobManager(runner, store, autostart=False, max_queued=2, clock=lambda: NOW)
    jobs.submit(project.id, RunRequest(force=True))
    jobs.submit(project.id, RunRequest(force=False))
    with pytest.raises(QueueFull):
        jobs.submit(project.id, RunRequest(force=True, engines=None, prompt_ids=["x" * 12]))
    with TestClient(create_app(store, db, runner, jobs, settings=lambda: settings)) as client:
        res = client.post(f"/api/projects/{project.id}/run", json={"prompt_ids": ["y" * 12]})
        assert res.status_code == 429 and res.headers["retry-after"] == "60"


# -- health probes the store ----------------------------------------------------------


def test_health_reports_degraded_when_the_store_is_gone(store, db, settings):
    with _client(store, db, settings) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        db.path.unlink()  # simulate a vanished volume: the file is gone under us
        Path(db.path).parent.rmdir()
        res = client.get("/api/health")
        assert res.status_code == 503 and res.json()["status"] == "degraded"
