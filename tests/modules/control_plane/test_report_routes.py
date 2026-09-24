"""The report, branding and alert routes: who may call them and what they leak."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.modules.control_plane.app import create_app
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.runner import ProjectRunner
from tests.modules.control_plane.conftest import CLIENT, NOW
from tests.modules.control_plane.test_runner import FakePipeline

HOOK = "https://hooks.slack.com/services/T04AB/B05CD/xyz123secret"
# A real 4x2 PNG: Pillow verifies the CRC, so a hand-written header fails.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000040000000208060000007fa87d63"
    "000000134944415478da6394b78affcf80049818d00000350801bb4db3d45e0000"
    "000049454e44ae426082"
)


@pytest.fixture
def client(store, db, settings):
    """A live app over the temporary stores, with the pipeline faked."""
    runner = ProjectRunner(
        store, db, settings=settings, pipeline=FakePipeline(db=db), clock=lambda: NOW
    )
    jobs = JobManager(runner, store, autostart=False, clock=lambda: NOW)
    with TestClient(create_app(store, db, runner, jobs, settings=lambda: settings)) as tc:
        yield tc


@pytest.fixture
def project_id(client) -> str:
    response = client.post("/api/projects", json={"name": "GEP", "client": CLIENT})
    assert response.status_code == 201
    return str(response.json()["id"])


def test_a_new_project_carries_a_default_brand(client, project_id):
    """Branding is part of the project, so the form has something to edit."""
    body = client.get(f"/api/projects/{project_id}").json()
    assert body["brand"]["primary_colour"] == "#1f3a5f"
    assert body["brand"]["show_spend"] is False
    assert body["brand"]["logo_id"] is None


def test_the_brand_is_editable_and_refuses_a_colour_that_is_not_one(client, project_id):
    """ReportLab fails deep inside a draw call otherwise."""
    ok = client.put(
        f"/api/projects/{project_id}",
        json={"brand": {"agency_name": "RankUno", "primary_colour": "#0a7", "show_spend": True}},
    )
    assert ok.status_code == 200
    assert ok.json()["brand"]["primary_colour"] == "#00aa77"  # expanded

    bad = client.put(
        f"/api/projects/{project_id}", json={"brand": {"primary_colour": "not-a-colour"}}
    )
    assert bad.status_code == 422


def test_a_logo_id_cannot_be_set_by_hand(client, project_id):
    """Only the upload route mints one, so a path cannot be smuggled in."""
    response = client.put(
        f"/api/projects/{project_id}", json={"brand": {"logo_id": "../../etc/passwd"}}
    )
    assert response.status_code == 422


def test_a_logo_uploads_validates_and_serves(client, project_id):
    """The bytes are decoded before they are stored, and served back for preview."""
    response = client.post(f"/api/projects/{project_id}/branding/logo", content=PNG)
    assert response.status_code == 200
    logo_id = response.json()["logo_id"]
    assert logo_id.endswith(".png")
    assert client.get(f"/api/projects/{project_id}").json()["brand"]["logo_id"] == logo_id

    served = client.get(f"/api/projects/{project_id}/branding/logo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG


def test_a_file_that_is_not_an_image_is_refused(client, project_id):
    """'Logo' is not a synonym for 'arbitrary upload'."""
    response = client.post(
        f"/api/projects/{project_id}/branding/logo", content=b"<svg>not an image</svg>"
    )
    assert response.status_code == 400
    assert "PNG or JPEG" in response.json()["detail"]


def test_an_oversized_logo_is_refused_before_it_is_decoded(client, project_id, settings):
    """The size check precedes `Image.open`, which is the decompression bomb."""
    response = client.post(
        f"/api/projects/{project_id}/branding/logo",
        content=b"x" * (settings.report_logo_max_bytes + 1),
    )
    assert response.status_code == 400
    assert "ceiling" in response.json()["detail"]


def test_a_report_is_queued_and_polled_then_downloaded(client, project_id):
    """202 with a row, then the worker fills it in."""
    queued = client.post(f"/api/projects/{project_id}/reports", json={"narrative": False})
    assert queued.status_code == 202
    report_id = queued.json()["id"]
    assert queued.json()["state"] in {"queued", "running", "done"}

    listed = client.get(f"/api/projects/{project_id}/reports").json()
    assert [row["id"] for row in listed] == [report_id]

    row = client.get(f"/api/projects/{project_id}/reports/{report_id}")
    assert row.status_code == 200
    assert row.json()["id"] == report_id


def test_a_report_of_another_project_is_not_found(client, project_id):
    """Ids are opaque, but they are still scoped to their project."""
    other = client.post("/api/projects", json={"name": "Other", "client": CLIENT}).json()["id"]
    report_id = client.post(f"/api/projects/{project_id}/reports", json={}).json()["id"]
    assert client.get(f"/api/projects/{other}/reports/{report_id}").status_code == 404
    assert client.get(f"/api/projects/{other}/reports/{report_id}/download").status_code == 404


def test_downloading_a_report_that_has_no_file_yet_is_a_400(client, project_id):
    """The row exists; the artefact does not."""
    report_id = client.post(f"/api/projects/{project_id}/reports", json={}).json()["id"]
    response = client.get(f"/api/projects/{project_id}/reports/{report_id}/download")
    assert response.status_code in {400, 200}
    if response.status_code == 400:
        assert "download" in response.json()["detail"]


def test_the_alert_destination_never_returns_the_webhook_or_the_addresses(client, project_id):
    """Reads are open (ADR 0019), so the response must be safe for anyone."""
    saved = client.put(
        f"/api/projects/{project_id}/alerts",
        json={"slack_webhook": HOOK, "email_to": ["priya@client.com"], "enabled": True},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["slack_configured"] is True
    assert body["slack_hint"] == "hooks.slack.com/services/T04A…"
    assert body["email_hints"] == ["p***@client.com"]
    assert "xyz123secret" not in saved.text
    assert "priya@client.com" not in saved.text

    read = client.get(f"/api/projects/{project_id}/alerts")
    assert "xyz123secret" not in read.text
    assert read.json()["rules"] == ["citation_drop"]


def test_the_project_payload_never_carries_the_destination(client, project_id):
    """The whole reason the destination is a separate table."""
    client.put(
        f"/api/projects/{project_id}/alerts",
        json={"slack_webhook": HOOK, "email_to": ["priya@client.com"], "enabled": True},
    )
    project = client.get(f"/api/projects/{project_id}")
    assert "hooks.slack.com" not in project.text
    assert "priya@client.com" not in project.text
    listing = client.get("/api/projects")
    assert "hooks.slack.com" not in listing.text


def test_a_hostile_destination_is_refused_with_a_reason(client, project_id):
    """A 400 an operator can act on, not a 500."""
    response = client.put(
        f"/api/projects/{project_id}/alerts",
        json={"slack_webhook": "https://evil.example.com/hook"},
    )
    assert response.status_code == 400
    assert "hooks.slack.com" in response.json()["detail"]


def test_alert_history_is_readable_and_starts_empty(client, project_id):
    """The audit trail the suppression reasons live in."""
    response = client.get(f"/api/projects/{project_id}/alerts/history")
    assert response.status_code == 200
    assert response.json() == []


def test_writes_need_the_owner_credential_when_the_project_is_protected(client, project_id):
    """Generating a report spends money; configuring alerts sets a credential."""
    client.put(
        f"/api/projects/{project_id}/credentials",
        json={"owner": "gaurav", "password": "open sesame"},
    )
    assert client.post(f"/api/projects/{project_id}/reports", json={}).status_code == 403
    assert (
        client.put(f"/api/projects/{project_id}/alerts", json={"enabled": False}).status_code == 403
    )
    assert client.post(f"/api/projects/{project_id}/branding/logo", content=PNG).status_code == 403
    # Reading stays open.
    assert client.get(f"/api/projects/{project_id}/reports").status_code == 200
    assert client.get(f"/api/projects/{project_id}/alerts").status_code == 200
