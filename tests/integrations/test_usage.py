"""Usage ledger: rows, enrichment, filters, context tags, shared instance."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from src.integrations.usage import (
    ApiCall,
    UsageLedger,
    current_usage_context,
    get_usage_ledger,
    usage_context,
)


def test_actual_cost_prefers_vendor_then_modelled_then_estimate():
    base = {"vendor": "openai", "operation": "op", "estimated_cost_usd": 0.03}
    assert ApiCall(**base).actual_cost_usd == 0.03
    assert ApiCall(**base, modelled_cost_usd=0.011).actual_cost_usd == 0.011
    assert (
        ApiCall(**base, modelled_cost_usd=0.011, vendor_cost_usd=0.0098).actual_cost_usd == 0.0098
    )


def test_record_update_and_query(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    now = datetime(2026, 9, 17, 12, tzinfo=UTC)
    a = ApiCall(vendor="openai", operation="responses.create", ts=now, run_id="r1", source="cli")
    b = ApiCall(
        vendor="perplexity",
        operation="v1.responses",
        ts=now + timedelta(minutes=1),
        run_id="r2",
        status="error",
        error="boom",
        source="live_check",
    )
    ledger.record(a)
    ledger.record(b)
    ledger.update(a.id, input_tokens=100, output_tokens=50, vendor_cost_usd=0.01, model="m")
    assert ledger.count() == 2

    rows = ledger.calls()
    assert [r.vendor for r in rows] == ["openai", "perplexity"]
    assert rows[0].input_tokens == 100 and rows[0].vendor_cost_usd == 0.01 and rows[0].model == "m"
    assert rows[1].status == "error" and rows[1].error == "boom"
    assert [r.id for r in ledger.calls(run_ids=["r2"])] == [b.id]
    assert ledger.calls(run_ids=[]) == []
    assert [r.id for r in ledger.calls(since=now + timedelta(seconds=30))] == [b.id]
    assert [r.id for r in ledger.calls(source="cli")] == [a.id]
    assert [r.id for r in ledger.calls(vendor="perplexity")] == [b.id]
    assert len(ledger.calls(limit=1)) == 1


def test_update_rejects_unknown_fields_and_ignores_empty(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    call = ApiCall(vendor="v", operation="o")
    ledger.record(call)
    with pytest.raises(ValueError, match="Unknown ApiCall fields"):
        ledger.update(call.id, bogus=1)
    ledger.update(call.id)  # no-op
    assert ledger.calls()[0].model is None


def test_usage_context_nests_and_resets():
    assert current_usage_context() == {}
    with usage_context(source="cli", run_id="r1"):
        assert current_usage_context() == {"source": "cli", "run_id": "r1"}
        # `None` clears an inherited key (cycle 0013): a keyword-rank lookup nested
        # in a prompt's context must not be charged to that prompt.
        with usage_context(prompt_id="p1", engine="GEMINI", run_id=None):
            assert current_usage_context() == {
                "source": "cli",
                "prompt_id": "p1",
                "engine": "GEMINI",
            }
        assert current_usage_context() == {"source": "cli", "run_id": "r1"}  # restored
    assert current_usage_context() == {}
    with pytest.raises(ValueError, match="Unknown usage context keys"), usage_context(nope="x"):
        pass


def test_context_is_per_thread_unless_copied():
    seen: dict[str, dict[str, str]] = {}

    def worker() -> None:
        seen["thread"] = current_usage_context()

    with usage_context(source="cli"):
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    assert seen["thread"] == {}


def test_get_usage_ledger_is_shared_per_path(settings):
    first = get_usage_ledger(settings)
    second = get_usage_ledger(settings)
    assert first is second
    assert first.path == settings.tracker_db_path
    other = settings.model_copy(
        update={"tracker_db_path": settings.tracker_db_path.parent / "x.db"}
    )
    assert get_usage_ledger(other) is not first


def test_record_survives_a_broken_database(tmp_path, caplog):
    ledger = UsageLedger(tmp_path / "u.sqlite")
    ledger._path = tmp_path / "missing-dir" / "nope.sqlite"  # forces sqlite3.Error
    ledger.record(ApiCall(vendor="v", operation="o"))  # must not raise
    ledger.update("x", model="m")  # must not raise
