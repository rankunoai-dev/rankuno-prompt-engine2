"""Usage tags (source, run, prompt, engine) reach the ledger from pool worker threads."""

from __future__ import annotations

import httpx
import pytest

from src.integrations.openai_search import OpenAISearchClient
from src.integrations.schemas import Engine
from src.integrations.usage import get_usage_ledger, usage_context
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    CustomPrompt,
    PipelineInput,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB
from tests.integrations.test_openai_search import _payload
from tests.modules.prompt_tracking.test_pipeline import LOB, SEED, StubEngine, _pipeline

PROMPTS = [
    CustomPrompt(prompt_text="What is the best procurement software?"),
    CustomPrompt(prompt_text="Is GEP SMART good for source to pay?"),
]


@pytest.fixture
def client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART"],
        domains=["gep.com"],
        competitor_domains=["coupa.com"],
        lob=LOB,
        seed_keywords=[SEED],
    )


def _openai(settings) -> OpenAISearchClient:
    body = _payload()
    body["usage"] = {"input_tokens": 500, "output_tokens": 120}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    return OpenAISearchClient(settings, transport=transport)


def test_worker_threads_record_run_prompt_and_engine(
    permissive_guardrails, ledger, settings, client, tmp_path
):
    engines = {e: StubEngine(e) for e in Engine}
    engines[Engine.CHATGPT_SEARCH] = _openai(settings)
    tool = _pipeline(
        permissive_guardrails,
        ledger,
        settings,
        engines=engines,
        db=TimeSeriesDB(tmp_path / "t.sqlite"),
    )
    with usage_context(source="control_plane"):
        result = tool.run(
            PipelineInput(
                client=client,
                custom_prompts=PROMPTS,
                generate_prompts=False,
                samples_per_engine=2,
                max_workers=2,
                track_keyword_rank=False,
            )
        )
    assert result.ok, result.error
    summary = result.data

    rows = get_usage_ledger(settings).calls(vendor="openai", run_ids=[summary.run_id])
    assert len(rows) == 2 * len(PROMPTS)  # two samples per prompt on the real connector
    assert {r.source for r in rows} == {"control_plane"}
    assert {r.engine for r in rows} == {"CHATGPT_SEARCH"}
    assert {r.prompt_id for r in rows} == {prompt_id_for(LOB, p.prompt_text) for p in PROMPTS}
    assert all(r.status == "ok" and r.input_tokens == 500 for r in rows)
    assert all(r.estimated_cost_usd == settings.cost_openai_search_call_usd for r in rows)
    assert all(r.modelled_cost_usd is not None and r.modelled_cost_usd > 0 for r in rows)


def test_source_defaults_to_pipeline_when_untagged(
    permissive_guardrails, ledger, settings, client, tmp_path
):
    engines = {e: StubEngine(e) for e in Engine}
    engines[Engine.CHATGPT_SEARCH] = _openai(settings)
    tool = _pipeline(
        permissive_guardrails,
        ledger,
        settings,
        engines=engines,
        db=TimeSeriesDB(tmp_path / "t.sqlite"),
    )
    result = tool.run(
        PipelineInput(
            client=client,
            custom_prompts=PROMPTS[:1],
            generate_prompts=False,
            samples_per_engine=1,
            track_keyword_rank=False,
        )
    )
    rows = get_usage_ledger(settings).calls(vendor="openai", run_ids=[result.data.run_id])
    assert rows and {r.source for r in rows} == {"pipeline"}
