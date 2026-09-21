"""Seed a demonstration project that exercises every capability of the engine.

Writes through the engine's own stores (no API mocking): a project with 20
tracked prompts, 30 crawls at a two-day interval over 60 days, three samples
per prompt and platform with full answer text, citation links, claims, source
snippets, fan-out queries, consulted URLs and mention snippets; organic ranks
with PAA and related searches; run headers; usage-ledger rows; crawl records;
consolidations computed by the engine after every third crawl; and analyst
action states with baselines so outcomes score after the next consolidation.

The scenario is designed so each of the eight action-card rules fires on real
stored data. Everything is invented and the project notes say so.

Usage (from the repository root, with the venv python):
    python scripts/seed_demo_project.py [--reset] [--db data/prompt_tracker.sqlite]
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import seed_demo_content as C  # noqa: E402

from src.core.config import get_settings  # noqa: E402
from src.integrations.schemas import (  # noqa: E402
    Citation,
    CitationClaim,
    Engine,
    OrganicResult,
    SourceSnippet,  # noqa: E402
)
from src.integrations.usage import ApiCall, UsageLedger  # noqa: E402
from src.modules.control_plane.actions import ActionStateStore  # noqa: E402
from src.modules.control_plane.insights import InsightEngine  # noqa: E402
from src.modules.control_plane.positioning import PositionStore  # noqa: E402
from src.modules.control_plane.schemas import (  # noqa: E402
    ProjectCreate,
    ProjectRunRecord,
    TrackedPromptCreate,
)
from src.modules.control_plane.store import ProjectStore  # noqa: E402
from src.modules.prompt_tracking.schemas import (  # noqa: E402
    AnswerSample,
    CitationSnapshot,
    ClientProfile,
    DecisionStage,
    MasterPromptRecord,
    MentionSnippet,
    OrganicRankSnapshot,
    PromptType,
    RankQueryKind,
    SearchIntent,
    TrackerRunSummary,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB  # noqa: E402

RUNS = 30
INTERVAL_DAYS = 2
WINDOW = 3
MODELS = {
    Engine.CHATGPT_SEARCH: "gpt-4o-mini-2024-07-18",
    Engine.PERPLEXITY: "perplexity/sonar",
    Engine.GEMINI: "gemini-3.6-flash",
    Engine.GOOGLE_AI_OVERVIEW: "google_ai_overview",
}
VENDOR = {
    Engine.CHATGPT_SEARCH: ("openai", "responses"),
    Engine.PERPLEXITY: ("perplexity", "responses"),
    Engine.GEMINI: ("gemini", "generate_content"),
    Engine.GOOGLE_AI_OVERVIEW: ("serpapi", "search"),
}
AGGREGATOR_HOSTS = ("g2.com", "capterra.com", "gartner.com", "trustradius.com", "reddit.com")


def cited_probability(subtopic: str, engine: Engine, t: float) -> float:
    """Chance a sample links the client; `t` runs 0 -> 1 across the two months."""
    table: dict[str, dict[Engine, tuple[float, float]]] = {
        "Source-to-Pay": {
            Engine.CHATGPT_SEARCH: (0.55, 0.85),
            Engine.PERPLEXITY: (0.6, 0.9),
            Engine.GEMINI: (0.7, 0.7),
            Engine.GOOGLE_AI_OVERVIEW: (0.35, 0.5),
        },
        "Spend Analysis": {
            Engine.CHATGPT_SEARCH: (0.02, 0.03),
            Engine.PERPLEXITY: (0.3, 0.55),
            Engine.GEMINI: (0.3, 0.35),
            Engine.GOOGLE_AI_OVERVIEW: (0.2, 0.25),
        },
        "Procure-to-Pay": {
            Engine.CHATGPT_SEARCH: (0.3, 0.4),
            Engine.PERPLEXITY: (0.12, 0.18),
            Engine.GEMINI: (0.35, 0.4),
            Engine.GOOGLE_AI_OVERVIEW: (0.2, 0.3),
        },
        "Supplier Management": {
            Engine.CHATGPT_SEARCH: (0.4, 0.6),
            Engine.PERPLEXITY: (0.45, 0.7),
            Engine.GEMINI: (0.4, 0.55),
            Engine.GOOGLE_AI_OVERVIEW: (0.04, 0.08),
        },
        "Reviews & comparisons": {
            Engine.CHATGPT_SEARCH: (0.3, 0.4),
            Engine.PERPLEXITY: (0.05, 0.45),
            Engine.GEMINI: (0.4, 0.45),
            Engine.GOOGLE_AI_OVERVIEW: (0.3, 0.35),
        },
        "Procurement AI": {
            Engine.CHATGPT_SEARCH: (0.25, 0.45),
            Engine.PERPLEXITY: (0.3, 0.6),
            Engine.GEMINI: (0.3, 0.5),
            Engine.GOOGLE_AI_OVERVIEW: (0.2, 0.3),
        },
        "Implementation & pricing": {
            Engine.CHATGPT_SEARCH: (0.8, 0.9),
            Engine.PERPLEXITY: (0.8, 0.95),
            Engine.GEMINI: (0.75, 0.85),
            Engine.GOOGLE_AI_OVERVIEW: (0.6, 0.7),
        },
    }
    start, end = table[subtopic][engine]
    p = start + (end - start) * t
    if subtopic == "Source-to-Pay" and engine is Engine.GEMINI and t > 0.86:
        p = 0.2  # a late drop: the "defend" card
    return p


def mention_probability(subtopic: str, engine: Engine, cited: bool) -> float:
    """Chance the brand is named in the text (higher when it is also linked)."""
    if subtopic == "Spend Analysis" and engine is Engine.CHATGPT_SEARCH:
        return 0.9  # named but not linked: the "convert mention" card
    return 0.9 if cited else 0.35


def competitor_for(subtopic: str, engine: Engine, rng: random.Random) -> str | None:
    """Competitor domain the answer leans on, if any."""
    if subtopic == "Procure-to-Pay" and engine is Engine.PERPLEXITY:
        return "coupa.com" if rng.random() < 0.65 else None
    if engine is Engine.GOOGLE_AI_OVERVIEW:
        return "coupa.com" if rng.random() < 0.5 else None  # the AI Overview "losing to" verdict
    if rng.random() < 0.35:
        return rng.choice(C.COMPETITORS)
    return None


def build_answer(
    rng: random.Random,
    subtopic: str,
    prompt_type: str,
    cited: bool,
    mention: bool,
    competitor: str | None,
) -> tuple[str, list[MentionSnippet]]:
    """Compose the answer text and the mention snippets the engine would detect."""
    parts = [rng.choice(C.OPENERS[subtopic])]
    mentions: list[MentionSnippet] = []
    if mention or cited:
        sentence = rng.choice(C.CLIENT_SENTENCES[subtopic])
        parts.append(sentence)
        term = "GEP SMART" if "GEP SMART" in sentence else "GEP"
        mentions.append(MentionSnippet(entity="client", term=term, snippet=sentence))
    if competitor:
        name = C.COMPETITOR_PAGES[competitor][2]
        sentence = C.COMPETITOR_SENTENCES[name]
        parts.append(sentence)
        mentions.append(MentionSnippet(entity=name, term=name.split()[0], snippet=sentence))
    if prompt_type == "BRANDED" and not (mention or cited):
        parts.append(
            "Several suites cover this need; the best fit depends on the existing ERP landscape."
        )
    parts.append(rng.choice(C.CLOSERS))
    return " ".join(parts), mentions


def build_sample(  # noqa: PLR0913 - one sample needs the whole scenario
    rng: random.Random,
    prompt: tuple[str, str, str, int, str, str, str, str | None],
    prompt_id: str,
    engine: Engine,
    t: float,
    captured_at: datetime,
) -> AnswerSample:
    """One raw engine answer with everything the connectors capture."""
    _, subtopic, _, _, _, _, ptype, page_key = prompt
    cited = rng.random() < cited_probability(subtopic, engine, t)
    mention = rng.random() < mention_probability(subtopic, engine, cited)
    competitor = competitor_for(subtopic, engine, rng)
    text, mentions = build_answer(rng, subtopic, ptype, cited, mention, competitor)

    links: list[tuple[str, str]] = list(C.THIRD_PARTY_PAGES[subtopic])
    if subtopic == "Reviews & comparisons" and engine is Engine.PERPLEXITY:
        links = links + [links[0], links[1]]  # review sites dominate this engine
    rng.shuffle(links)
    links = links[: rng.randint(3, 4)]
    if competitor:
        url, title, _ = C.COMPETITOR_PAGES[competitor]
        links.insert(rng.randint(0, 1), (url, title))
    client_url = C.CLIENT_PAGES.get(page_key or "Source-to-Pay")
    client_rank: int | None = None
    if cited:
        client_rank = rng.randint(1, 2) if ptype == "BRANDED" else rng.randint(1, 5)
        client_rank = min(client_rank, len(links) + 1)
        links.insert(client_rank - 1, (client_url, "GEP SMART | " + subtopic))
    seen: dict[str, str] = {}
    for url, title in links:
        seen.setdefault(url, title)
    citations = [
        Citation(url=url, domain=_domain(url), title=title, position=i + 1)
        for i, (url, title) in enumerate(seen.items())
    ]
    cited_domains = list(dict.fromkeys(c.domain for c in citations))

    consulted: list[str] = []
    if engine is Engine.CHATGPT_SEARCH:
        consulted = [c.url for c in citations[:2]] + [
            "https://www.techtarget.com/searcherp/definition/procurement"
        ]
        if not cited and subtopic == "Procurement AI" and rng.random() < 0.6:
            consulted.append(C.REJECTED_CLIENT_PAGE)  # read but not cited

    sentences = [s for s in text.split(". ") if s]
    claims = [
        CitationClaim(
            url=c.url,
            sentence=(sentences[min(i, len(sentences) - 1)].rstrip(".") + ".")[:600],
            start=max(0, text.find(sentences[min(i, len(sentences) - 1)])),
            end=max(0, text.find(sentences[min(i, len(sentences) - 1)]))
            + len(sentences[min(i, len(sentences) - 1)]),
        )
        for i, c in enumerate(citations[:3])
    ]
    snippets: list[SourceSnippet] = []
    if engine in (Engine.PERPLEXITY, Engine.GEMINI):
        for c in citations:
            is_client = c.domain in C.CLIENT_DOMAINS
            date = "2023-02-14" if is_client else f"2026-0{rng.randint(6, 8)}-{rng.randint(10, 28)}"
            snippets.append(
                SourceSnippet(
                    url=c.url,
                    title=c.title,
                    snippet=f"{(c.title or c.domain)}: {rng.choice(C.OPENERS[subtopic])[:160]}",
                    date=date,
                )
            )
    return AnswerSample(
        prompt_id=prompt_id,
        engine=engine,
        model=MODELS[engine],
        captured_at=captured_at,
        response_id=f"resp_{uuid.uuid4().hex[:20]}",
        web_triggered=True,
        client_cited=cited,
        client_rank=client_rank,
        cited_domains=cited_domains,
        citation_links=citations,
        consulted_urls=consulted,
        mention_detected=any(m.entity == "client" for m in mentions),
        mentions=mentions,
        answer_excerpt=text[:300],
        answer_text=text,
        search_queries=rng.sample(C.FANOUT[subtopic], k=2),
        citation_claims=claims,
        source_snippets=snippets,
    )


def build_snapshot(samples: list[AnswerSample], failed: int) -> CitationSnapshot:
    """Fold the stored samples the way `citations.build_snapshot` does."""
    first = samples[0]
    ok = len(samples)
    cited = [s for s in samples if s.client_cited]
    ranks = [s.client_rank for s in cited if s.client_rank]
    domain_counts: dict[str, int] = {}
    best: dict[str, Citation] = {}
    for s in samples:
        for d in dict.fromkeys(s.cited_domains):
            domain_counts[d] = domain_counts.get(d, 0) + 1
        for c in s.citation_links:
            if c.url not in best or c.position < best[c.url].position:
                best[c.url] = c
    links = sorted(best.values(), key=lambda c: c.position)
    competitors = {
        c.domain: c.position
        for c in links
        if c.domain in C.COMPETITORS
        and c.position <= min((x.position for x in links if x.domain == c.domain), default=99)
    }
    mention_samples = [s for s in samples if s.mention_detected]
    snippets = list(
        {m.snippet: m for s in mention_samples for m in s.mentions if m.entity == "client"}.values()
    )[:10]
    comp_mentions: dict[str, int] = {}
    for s in samples:
        for name in {m.entity for m in s.mentions if m.entity != "client"}:
            comp_mentions[name] = comp_mentions.get(name, 0) + 1
    return CitationSnapshot(
        engine=first.engine,
        model=first.model,
        captured_at=samples[-1].captured_at,
        samples=ok + failed,
        failed_samples=failed,
        web_trigger_rate=1.0 if first.engine is not Engine.GOOGLE_AI_OVERVIEW else 0.9,
        client_cited_samples=len(cited),
        client_citation_rate=round(len(cited) / ok, 4),
        client_cited=len(cited) >= ok / 2,
        client_best_rank=min(ranks) if ranks else None,
        client_mean_rank=round(sum(ranks) / len(ranks), 2) if ranks else None,
        cited_domains=[d for d, _ in sorted(domain_counts.items(), key=lambda kv: -kv[1])],
        competitor_citations=competitors,
        answer_excerpt=first.answer_excerpt,
        response_ids=[s.response_id or "" for s in samples],
        citation_links=links,
        client_urls=[c.url for c in links if c.domain in C.CLIENT_DOMAINS],
        consulted_urls=sorted({u for s in samples for u in s.consulted_urls}),
        mention_detected=bool(mention_samples),
        mention_rate=round(len(mention_samples) / ok, 4),
        mention_snippets=snippets,
        competitor_mentions=comp_mentions,
    )


def build_organic(
    rng: random.Random,
    prompt: tuple[str, str, str, int, str, str, str, str | None],
    kind: RankQueryKind,
    captured_at: datetime,
) -> OrganicRankSnapshot:
    """Google organic top-10 for the prompt text or its keyword."""
    text, subtopic, keyword, _, _, _, ptype, page_key = prompt
    pages = [(u, t) for u, t in C.THIRD_PARTY_PAGES[subtopic]]
    pages += [
        (C.COMPETITOR_PAGES[d][0], C.COMPETITOR_PAGES[d][1]) for d in rng.sample(C.COMPETITORS, 3)
    ]
    rng.shuffle(pages)
    client_pos: int | None
    if subtopic == "Supplier Management":
        client_pos = 3  # ranks organically, absent from the AI Overview
    elif ptype == "BRANDED":
        client_pos = 1
    else:
        client_pos = rng.choice([None, 6, 8, 11])
    client_url = C.CLIENT_PAGES.get(page_key or "Source-to-Pay")
    if client_pos and client_pos <= 10:
        pages.insert(client_pos - 1, (client_url, "GEP SMART | " + subtopic))
    results = [
        OrganicResult(
            position=i + 1,
            url=u,
            domain=_domain(u),
            title=t,
            snippet=rng.choice(C.OPENERS[subtopic])[:180],
        )
        for i, (u, t) in enumerate(pages[:10])
    ]
    return OrganicRankSnapshot(
        query=text if kind is RankQueryKind.PROMPT else keyword,
        query_kind=kind,
        captured_at=captured_at,
        samples=1,
        client_position=client_pos if client_pos and client_pos <= 10 else None,
        client_url=client_url if client_pos and client_pos <= 10 else None,
        top_domains=[r.domain for r in results],
        competitor_positions={r.domain: r.position for r in results if r.domain in C.COMPETITORS},
        organic_results=results,
        paa_questions=C.PAA[subtopic],
        related_searches=C.RELATED[subtopic],
    )


def ledger_row(  # noqa: PLR0913
    rng: random.Random,
    engine: Engine,
    run_id: str,
    prompt_id: str,
    ts: datetime,
    estimate: float,
) -> ApiCall:
    """One usage-ledger row per sample, shaped like the connectors record."""
    vendor, operation = VENDOR[engine]
    tokens_in, tokens_out = rng.randint(900, 1800), rng.randint(350, 700)
    modelled = round(tokens_in * 1.5e-7 + tokens_out * 6e-7 + 0.01, 5)
    return ApiCall(
        ts=ts,
        vendor=vendor,
        operation=operation,
        source="demo",  # keeps invented rows apart from real spend in the cost report
        run_id=run_id,
        prompt_id=prompt_id,
        engine=engine.value,
        model=MODELS[engine],
        latency_ms=rng.uniform(2500, 9000),
        input_tokens=tokens_in if vendor != "serpapi" else None,
        output_tokens=tokens_out if vendor != "serpapi" else None,
        search_calls=1,
        estimated_cost_usd=estimate,
        vendor_cost_usd=round(modelled * 0.9, 5) if vendor == "perplexity" else None,
        modelled_cost_usd=modelled if vendor != "perplexity" else None,
    )


def _domain(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return host.removeprefix("www.")


def reset(conn_path: Path, store: ProjectStore) -> None:
    """Remove a previous demo project and every row it seeded."""
    for project in store.list_projects():
        if project.name == C.PROJECT_NAME:
            conn = sqlite3.connect(conn_path)
            try:
                conn.execute("DELETE FROM action_states WHERE project_id = ?", (project.id,))
                ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM consolidations WHERE project_id = ?", (project.id,)
                    )
                ]
                for cid in ids:
                    conn.execute("DELETE FROM positions WHERE consolidation_id = ?", (cid,))
                conn.execute("DELETE FROM consolidations WHERE project_id = ?", (project.id,))
                conn.execute("DELETE FROM project_runs WHERE project_id = ?", (project.id,))
                conn.commit()
            finally:
                conn.close()
            store.delete_project(project.id)
    conn = sqlite3.connect(conn_path)
    try:
        pids = [r[0] for r in conn.execute("SELECT prompt_id FROM prompts WHERE lob = ?", (C.LOB,))]
        runs = [r[0] for r in conn.execute("SELECT run_id FROM runs WHERE lob = ?", (C.LOB,))]
        for pid in pids:
            for table in ("snapshots", "answer_samples", "organic_snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE prompt_id = ?", (pid,))  # noqa: S608
        conn.execute("DELETE FROM prompts WHERE lob = ?", (C.LOB,))
        for run_id in runs:
            conn.execute("DELETE FROM api_calls WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE lob = ?", (C.LOB,))
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Seed the demo project and print what was written."""
    parser = argparse.ArgumentParser(description="Seed the full-capability demo project.")
    parser.add_argument("--db", type=Path, default=None, help="SQLite path (default: settings).")
    parser.add_argument("--reset", action="store_true", help="Remove a previous demo first.")
    parser.add_argument("--seed", type=int, default=20260918, help="Random seed.")
    args = parser.parse_args(argv)
    settings = get_settings()
    db_path = args.db or settings.tracker_db_path
    rng = random.Random(args.seed)  # noqa: S311 - reproducible demo data, not security

    db = TimeSeriesDB(db_path)
    store = ProjectStore(db_path)
    positions = PositionStore(db_path)
    actions = ActionStateStore(db_path)
    ledger = UsageLedger(db_path)
    if args.reset:
        reset(db_path, store)
    if any(p.name == C.PROJECT_NAME for p in store.list_projects()):
        print("Demo project already exists; use --reset to rebuild it.")
        return 1

    estimates = {
        Engine.CHATGPT_SEARCH: settings.cost_openai_search_call_usd,
        Engine.PERPLEXITY: settings.cost_perplexity_call_usd,
        Engine.GEMINI: settings.cost_gemini_grounded_call_usd,
        Engine.GOOGLE_AI_OVERVIEW: settings.cost_serpapi_call_usd,
    }
    project = store.create_project(
        ProjectCreate(
            name=C.PROJECT_NAME,
            client=ClientProfile(
                brand_name=C.BRAND,
                aliases=list(C.ALIASES),
                domains=list(C.CLIENT_DOMAINS),
                competitor_domains=list(C.COMPETITORS),
                competitor_names=list(C.COMPETITOR_NAMES),
                lob=C.LOB,
                seed_keywords=list(C.SEED_KEYWORDS),
                subtopics=sorted({p[1] for p in C.PROMPTS}),
                landing_pages=list(C.CLIENT_PAGES.values()),
            ),
            engines=list(Engine),
            interval=f"{INTERVAL_DAYS}d",
            samples_per_engine=3,
            consolidation_runs=WINDOW,
            notes=(
                "DEMONSTRATION DATA. Seeded by scripts/seed_demo_project.py: 20 prompts, "
                f"{RUNS} crawls every {INTERVAL_DAYS} days, consolidated every {WINDOW} crawls. "
                "No vendor was called; every answer, link and cost is invented to show what "
                "the engine records and derives."
            ),
        )
    )
    tracked = [
        store.add_prompt(
            project.id,
            TrackedPromptCreate(
                prompt_text=p[0],
                keyword=p[2],
                subtopic=p[1],
                important=p[6] == "BRANDED" and i % 3 == 0,
            ),
        )
        for i, p in enumerate(C.PROMPTS)
    ]
    ids = {p[0]: prompt_id_for(C.LOB, p[0]) for p in C.PROMPTS}

    end = datetime.now(UTC).replace(hour=6, minute=0, second=0, microsecond=0) - timedelta(days=1)
    total_calls = 0
    for run_index in range(RUNS):
        t = run_index / (RUNS - 1)
        started = end - timedelta(days=(RUNS - 1 - run_index) * INTERVAL_DAYS)
        run_id = uuid.uuid4().hex[:16]
        calls = failed_calls = 0
        cost = 0.0
        for order, prompt in enumerate(C.PROMPTS):
            pid = ids[prompt[0]]
            record = MasterPromptRecord(
                prompt_id=pid,
                lob=C.LOB,
                subtopic=prompt[1],
                core_keyword=prompt[2],
                search_volume=prompt[3],
                prompt_text=prompt[0],
                search_intent=SearchIntent(prompt[4]),
                decision_stage=DecisionStage(prompt[5]),
                prompt_type=PromptType(prompt[6]),
                web_triggers=True,
                mapped_url=C.CLIENT_PAGES.get(prompt[7]) if prompt[7] else None,
                content_gap=prompt[7] is None,
                verdict=Verdict.KEEP,
                verdict_reason="Commercially relevant; tracked on every platform.",
                created_at=started,
            )
            db.upsert_prompt(record, C.BRAND)
            for e_index, engine in enumerate(Engine):
                at = started + timedelta(seconds=90 * (order * 4 + e_index))
                first_two = [
                    build_sample(rng, prompt, pid, engine, t, at + timedelta(seconds=k * 20))
                    for k in range(2)
                ]
                samples = first_two
                if first_two[0].client_cited != first_two[1].client_cited:
                    samples = first_two + [
                        build_sample(rng, prompt, pid, engine, t, at + timedelta(seconds=40))
                    ]
                failed = 1 if rng.random() < 0.02 else 0
                db.record_samples(pid, samples, run_id)
                db.record_snapshot(pid, build_snapshot(samples, failed), run_id)
                for s in samples:
                    ledger.record(
                        ledger_row(rng, engine, run_id, pid, s.captured_at, estimates[engine])
                    )
                    cost += estimates[engine]
                calls += len(samples) + failed
                failed_calls += failed
            db.record_organic(pid, build_organic(rng, prompt, RankQueryKind.PROMPT, at), run_id)
            db.record_organic(pid, build_organic(rng, prompt, RankQueryKind.KEYWORD, at), run_id)
        finished = started + timedelta(minutes=38)
        db.record_run(
            TrackerRunSummary(
                run_id=run_id,
                lob=C.LOB,
                brand_name=C.BRAND,
                started_at=started,
                finished_at=finished,
                candidates_generated=len(C.PROMPTS),
                candidates_kept=len(C.PROMPTS),
                prompts_selected=len(C.PROMPTS),
                engine_calls=calls,
                failed_engine_calls=failed_calls,
                estimated_cost_usd=round(cost, 2),
                semrush_units=0,
                report_path=None,
            )
        )
        total_calls += calls
        positions.record_project_run(
            ProjectRunRecord(
                id=uuid.uuid4().hex[:16],
                project_id=project.id,
                started_at=started,
                finished_at=finished,
                run_ids=[run_id],
                prompts_run=len(C.PROMPTS),
                batches=1,
                statuses=["success"],
                full=True,
            )
        )
        if positions.runs_since_last_consolidation(project.id) >= WINDOW:
            positions.consolidate(
                project, tracked, window_runs=WINDOW, trigger="auto", now=finished
            )

    history = positions.consolidations(project.id)
    engine_view = InsightEngine(db, positions, actions)
    latest = engine_view.build(project, tracked)
    if len(history) >= 3:
        earlier = history[min(7, len(history) - 1)]  # ~7 consolidations back
        before = engine_view.build(project, tracked, consolidation_id=earlier.id)
        before_by = {a.id: a for a in before.actions}
        wanted: list[tuple[str, Engine | None]] = [
            ("earned_placement", Engine.PERPLEXITY),  # designed to improve
            ("defend", None),  # a drop: scores as regressed
            ("landing_page", None),  # no metric movement: unchanged
        ]
        seen_types: set[str] = set()
        for card in latest.actions:
            if card.type in seen_types:
                continue
            if not any(card.type == t_ and (e_ is None or card.engine is e_) for t_, e_ in wanted):
                continue
            seen_types.add(card.type)
            old = before_by.get(card.id)
            value = float(old.evidence.numbers.get(card.metric, 0.0)) if old else 0.6
            actions.set_state(
                project.id,
                card.id,
                status="done",
                owner="Analyst (demo)",
                note="Marked done for the demonstration; scored at the next consolidation.",
                baseline={
                    "metric": card.metric,
                    "value": value,
                    "direction": "up",
                    "basis": earlier.id,
                    "title": card.title,
                },
                now=earlier.consolidated_at,
            )

    final = engine_view.build(project, tracked)
    print(f"Project {project.id} '{project.name}' seeded into {db_path}")
    print(f"  prompts {len(tracked)} · crawls {RUNS} · consolidations {len(history)}")
    print(f"  engine calls {total_calls} · ledger rows {ledger.count()}")
    print(
        f"  insights: health {[h.verdict for h in final.health]}, actions "
        f"{sorted({a.type for a in final.actions})}, outcomes "
        f"{sorted({a.outcome for a in final.actions if a.status == 'done'})}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
