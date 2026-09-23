"""Crawler-log routes: both body shapes, the error mappings, the view and the card."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.integrations.schemas import Citation, Engine
from src.modules.control_plane.app import create_app
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.runner import ProjectRunner
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    DecisionStage,
    MasterPromptRecord,
    PromptType,
    SearchIntent,
    Verdict,
)
from tests.modules.control_plane.conftest import CLIENT, NOW
from tests.modules.control_plane.test_runner import FakePipeline

UA = "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)"


def line(path: str, status: int = 200, sec: int = 57) -> str:
    when = f"[18/Sep/2026:06:59:{sec:02d} +0000]"
    return f'203.0.113.9 - - {when} "GET {path} HTTP/1.1" {status} 512 "-" "{UA}"'


def _seed_prompt(db, prompt_id: str) -> None:
    db.upsert_prompt(
        MasterPromptRecord(
            prompt_id=prompt_id,
            lob=CLIENT["lob"],
            subtopic="S",
            core_keyword="k",
            search_volume=0,
            prompt_text="p",
            search_intent=SearchIntent.INFORMATIONAL,
            decision_stage=DecisionStage.AWARENESS,
            prompt_type=PromptType.NON_BRANDED,
            web_triggers=True,
            verdict=Verdict.KEEP,
            verdict_reason="x",
        ),
        "GEP",
    )


@pytest.fixture
def client(store, db, settings):
    small = settings.model_copy(
        update={"crawler_log_max_bytes": 5_000, "crawler_log_max_json_bytes": 2_000}
    )
    runner = ProjectRunner(
        store, db, settings=small, pipeline=FakePipeline(db=db), clock=lambda: NOW
    )
    jobs = JobManager(runner, store, autostart=False, clock=lambda: NOW)
    with TestClient(create_app(store, db, runner, jobs, settings=lambda: small)) as tc:
        yield tc


def _project(client) -> str:
    res = client.post("/api/projects", json={"name": "GEP", "client": CLIENT})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_import_as_json_text_then_view_and_delete(client):
    pid = _project(client)
    text = "\n".join(line(f"/blog/{i}") for i in range(3)) + "\n"
    res = client.post(
        f"/api/projects/{pid}/crawler-logs/import", json={"text": text, "note": "sept"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["format"] == "combined" and body["hits"] == 3 and body["parsed"] == 3
    assert body["verification_basis"] == "remote_addr" and body["overlaps"] == []
    assert "No request lines" in body["stored"]

    view = client.get(f"/api/projects/{pid}/crawler-logs?days=400").json()
    assert view["covered_days"] == 1 and len(view["pages"]) == 3
    assert view["by_bot"][0]["bot"] == "OAI-SearchBot" and view["imports"][0]["note"] == "sept"
    assert "openai" in view["ranges"]["vendors"]
    assert client.get(f"/api/projects/{pid}/crawler-logs?days=0").status_code == 422

    dup = client.post(f"/api/projects/{pid}/crawler-logs/import", json={"text": text})
    assert dup.status_code == 409 and dup.json()["import_id"] == body["import_id"]

    gone = client.delete(f"/api/projects/{pid}/crawler-logs/imports/{body['import_id']}")
    assert gone.status_code == 204
    assert (
        client.delete(f"/api/projects/{pid}/crawler-logs/imports/{body['import_id']}").status_code
        == 404
    )
    assert client.get(f"/api/projects/{pid}/crawler-logs?days=400").json()["pages"] == []


def test_import_as_raw_text_and_gzip_bodies(client):
    pid = _project(client)
    raw = client.post(
        f"/api/projects/{pid}/crawler-logs/import",
        content=(line("/a") + "\n").encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert raw.status_code == 200 and raw.json()["hits"] == 1
    packed = client.post(
        f"/api/projects/{pid}/crawler-logs/import",
        content=gzip.compress((line("/b") + "\n").encode()),
        headers={"Content-Type": "application/gzip"},
    )
    assert packed.status_code == 200 and packed.json()["hits"] == 1


def test_error_mappings_400_413_and_bots_catalogue(client):
    pid = _project(client)
    bad = client.post(f"/api/projects/{pid}/crawler-logs/import", json={"text": "not a log at all"})
    assert bad.status_code == 400 and "recognisable" in bad.json()["detail"]
    assert "not a log" not in bad.json()["detail"]  # never echo line content
    huge = client.post(
        f"/api/projects/{pid}/crawler-logs/import",
        content=(line("/x") + "\n").encode() * 100,
        headers={"Content-Type": "text/plain"},
    )
    assert huge.status_code == 413
    too_big_json = client.post(
        f"/api/projects/{pid}/crawler-logs/import", json={"text": (line("/x") + "\n") * 20}
    )
    assert too_big_json.status_code == 413
    empty = client.post(
        f"/api/projects/{pid}/crawler-logs/import",
        content=b"",
        headers={"Content-Type": "text/plain"},
    )
    assert empty.status_code == 400
    assert (
        client.post("/api/projects/nope/crawler-logs/import", json={"text": "x"}).status_code == 404
    )
    bots = client.get("/api/crawler-logs/bots").json()
    assert {b["name"] for b in bots} >= {"OAI-SearchBot", "GPTBot", "PerplexityBot", "Googlebot"}


def test_fetched_not_cited_card_reaches_insights(client, db, store):
    pid = _project(client)
    client.post(f"/api/projects/{pid}/prompts", json={"prompt_text": "How do I implement GEP?"})
    prompt_id = store.list_prompts(pid)[0].prompt_id
    _seed_prompt(db, prompt_id)
    samples = [
        AnswerSample(
            prompt_id=prompt_id,
            engine=Engine.CHATGPT_SEARCH,
            model="m",
            captured_at=datetime(2026, 9, 18, 7, tzinfo=UTC),
            web_triggered=True,
            client_cited=False,
            citation_links=[
                Citation(url="https://coupa.com/x", domain="coupa.com", title="c", position=1)
            ],
            consulted_urls=["https://www.gep.com/blog/read-me"],
        )
        for _ in range(3)
    ]
    db.record_samples(prompt_id, samples, "run-x")
    text = "\n".join(line("/blog/read-me", sec=i) for i in range(4)) + "\n"
    assert (
        client.post(f"/api/projects/{pid}/crawler-logs/import", json={"text": text}).status_code
        == 200
    )

    view = client.get(f"/api/projects/{pid}/crawler-logs?days=400").json()
    assert [c["url_key"] for c in view["fetched_not_cited"]] == ["gep.com/blog/read-me"]
    insights = client.get(f"/api/projects/{pid}/insights").json()
    card = next(a for a in insights["actions"] if a["type"] == "fetched_not_cited")
    assert "OAI-SearchBot fetched /blog/read-me 4x" in card["title"]
    assert card["engine"] == "CHATGPT_SEARCH" and card["evidence"]["numbers"]["consulted"] == 3.0
    assert card["evidence"]["urls"] == ["https://gep.com/blog/read-me"]
    # analyst state applies to it like any other card
    done = client.put(f"/api/projects/{pid}/actions/{card['id']}", json={"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done"
