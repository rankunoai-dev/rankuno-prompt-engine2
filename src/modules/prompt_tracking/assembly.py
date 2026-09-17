"""Turns audit outcomes into master-sheet records and audit-trail rows.

Kept apart from the pipeline so the orchestration file stays readable and these
pure functions can be tested without a thread pool or a store.
"""

from __future__ import annotations

from typing import Final

from src.integrations.schemas import Engine, EngineAnswer
from src.modules.prompt_tracking.citations import client_rank
from src.modules.prompt_tracking.mentions import CLIENT, detect_mentions
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    CitationSnapshot,
    ClientProfile,
    MasterPromptRecord,
    OrganicRankSnapshot,
    PromptCandidate,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB
from src.modules.prompt_tracking.url_mapper import UrlMapper

__all__ = ["answer_samples", "build_record", "model_shifts"]

_EXCERPT_CHARS: Final = 300


def build_record(
    candidate: PromptCandidate,
    snapshots: list[CitationSnapshot],
    organic: list[OrganicRankSnapshot],
    lob: str,
    mapper: UrlMapper,
) -> MasterPromptRecord:
    """Assemble the master-sheet row for one prompt, including its verdict."""
    mapping = mapper.map(candidate)
    web_triggers = any(s.web_trigger_rate > 0 for s in snapshots)
    if not snapshots:
        verdict, reason = Verdict.KEEP, "Selected by intent gate; engine audit not run."
    elif web_triggers:
        cited = any(s.client_cited for s in snapshots)
        verdict = Verdict.KEEP
        reason = (
            "Web-grounded on at least one engine; client cited."
            if cited
            else "Web-grounded on at least one engine; client not cited — visibility gap."
        )
    elif candidate.search_volume > 0:
        verdict, reason = Verdict.KEEP, "No engine searched the web, but keyword has demand."
    else:
        verdict, reason = Verdict.DROP, "No web trigger on any engine and no search volume."

    return MasterPromptRecord(
        prompt_id=prompt_id_for(lob, candidate.prompt_text),
        lob=lob,
        subtopic=candidate.subtopic,
        core_keyword=candidate.core_keyword,
        search_volume=candidate.search_volume,
        prompt_text=candidate.prompt_text,
        search_intent=candidate.search_intent,
        decision_stage=candidate.decision_stage,
        prompt_type=candidate.prompt_type,
        web_triggers=web_triggers,
        citation_history=snapshots,
        organic_history=organic,
        mapped_url=mapping.mapped_url,
        content_gap=mapping.content_gap,
        verdict=verdict,
        verdict_reason=reason,
    )


def answer_samples(
    answers: list[EngineAnswer], client: ClientProfile, candidate: PromptCandidate
) -> list[AnswerSample]:
    """Raw per-answer rows for the model-shift audit trail."""
    pid = prompt_id_for(client.lob, candidate.prompt_text)
    rows: list[AnswerSample] = []
    for answer in answers:
        rank = client_rank(answer, client.domains)
        mentions = detect_mentions(answer.answer_text, client)
        rows.append(
            AnswerSample(
                prompt_id=pid,
                engine=answer.engine,
                model=answer.model,
                captured_at=answer.captured_at,
                response_id=answer.response_id,
                web_triggered=answer.web_triggered,
                client_cited=rank is not None,
                client_rank=rank,
                cited_domains=answer.cited_domains,
                citation_links=answer.citations,
                consulted_urls=answer.consulted_urls,
                mention_detected=any(m.entity == CLIENT for m in mentions),
                mentions=mentions,
                answer_excerpt=answer.answer_text[:_EXCERPT_CHARS],
                answer_text=answer.answer_text,
                search_queries=answer.search_queries,
                citation_claims=answer.citation_claims,
                source_snippets=answer.source_snippets,
            )
        )
    return rows


def model_shifts(db: TimeSeriesDB, records: list[MasterPromptRecord], run_id: str) -> list[str]:
    """Engines whose model string differs from the previous run's.

    A drop in citations that coincides with a vendor model change is a
    different problem from a drop caused by the client's content; this is how
    the two are told apart.
    """
    seen: dict[Engine, str] = {}
    for record in records:
        for snap in record.citation_history:
            if not snap.reused and snap.model != "unavailable":
                seen.setdefault(snap.engine, snap.model)
    shifts: list[str] = []
    for engine, model in seen.items():
        previous = db.last_model(engine, exclude_run_id=run_id)
        if previous and previous != model:
            shifts.append(f"{engine.value}: {previous} -> {model}")
    return shifts
