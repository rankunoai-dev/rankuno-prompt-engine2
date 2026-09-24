"""Storage, retention and the generation worker, with no vendor and no SMTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.branding import Brand
from src.core.config import Settings
from src.core.errors import IntegrationError
from src.modules.reporting.schemas import NarrativeSource, ReportRequest, ReportState
from src.modules.reporting.store import ReportStore
from src.modules.reporting.worker import QueueFull, ReportInputs, ReportWorker

# A real 4x2 PNG: Pillow verifies the CRC, so a hand-written header fails.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000040000000208060000007fa87d63"
    "000000134944415478da6394b78affcf80049818d00000350801bb4db3d45e0000"
    "000049454e44ae426082"
)


@pytest.fixture
def store(tmp_path) -> ReportStore:
    return ReportStore(tmp_path / "cp.sqlite", tmp_path / "reports")


def _inputs(project, insights, positions, previous_positions) -> ReportInputs:
    return ReportInputs(
        project=project,
        insights=insights,
        positions=positions,
        previous_positions=previous_positions,
    )


def _worker(store, inputs, **kwargs) -> ReportWorker:
    settings = kwargs.pop("settings", Settings(anthropic_api_key=None))
    return ReportWorker(
        store, lambda _pid, _cid: inputs, settings=settings, autostart=False, **kwargs
    )


class FakeMailer:
    """Records sends instead of making them."""

    def __init__(self, *, configured: bool = True, error: Exception | None = None) -> None:
        self.configured = configured
        self.error = error
        self.sent: list[dict[str, object]] = []

    def send(self, **kwargs: object) -> int:
        if self.error:
            raise self.error
        self.sent.append(kwargs)
        recipients = kwargs.get("to")
        return len(recipients) if isinstance(recipients, list) else 0


def test_a_report_runs_end_to_end_and_lands_on_disk(
    store, project, insights, positions, previous_positions
):
    """Queued to done, with a file, a page count and the narrative's provenance."""
    worker = _worker(store, _inputs(project, insights, positions, previous_positions))
    record = store.create(project.id, ReportRequest(), project.brand, "September 2026")
    assert record.state is ReportState.QUEUED

    done = worker.generate(record)
    assert done.state is ReportState.DONE
    assert done.pages and done.pages >= 3
    assert done.size_bytes and done.size_bytes > 1000
    assert done.narrative_source is NarrativeSource.TEMPLATE  # no key configured
    assert done.spend_usd == 0.0
    assert done.window_label.endswith("3 crawl(s)")
    path = store.file_path(done)
    assert path is not None and path.exists()
    assert path.read_bytes()[:4] == b"%PDF"
    assert store.get(done.id) is not None and store.list_for(project.id)[0].id == done.id


def test_a_loader_failure_marks_the_row_and_keeps_the_worker_alive(store, project):
    """A broken window is a failed report, not a dead thread."""

    def explode(_pid: str, _cid: str | None) -> ReportInputs:
        msg = "no consolidation"
        raise KeyError(msg)

    worker = ReportWorker(
        store, explode, settings=Settings(anthropic_api_key=None), autostart=False
    )
    record = store.create(project.id, ReportRequest(), project.brand, "t")
    failed = worker.generate(record)
    assert failed.state is ReportState.FAILED
    assert failed.error is not None and "no consolidation" in failed.error
    assert failed.file_name is None


def test_email_delivery_is_optional_and_a_failure_never_loses_the_report(
    store, project, insights, positions, previous_positions
):
    """The PDF exists whether or not the mail server cooperated."""
    inputs = _inputs(project, insights, positions, previous_positions)
    mailer = FakeMailer()
    worker = _worker(store, inputs, mailer=mailer)
    record = store.create(
        project.id, ReportRequest(email_to=["priya@client.com"]), project.brand, "t"
    )
    done = worker.generate(record)
    assert done.state is ReportState.DONE
    assert done.emailed_to == 1
    assert mailer.sent and mailer.sent[0]["to"] == ["priya@client.com"]

    broken = _worker(store, inputs, mailer=FakeMailer(error=IntegrationError("smtp", "refused")))
    second = broken.generate(
        store.create(project.id, ReportRequest(email_to=["priya@client.com"]), project.brand, "t")
    )
    assert second.state is ReportState.DONE
    assert second.emailed_to == 0
    assert second.error is not None and "email failed" in second.error

    unconfigured = _worker(store, inputs, mailer=FakeMailer(configured=False))
    third = unconfigured.generate(
        store.create(project.id, ReportRequest(email_to=["priya@client.com"]), project.brand, "t")
    )
    assert third.state is ReportState.DONE
    assert third.emailed_to == 0
    assert third.error is not None and "SMTP is not configured" in third.error


def test_the_queue_is_bounded(store, project, insights, positions, previous_positions):
    """Twenty waiting reports is already pathological; the twenty-first is refused."""
    worker = _worker(store, _inputs(project, insights, positions, previous_positions))
    for _ in range(20):
        worker.submit(store.create(project.id, ReportRequest(), project.brand, "t"))
    with pytest.raises(QueueFull):
        worker.submit(store.create(project.id, ReportRequest(), project.brand, "t"))


def test_a_logo_is_stored_under_a_minted_name_and_nothing_else_resolves(store):
    """A caller-supplied path must not reach the filesystem."""
    logo_id = store.save_logo(PNG, "png")
    assert store.logo_path(logo_id).exists()
    for hostile in ("../../etc/passwd", "a.png/../../x", "..\\windows\\win.ini", "x.svg"):
        with pytest.raises(ValueError, match="logo id"):
            store.logo_path(hostile)


def test_a_brand_logo_is_drawn_when_present_and_skipped_when_missing(
    store, project, insights, positions, previous_positions
):
    """A deleted logo file costs the cover its image, never the report."""
    logo_id = store.save_logo(PNG, "png")
    brand = Brand(logo_id=logo_id, agency_name="RankUno")
    inputs = _inputs(project, insights, positions, previous_positions)
    worker = _worker(store, inputs)
    with_logo = worker.generate(store.create(project.id, ReportRequest(), brand, "t"))
    assert with_logo.state is ReportState.DONE

    store.logo_path(logo_id).unlink()
    without = worker.generate(store.create(project.id, ReportRequest(), brand, "t"))
    assert without.state is ReportState.DONE


def test_retention_removes_the_file_and_keeps_the_row(
    store, project, insights, positions, previous_positions
):
    """'What did you send me in March' still has an answer after the purge."""
    worker = _worker(store, _inputs(project, insights, positions, previous_positions))
    done = worker.generate(store.create(project.id, ReportRequest(), project.brand, "t"))
    old = done.model_copy(update={"created_at": datetime.now(UTC) - timedelta(days=500)})
    store.save(old)
    # `save` does not move created_at, so write it directly for the test.
    import sqlite3

    with sqlite3.connect(store._path) as conn:  # noqa: SLF001 - fixing the row's age
        conn.execute(
            "UPDATE report_runs SET created_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(days=500)).isoformat(), done.id),
        )

    assert store.purge(400) == 1
    after = store.get(done.id)
    assert after is not None
    assert after.purged_at is not None
    assert after.downloadable is False
    assert not (store.file_path(after) or store.file_path(done)).exists()
    assert store.purge(400) == 0  # already purged rows are skipped


def test_deleting_a_report_and_a_project_removes_the_artefacts(
    store, project, insights, positions, previous_positions
):
    """Nothing is left on the volume after a delete."""
    worker = _worker(store, _inputs(project, insights, positions, previous_positions))
    first = worker.generate(store.create(project.id, ReportRequest(), project.brand, "t"))
    second = worker.generate(store.create(project.id, ReportRequest(), project.brand, "t"))
    assert store.delete(first.id) is True
    assert store.get(first.id) is None
    assert store.delete("nosuchid0") is False

    path = store.file_path(second)
    store.delete_project(project.id)
    assert store.list_for(project.id) == []
    assert path is not None and not path.exists()
