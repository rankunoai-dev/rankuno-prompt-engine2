"""Per-project owner credentials: everyone reads, only the holder writes (ADR 0019)."""

from __future__ import annotations

import base64
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from src.core.config import Settings
from src.core.errors import ConfigurationError
from src.modules.control_plane.app import create_app
from src.modules.control_plane.credentials import CredentialRecord, hash_password, verify_password
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.project_access import PROJECT_AUTH_HEADER, AttemptLimiter
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import ProjectCreate, ProjectCredentials
from tests.modules.control_plane.conftest import CLIENT, NOW
from tests.modules.control_plane.test_runner import FakePipeline

OWNER = "gaurav"
PASSWORD = "correct horse battery"  # noqa: S105 - test credential
ADMIN = "recovery-password-0123456789"


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


KEY = {PROJECT_AUTH_HEADER: _basic(OWNER, PASSWORD)}
WRONG = {PROJECT_AUTH_HEADER: _basic(OWNER, "not the password")}


def _client(store, db, settings) -> TestClient:
    runner = ProjectRunner(
        store, db, settings=settings, pipeline=FakePipeline(db=db), clock=lambda: NOW
    )
    jobs = JobManager(runner, store, autostart=False, clock=lambda: NOW)
    return TestClient(create_app(store, db, runner, jobs, settings=lambda: settings))


@pytest.fixture
def client(store, db, settings):
    with _client(store, db, settings) as tc:
        yield tc


def _create(client, *, protected: bool = True) -> dict:
    body: dict = {"name": "GEP procurement", "client": CLIENT}
    if protected:
        body["credentials"] = {"owner": OWNER, "password": PASSWORD}
    res = client.post("/api/projects", json=body)
    assert res.status_code == 201, res.text
    return res.json()


# -- hashing -------------------------------------------------------------------


def test_hash_verifies_only_the_exact_pair_and_salts_every_record():
    record = hash_password(OWNER, PASSWORD)
    assert verify_password(record, OWNER, PASSWORD)
    assert not verify_password(record, OWNER, PASSWORD + "x")
    assert not verify_password(record, "someone-else", PASSWORD)
    assert PASSWORD not in record.model_dump_json()
    assert hash_password(OWNER, PASSWORD).digest != record.digest  # fresh salt each time


@pytest.mark.parametrize("scheme", ["bcrypt-1-2-3", "scrypt-x-8-1", "garbage"])
def test_an_unreadable_record_locks_instead_of_crashing(scheme):
    record = hash_password(OWNER, PASSWORD).model_copy(update={"scheme": scheme})
    assert not verify_password(record, OWNER, PASSWORD)
    broken = CredentialRecord(owner=OWNER, scheme="scrypt-16384-8-1", salt="zz", digest="zz")
    assert not verify_password(broken, OWNER, PASSWORD)


def test_credentials_contract_rejects_weak_or_unencodable_values():
    with pytest.raises(ValidationError):
        ProjectCredentials(owner=OWNER, password=SecretStr("short"))
    with pytest.raises(ValidationError):
        ProjectCredentials(owner="has:colon", password=SecretStr(PASSWORD))
    with pytest.raises(ValidationError):
        ProjectCredentials(owner="", password=SecretStr(PASSWORD))


# -- store ---------------------------------------------------------------------


def test_store_keeps_the_secret_out_of_the_project_payload(store, tmp_path):
    project = store.create_project(
        ProjectCreate(
            name="Locked",
            client=CLIENT,
            credentials=ProjectCredentials(owner=OWNER, password=SecretStr(PASSWORD)),
        )
    )
    assert (project.protected, project.owner) == (True, OWNER)
    assert store.get_project(project.id).protected
    assert store.list_projects()[0].owner == OWNER

    with sqlite3.connect(tmp_path / "cp.sqlite") as conn:
        payload = conn.execute("SELECT payload FROM projects").fetchone()[0]
        digest = conn.execute("SELECT digest FROM project_credentials").fetchone()[0]
    assert "credentials" not in payload and "password" not in payload
    assert PASSWORD not in payload and digest not in payload
    assert "protected" not in payload  # derived on read, so a payload cannot claim to be open

    # an edit keeps the lock, and a delete removes the credential with the project
    assert store.update_project(project.id, _update(name="Renamed")).protected
    store.delete_project(project.id)
    with sqlite3.connect(tmp_path / "cp.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_credentials").fetchone()[0] == 0


def _update(**fields):
    from src.modules.control_plane.schemas import ProjectUpdate

    return ProjectUpdate(**fields)


def test_store_open_project_and_unknown_project(store, project):
    assert (project.protected, project.owner) == (False, None)
    assert store.credential_record(project.id) is None
    with pytest.raises(KeyError):
        store.credential_record("missing-project")
    with pytest.raises(KeyError):
        store.set_credentials("missing-project", hash_password(OWNER, PASSWORD))


# -- limiter -------------------------------------------------------------------


def test_limiter_blocks_after_the_threshold_and_recovers_with_time():
    now = [0.0]
    limiter = AttemptLimiter(max_failures=3, window_seconds=60, clock=lambda: now[0])
    assert limiter.retry_after("p") is None
    for _ in range(3):
        limiter.record_failure("p")
        now[0] += 1
    assert limiter.retry_after("p") == 57
    assert limiter.retry_after("other") is None  # throttling one project never locks another
    now[0] = 61
    assert limiter.retry_after("p") is None
    limiter.record_failure("p")
    limiter.reset("p")
    assert limiter.retry_after("p") is None


# -- API: reads are open, writes need the credential ---------------------------


def test_create_returns_lock_state_and_never_the_secret(client):
    created = _create(client)
    assert created["protected"] is True and created["owner"] == OWNER
    assert "credentials" not in created and PASSWORD not in str(created)
    assert (
        client.post(
            "/api/projects",
            json={
                "name": "Weak",
                "client": CLIENT,
                "credentials": {"owner": "a", "password": "short"},
            },
        ).status_code
        == 422
    )


def test_everyone_reads_a_protected_project_without_any_credential(client):
    pid = _create(client)["id"]
    tracked = client.post(
        f"/api/projects/{pid}/prompts", json={"prompt_text": "best procurement tool?"}, headers=KEY
    ).json()
    for path in (
        "/api/projects",
        f"/api/projects/{pid}",
        f"/api/projects/{pid}/prompts",
        f"/api/projects/{pid}/prompts/{tracked['id']}",
        f"/api/projects/{pid}/results",
        f"/api/projects/{pid}/jobs",
        f"/api/projects/{pid}/positions",
        f"/api/projects/{pid}/crawls",
        f"/api/projects/{pid}/insights",
        f"/api/projects/{pid}/runs",
        f"/api/projects/{pid}/export",
    ):
        res = client.get(path)
        assert res.status_code == 200, path
        assert PASSWORD not in res.text and "digest" not in res.text


def _writes(pid: str, tracked_id: str) -> list[tuple[str, str, dict | None]]:
    return [
        ("PUT", f"/api/projects/{pid}", {"notes": "edited"}),
        ("POST", f"/api/projects/{pid}/prompts", {"prompt_text": "another tracked prompt?"}),
        ("POST", f"/api/projects/{pid}/prompts/import", {"text": "an imported prompt?"}),
        ("PUT", f"/api/projects/{pid}/prompts/{tracked_id}", {"important": True}),
        ("POST", f"/api/projects/{pid}/run", {}),
        ("POST", f"/api/projects/{pid}/consolidate", {}),
        ("PUT", f"/api/projects/{pid}/actions/some-action", {"status": "done"}),
        ("PUT", f"/api/projects/{pid}/credentials", {"owner": "mallory", "password": PASSWORD}),
        ("POST", f"/api/projects/{pid}/crawler-logs/import", {"text": "not a log"}),
        ("DELETE", f"/api/projects/{pid}/crawler-logs/imports/nope", None),
        ("DELETE", f"/api/projects/{pid}/prompts/{tracked_id}", None),
        ("DELETE", f"/api/projects/{pid}", None),
    ]


def test_every_write_is_refused_without_the_credential_and_changes_nothing(client, store):
    pid = _create(client)["id"]
    tracked = client.post(
        f"/api/projects/{pid}/prompts", json={"prompt_text": "best procurement tool?"}, headers=KEY
    ).json()
    before = (store.get_project(pid), store.list_prompts(pid))

    for method, path, body in _writes(pid, tracked["id"]):
        anonymous = client.request(method, path, json=body)
        assert anonymous.status_code == 403, (method, path, anonymous.text)
        assert anonymous.json()["code"] == "project_locked"
        assert anonymous.json()["owner"] == OWNER
        # 403, never 401: a 401 would make the browser drop the site login (ADR 0019)
        assert "www-authenticate" not in anonymous.headers

    wrong = client.put(f"/api/projects/{pid}", json={"notes": "x"}, headers=WRONG)
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "project_credentials_invalid"

    assert (store.get_project(pid), store.list_prompts(pid)) == before
    assert client.get(f"/api/projects/{pid}/jobs").json() == []  # no paid run was queued


def test_the_holder_can_do_every_write(client):
    pid = _create(client)["id"]
    tracked = client.post(
        f"/api/projects/{pid}/prompts", json={"prompt_text": "best procurement tool?"}, headers=KEY
    ).json()
    allowed = {200, 201, 202, 204, 400, 404}  # past the guard; 400/404 are the route's own answer
    for method, path, body in _writes(pid, tracked["id"]):
        if path.endswith("/credentials"):
            continue  # covered by the rotation test; it would change KEY mid-loop
        res = client.request(method, path, json=body, headers=KEY)
        assert res.status_code in allowed, (method, path, res.status_code, res.text)
    assert client.get(f"/api/projects/{pid}").status_code == 404  # the final DELETE went through


def test_unknown_project_is_404_not_403(client):
    assert (
        client.put("/api/projects/nope-nope", json={"notes": "x"}, headers=KEY).status_code == 404
    )
    assert client.get("/api/projects/nope-nope/access").status_code == 404


def test_malformed_header_is_a_refusal_not_an_error(client):
    pid = _create(client)["id"]
    for value in (
        "Bearer abc",
        "Basic !!!not-base64!!!",
        "Basic " + base64.b64encode(b"nocolon").decode(),
    ):
        res = client.put(
            f"/api/projects/{pid}", json={"notes": "x"}, headers={PROJECT_AUTH_HEADER: value}
        )
        assert res.status_code == 403, value


# -- API: access report, claiming and rotating ---------------------------------


def test_access_reports_what_the_presented_credential_may_do(client):
    pid = _create(client)["id"]
    assert client.get(f"/api/projects/{pid}/access").json() == {
        "protected": True,
        "owner": OWNER,
        "can_write": False,
    }
    assert client.get(f"/api/projects/{pid}/access", headers=KEY).json()["can_write"] is True
    assert client.get(f"/api/projects/{pid}/access", headers=WRONG).json()["can_write"] is False


def test_open_project_stays_writable_until_it_is_claimed(client):
    pid = _create(client, protected=False)["id"]
    assert client.get(f"/api/projects/{pid}/access").json() == {
        "protected": False,
        "owner": None,
        "can_write": True,
    }
    assert client.put(f"/api/projects/{pid}", json={"notes": "anyone"}).status_code == 200

    claimed = client.put(
        f"/api/projects/{pid}/credentials", json={"owner": OWNER, "password": PASSWORD}
    )
    assert claimed.status_code == 200 and claimed.json()["protected"] is True
    assert client.get(f"/api/projects/{pid}").json()["owner"] == OWNER
    assert client.put(f"/api/projects/{pid}", json={"notes": "anyone"}).status_code == 403
    assert client.put(f"/api/projects/{pid}", json={"notes": "me"}, headers=KEY).status_code == 200


def test_rotating_the_credential_needs_the_current_one_and_retires_it(client):
    pid = _create(client)["id"]
    new = {"owner": "priya", "password": "a brand new passphrase"}
    assert client.put(f"/api/projects/{pid}/credentials", json=new).status_code == 403
    assert client.put(f"/api/projects/{pid}/credentials", json=new, headers=KEY).status_code == 200

    assert client.put(f"/api/projects/{pid}", json={"notes": "old"}, headers=KEY).status_code == 403
    fresh = {PROJECT_AUTH_HEADER: _basic(new["owner"], new["password"])}
    assert (
        client.put(f"/api/projects/{pid}", json={"notes": "new"}, headers=fresh).status_code == 200
    )
    assert client.get(f"/api/projects/{pid}").json()["owner"] == "priya"


# -- API: throttle, recovery, coexistence with the site login ------------------


def test_repeated_wrong_credentials_are_throttled_even_for_the_right_one(client):
    pid = _create(client)["id"]
    other = _create(client)["id"]
    for _ in range(8):
        assert (
            client.put(f"/api/projects/{pid}", json={"notes": "x"}, headers=WRONG).status_code
            == 403
        )
    blocked = client.put(f"/api/projects/{pid}", json={"notes": "x"}, headers=KEY)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "project_unlock_throttled"
    assert int(blocked.headers["retry-after"]) > 0
    assert client.get(f"/api/projects/{pid}/access", headers=KEY).status_code == 429  # no oracle
    # reads, and other projects, are unaffected
    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert client.put(f"/api/projects/{other}", json={"notes": "x"}, headers=KEY).status_code == 200


def test_recovery_password_unlocks_any_project_only_when_configured(store, db, settings):
    with _client(store, db, settings) as plain:
        pid = _create(plain)["id"]
        as_admin = {PROJECT_AUTH_HEADER: _basic("admin", ADMIN)}
        assert (
            plain.put(f"/api/projects/{pid}", json={"notes": "x"}, headers=as_admin).status_code
            == 403
        )

    recovering = settings.model_copy(update={"project_admin_password": SecretStr(ADMIN)})
    with _client(store, db, recovering) as client:
        assert (
            client.put(f"/api/projects/{pid}", json={"notes": "x"}, headers=as_admin).status_code
            == 200
        )
        reset = client.put(
            f"/api/projects/{pid}/credentials",
            json={"owner": "new-owner", "password": "replacement passphrase"},
            headers=as_admin,
        )
        assert reset.status_code == 200 and reset.json()["owner"] == "new-owner"


def test_recovery_password_must_be_long_or_blank(tmp_path):
    base = {"_env_file": None, "tracker_db_path": tmp_path / "x"}
    with pytest.raises(ConfigurationError, match="PROJECT_ADMIN_PASSWORD"):
        Settings(**base, project_admin_password="too-short")
    assert Settings(**base, project_admin_password="").project_admin_secret is None
    assert Settings(**base).project_admin_secret is None
    assert Settings(**base, project_admin_password=ADMIN).project_admin_secret == ADMIN


def test_site_login_and_project_credential_travel_in_separate_headers(store, db, settings):
    guarded = settings.model_copy(
        update={"control_plane_user": "team", "control_plane_password": SecretStr("site-pass")}
    )
    site = {"Authorization": _basic("team", "site-pass")}
    with _client(store, db, guarded) as client:
        assert client.post("/api/projects", json={"name": "x", "client": CLIENT}).status_code == 401
        created = client.post(
            "/api/projects",
            json={
                "name": "Shared",
                "client": CLIENT,
                "credentials": {"owner": OWNER, "password": PASSWORD},
            },
            headers=site,
        )
        pid = created.json()["id"]
        # the site login alone reads but cannot write; it is not the project credential
        assert client.get(f"/api/projects/{pid}", headers=site).status_code == 200
        assert (
            client.put(f"/api/projects/{pid}", json={"notes": "x"}, headers=site).status_code == 403
        )
        # nor does the project credential replace the site login
        assert (
            client.put(f"/api/projects/{pid}", json={"notes": "x"}, headers=KEY).status_code == 401
        )
        both = client.put(f"/api/projects/{pid}", json={"notes": "x"}, headers={**site, **KEY})
        assert both.status_code == 200
