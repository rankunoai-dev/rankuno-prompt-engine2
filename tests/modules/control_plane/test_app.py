"""HTTP API tests via FastAPI's TestClient with a fake pipeline and an inline job manager."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core.schemas import ExecutionStatus
from src.modules.control_plane.app import create_app
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.runner import ProjectRunner
from tests.modules.control_plane.conftest import CLIENT, NOW
from tests.modules.control_plane.test_runner import FakePipeline

ENGINES = {"GOOGLE_AI_OVERVIEW", "CHATGPT_SEARCH", "PERPLEXITY", "GEMINI"}


def _client(store, db, settings, fake):
    runner = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=lambda: NOW)
    jobs = JobManager(runner, store, autostart=False, clock=lambda: NOW)
    tc = TestClient(create_app(store, db, runner, jobs, settings=lambda: settings))
    tc.fake = fake  # type: ignore[attr-defined]
    tc.jobs = jobs  # type: ignore[attr-defined]
    return tc


@pytest.fixture
def client(store, db, settings):
    with _client(store, db, settings, FakePipeline(db=db)) as tc:
        yield tc


def _create(client, **overrides):
    body = {"name": "GEP procurement", "client": CLIENT, **overrides}
    res = client.post("/api/projects", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_index_health_and_options(client):
    assert client.get("/api/health").json() == {"status": "ok", "active_jobs": 0}
    page = client.get("/")
    assert page.status_code == 200
    assert "Control Plane" in page.text
    assert "progress-card" in page.text and "/api/jobs/" in page.text
    opts = client.get("/api/options").json()
    assert [e["value"] for e in opts["engines"]] == [
        "GOOGLE_AI_OVERVIEW",
        "CHATGPT_SEARCH",
        "PERPLEXITY",
        "GEMINI",
    ]
    assert opts["intervals"][0]["value"] == "daily"
    assert any(i["value"] == "2d" for i in opts["intervals"])


def test_project_crud(client):
    project = _create(client, interval="2d", engines=["PERPLEXITY"])
    pid = project["id"]
    assert client.get("/api/projects").json()[0]["id"] == pid
    assert client.get(f"/api/projects/{pid}").json()["interval"] == "2d"
    res = client.put(f"/api/projects/{pid}", json={"interval": "weekly", "enabled": False})
    assert res.status_code == 200
    assert res.json()["interval"] == "weekly" and res.json()["enabled"] is False
    assert client.put(f"/api/projects/{pid}", json={"interval": "whenever"}).status_code == 422
    assert client.put(f"/api/projects/{pid}", json={"bogus": 1}).status_code == 422
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.delete(f"/api/projects/{pid}").status_code == 404


def test_prompt_crud_and_import(client):
    pid = _create(client)["id"]
    res = client.post(
        f"/api/projects/{pid}/prompts",
        json={"prompt_text": "What is the best procurement software?"},
    )
    assert res.status_code == 201
    tracked = res.json()
    assert tracked["important"] is False
    res = client.post(
        f"/api/projects/{pid}/prompts/import",
        json={"text": "Second one | procurement software\nThird one\n"},
    )
    assert res.status_code == 200 and len(res.json()) == 2
    assert len(client.get(f"/api/projects/{pid}/prompts").json()) == 3

    res = client.put(
        f"/api/projects/{pid}/prompts/{tracked['id']}",
        json={
            "important": True,
            "interval": "3d",
            "engines": ["GEMINI", "PERPLEXITY"],
            "samples_per_engine": 4,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert (
        body["important"] is True
        and body["interval"] == "3d"
        and body["engines"] == ["GEMINI", "PERPLEXITY"]
    )
    assert (
        client.get(f"/api/projects/{pid}/prompts").json()[0]["id"] == tracked["id"]
    )  # important first
    res = client.put(f"/api/projects/{pid}/prompts/{tracked['id']}", json={"clear_overrides": True})
    assert res.json()["interval"] is None and res.json()["engines"] is None
    assert (
        client.put(
            f"/api/projects/{pid}/prompts/{tracked['id']}", json={"interval": "nope"}
        ).status_code
        == 422
    )
    assert client.delete(f"/api/projects/{pid}/prompts/{tracked['id']}").status_code == 204
    assert client.get(f"/api/projects/{pid}/prompts/").status_code in (404, 405, 307, 200)
    assert client.get("/api/projects/nope/prompts").status_code == 404


def test_run_is_queued_then_visible_with_progress(client):
    pid = _create(client)["id"]
    client.post(
        f"/api/projects/{pid}/prompts",
        json={"prompt_text": "What is the best procurement software?"},
    )
    results = client.get(f"/api/projects/{pid}/results").json()
    assert len(results) == 1 and set(results[0]["due_on"]) == ENGINES

    res = client.post(f"/api/projects/{pid}/run", json={})
    assert res.status_code == 202, res.text
    job = res.json()
    assert job["state"] == "queued" and job["position"] == 0
    assert job["project_id"] == pid and job["progress"]["percent"] == 0.0
    assert client.fake.payloads == []  # nothing ran inside the request
    assert client.get("/api/api/health").status_code == 404
    assert client.get("/api/health").json()["active_jobs"] == 1
    assert [j["id"] for j in client.get("/api/jobs?active=true").json()] == [job["id"]]

    assert client.jobs.run_pending() == 1
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["state"] == "finished"
    assert done["progress"]["phase"] == "done" and done["progress"]["percent"] == 100.0
    assert done["progress"]["checks_total"] == 4 and done["progress"]["checks_done"] == 4
    out = done["outcome"]
    assert out["batches"] == 1 and out["prompts_run"] == 1 and out["statuses"] == ["success"]
    assert (
        client.fake.payloads[0].custom_prompts[0].prompt_text
        == "What is the best procurement software?"
    )
    assert client.get("/api/health").json()["active_jobs"] == 0

    results = client.get(f"/api/projects/{pid}/results").json()
    snap = results[0]["snapshots"]["PERPLEXITY"]
    assert snap["client_cited"] is True and snap["client_best_rank"] == 1
    assert results[0]["due_on"] == []

    again = client.post(f"/api/projects/{pid}/run").json()
    client.jobs.run_pending()
    again = client.get(f"/api/jobs/{again['id']}").json()
    assert again["outcome"]["batches"] == 0 and again["outcome"]["reason"] == "nothing due"
    forced = client.post(
        f"/api/projects/{pid}/run", json={"force": True, "engines": ["GEMINI"]}
    ).json()
    client.jobs.run_pending()
    assert client.get(f"/api/jobs/{forced['id']}").json()["outcome"]["batches"] == 1

    jobs = client.get(f"/api/projects/{pid}/jobs").json()
    assert [j["id"] for j in jobs] == [forced["id"], again["id"], job["id"]]
    assert len(client.get(f"/api/projects/{pid}/jobs?limit=1").json()) == 1
    assert len(client.get("/api/jobs").json()) == 3
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/projects/nope/jobs").status_code == 404

    export = client.get(f"/api/projects/{pid}/export").json()
    assert export["project"]["id"] == pid and len(export["prompts"]) == 1
    assert client.get(f"/api/projects/{pid}/runs").status_code == 200
    assert client.get("/api/projects/nope/run").status_code in (404, 405)
    assert client.post("/api/projects/nope/run").status_code == 404


def test_blocked_run_is_visible_in_job_outcome(store, db, settings):
    fake = FakePipeline(status=ExecutionStatus.BLOCKED_PENDING_APPROVAL)
    with _client(store, db, settings, fake) as tc:
        pid = _create(tc)["id"]
        tc.post(f"/api/projects/{pid}/prompts", json={"prompt_text": "Some prompt text"})
        job = tc.post(f"/api/projects/{pid}/run").json()
        tc.jobs.run_pending()
        out = tc.get(f"/api/jobs/{job['id']}").json()["outcome"]
    assert out["statuses"] == ["blocked_pending_approval"]
    assert any("denied" in w for w in out["warnings"])


def test_atlas_data_is_served_live_from_the_store(client):
    body = {"name": "GEP", "client": {**CLIENT, "domains": ["gep.com"]}}
    pid = client.post("/api/projects", json=body).json()["id"]
    client.post(f"/api/projects/{pid}/prompts", json={"prompt_text": "Some prompt text"})
    empty = client.get("/reports/prompt-atlas-data.json").json()
    assert empty["snapshots"] == [] and empty["meta"]["domains"] == ["gep.com"]
    client.post(f"/api/projects/{pid}/run")
    client.jobs.run_pending()
    doc = client.get("/docs/prompt-atlas-data.json").json()
    assert len(doc["snapshots"]) == 4 and doc["snapshots"][0]["mention_rate"] == 0.0
    assert doc["prompts"][0]["prompt_text"] == "Some prompt text"
    assert doc["meta"]["competitor_domains"] == ["coupa.com"]
    assert client.get("/reports/prompt-atlas-data.json?lob=other").json()["prompts"] == []


def test_create_app_builds_its_own_job_manager(store, db, settings):
    runner = ProjectRunner(store, db, settings=settings, pipeline=FakePipeline(db=db))
    with TestClient(create_app(store, db, runner)) as tc:
        assert tc.get("/api/jobs").json() == []


def test_costs_report_for_project_and_everything(client, settings, db):
    from datetime import UTC, datetime

    from src.integrations.usage import ApiCall, get_usage_ledger
    from src.modules.prompt_tracking.schemas import TrackerRunSummary

    pid = _create(client)["id"]
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)  # a week back: outside any 1-day window
    db.record_run(
        TrackerRunSummary(
            run_id="run-project-1",
            lob=CLIENT["lob"],
            brand_name="GEP",
            started_at=now,
            finished_at=now,
            candidates_generated=1,
            candidates_kept=1,
            prompts_selected=1,
            engine_calls=1,
            failed_engine_calls=0,
            estimated_cost_usd=0.02,
            semrush_units=0,
        )
    )
    ledger = get_usage_ledger(settings)
    ledger.record(
        ApiCall(
            vendor="perplexity",
            operation="v1.responses",
            ts=now,
            run_id="run-project-1",
            source="control_plane",
            estimated_cost_usd=0.02,
            vendor_cost_usd=0.0098,
        )
    )
    ledger.record(
        ApiCall(
            vendor="openai",
            operation="responses.create",
            ts=now,
            run_id="elsewhere",
            source="live_check",
            estimated_cost_usd=0.03,
            modelled_cost_usd=0.011,
        )
    )
    mine = client.get(f"/api/costs?project_id={pid}").json()
    assert mine["calls"] == 1 and mine["vendors"][0]["vendor"] == "perplexity"
    assert mine["total_actual_usd"] == 0.0098 and mine["by_source"] == {"control_plane": 1}
    rec = mine["recommendations"][0]
    assert rec["setting"] == "cost_perplexity_call_usd"
    assert rec["current"] == settings.cost_perplexity_call_usd
    everything = client.get("/api/costs").json()
    assert everything["calls"] == 2
    assert {v["vendor"] for v in everything["vendors"]} == {"openai", "perplexity"}
    assert client.get("/api/costs?days=1").json()["calls"] == 0  # both rows are a week old
    assert client.get("/api/costs?project_id=nope").status_code == 404


def test_consolidation_routes(client):
    pid = _create(client, consolidation_runs=2)["id"]
    assert client.get(f"/api/projects/{pid}").json()["consolidation_runs"] == 2
    client.post(f"/api/projects/{pid}/prompts", json={"prompt_text": "Some prompt text"})
    empty = client.get(f"/api/projects/{pid}/positions").json()
    assert empty["consolidation"] is None and empty["runs_since_last"] == 0
    assert client.post(f"/api/projects/{pid}/consolidate").status_code == 400  # no crawl yet

    job = client.post(f"/api/projects/{pid}/run", json={"force": True}).json()
    client.jobs.run_pending()
    out = client.get(f"/api/jobs/{job['id']}").json()["outcome"]
    assert out["project_run_id"] and out["consolidation_id"] is None
    assert client.get(f"/api/projects/{pid}/positions").json()["runs_since_last"] == 1

    job = client.post(f"/api/projects/{pid}/run", json={"force": True}).json()
    client.jobs.run_pending()
    out = client.get(f"/api/jobs/{job['id']}").json()["outcome"]
    assert out["consolidation_id"]
    view = client.get(f"/api/projects/{pid}/positions").json()
    assert view["consolidation"]["trigger"] == "auto" and view["consolidation"]["window_runs"] == 2
    assert len(view["positions"]) == 4 and view["runs_since_last"] == 0
    assert view["positions"][0]["cited"] is True and view["positions"][0]["runs"] == 2

    manual = client.post(
        f"/api/projects/{pid}/consolidate", json={"window_runs": 1, "note": "spot check"}
    ).json()
    assert manual["trigger"] == "manual" and manual["window_runs"] == 1
    chosen = client.get(
        f"/api/projects/{pid}/positions?consolidation_id={view['consolidation']['id']}"
    ).json()
    assert chosen["consolidation"]["id"] == view["consolidation"]["id"]
    assert len(chosen["history"]) == 2
    assert client.get(f"/api/projects/{pid}/positions?consolidation_id=nope").status_code == 404
    crawls = client.get(f"/api/projects/{pid}/crawls").json()
    assert len(crawls) == 2 and all(c["full"] for c in crawls)
    assert client.put(f"/api/projects/{pid}", json={"consolidation_runs": 0}).status_code == 422
    assert client.get("/api/projects/nope/positions").status_code == 404


def test_insights_actions_and_samples_routes(client):
    pid = _create(client, consolidation_runs=1)["id"]
    client.post(f"/api/projects/{pid}/prompts", json={"prompt_text": "Some prompt text"})
    empty = client.get(f"/api/projects/{pid}/insights").json()
    assert empty["basis"]["computed_from"] == "none" and empty["actions"] == []
    assert {h["verdict"] for h in empty["health"]} == {"invisible"}

    job = client.post(f"/api/projects/{pid}/run", json={"force": True}).json()
    client.jobs.run_pending()
    assert client.get(f"/api/jobs/{job['id']}").json()["outcome"]["consolidation_id"]
    view = client.get(f"/api/projects/{pid}/insights").json()
    assert view["basis"]["computed_from"] == "consolidation" and view["basis"]["crawls"] == 1
    assert {h["engine"] for h in view["health"]} == ENGINES
    assert all(h["cited_rate"] == 1.0 for h in view["health"])  # FakePipeline cites everywhere
    assert isinstance(view["fanout"], list) and isinstance(view["claims"], list)
    assert client.get(f"/api/projects/{pid}/insights?consolidation_id=nope").status_code == 404

    prompt_id = client.get(f"/api/projects/{pid}/prompts").json()[0]["prompt_id"]
    samples = client.get(f"/api/projects/{pid}/samples?prompt_id={prompt_id}").json()
    assert samples == []  # the fake pipeline records snapshots, not raw samples
    assert (
        client.get(f"/api/projects/{pid}/samples?prompt_id={prompt_id}&engine=NOPE").status_code
        == 422
    )
    assert client.get("/api/projects/nope/samples?prompt_id=x").status_code == 404

    res = client.put(f"/api/projects/{pid}/actions/nope", json={"status": "done"})
    assert res.status_code == 404
    assert client.put(f"/api/projects/{pid}/actions/x", json={"status": "later"}).status_code == 422
