"""Insight engine: from stored crawl data to verdicts, changes and action cards.

Nothing here calls a vendor. Every figure comes from what one project crawl
already stored (snapshots, answer samples with full text, the engine's own
search queries, claim attributions, source snippets, organic SERP extras) and
from the consolidated positions. Cards are deterministic and carry a stable id
so an analyst's status survives recomputation and can be scored after the
next consolidation.
"""

from __future__ import annotations

import hashlib
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from src.core.domains import domain_matches, normalize_domain, registrable_domain
from src.integrations.schemas import Engine
from src.modules.control_plane.actions import ActionStateStore
from src.modules.control_plane.planner import effective_engines
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.schemas import (
    ActionCard,
    ActionEvidence,
    ActionUpdate,
    ClaimEntry,
    ConsolidatedPosition,
    DomainShare,
    EngineHealth,
    EvidenceQuote,
    FanoutQuery,
    FreshnessProfile,
    InsightBasis,
    InsightChange,
    InsightsView,
    PageInventory,
    PlacementProfile,
    Project,
    RejectedPage,
    TrackedPrompt,
    TrustShare,
)
from src.modules.prompt_tracking.mentions import CLIENT
from src.modules.prompt_tracking.schemas import AnswerSample, RankQueryKind
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["InsightEngine", "classify_domain"]

_AGGREGATORS = {
    "g2.com",
    "capterra.com",
    "gartner.com",
    "trustradius.com",
    "softwareadvice.com",
    "getapp.com",
    "peerspot.com",
    "sourceforge.net",
    "crozdesk.com",
    "selecthub.com",
    "clutch.co",
    "softwaresuggest.com",
    "goodfirms.co",
    "producthunt.com",
    "forrester.com",
}
_FORUMS = {"reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com", "ycombinator.com"}
_REFERENCE = {"wikipedia.org", "wikidata.org", "britannica.com"}
_DOCS = {"github.com", "readthedocs.io", "gitbook.io", "notion.site"}
_MARKETPLACE_HINTS = ("marketplace.", "appexchange", "/marketplace", "apps.", "appsource")
_TOP_THIRD = 0.34
_ACTION_TARGET = 0.5
_MIN_SHARE = 0.3
_MIN_MENTION = 0.4
_LOW_CITED = 0.1

_TITLES: dict[str, str] = {
    "convert_mention": "{engine}: named but not linked in '{topic}'",
    "own_claim": "{domain} owns '{topic}' on {engine}",
    "aio_gap": "Ranked #{organic_best} organically, absent from AI Overview: '{topic}'",
    "earned_placement": "{engine} trusts third-party listings for '{topic}'",
    "defend": "Citation rate fell {drop:.0%} on {engine} for '{topic}'",
    "landing_page": "No landing page for '{topic}' ({n} prompt{plural})",
    "read_but_rejected": "{engine} read {page} and did not cite it",
    "freshness": "Client sources on {engine} are {gap:.0f} days older than competitors'",
}
_PRESCRIPTIONS: dict[str, str] = {
    "convert_mention": (
        "{engine} names the brand in {mention_rate:.0%} of answers but links it in "
        "{cited_rate:.0%}. Publish or fix the page that should own these claims (clear H1, "
        "one definition sentence, FAQ schema, crawlable), and get listed on the domains this "
        "engine links for the topic."
    ),
    "own_claim": (
        "{domain} is cited in {rival_share:.0%} of {engine} answers for this topic (client "
        "{cited_rate:.0%}). Create or upgrade a page that answers the engine's own queries "
        "below in the format of the winning page, and earn the claims it currently holds."
    ),
    "aio_gap": (
        "Google ranks the client on the classic results but the AI Overview cites others. Add "
        "a concise answer block matching the Overview's sentences and cover the "
        "People-Also-Ask questions below on the ranking page."
    ),
    "earned_placement": (
        "{share:.0%} of {engine}'s sources here are review sites, forums, marketplaces or "
        "reference pages. Secure or refresh the brand's presence on exactly these pages; "
        "that is where this engine looks."
    ),
    "defend": (
        "Check the cited client page still resolves and is unchanged, compare the engine's "
        "new sources below, and refresh the page's date and content."
    ),
    "landing_page": (
        "Create the page. Brief: answer the engine's own queries and the People-Also-Ask "
        "questions below in the first screen; one claim per H2; cite sources; add FAQ schema."
    ),
    "read_but_rejected": (
        "The engine consulted this client page in {samples} answer(s) and cited others. "
        "Answer the queries below in the first 100 words, add the missing specifics, and "
        "refresh the date."
    ),
    "freshness": (
        "Refresh the cited client pages: update the visible date, statistics and examples; "
        "engines prefer recent sources."
    ),
}


def classify_domain(
    url: str, domain: str, client_domains: list[str], competitors: list[str]
) -> str:
    """Bucket a cited source by what kind of site it is."""
    if any(domain_matches(domain, d) for d in client_domains):
        return "client"
    if any(domain_matches(domain, d) for d in competitors):
        return "competitor"
    host = normalize_domain(url) or domain
    lowered = url.lower()
    if any(hint in host or hint in lowered for hint in _MARKETPLACE_HINTS):
        return "marketplace"
    if domain in _AGGREGATORS:
        return "aggregator"
    if domain in _FORUMS:
        return "forum"
    if domain in _REFERENCE:
        return "reference"
    if domain in _DOCS or host.startswith("docs.") or "/docs/" in lowered:
        return "docs"
    return "publisher"


def _action_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]  # noqa: S324 - id, not security


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _median_age_days(dates: Iterable[str], now: datetime) -> float | None:
    ages: list[float] = []
    for raw in dates:
        try:
            parsed = datetime.fromisoformat(raw[:10])
        except ValueError:
            continue
        ages.append((now.date() - parsed.date()).days)
    return round(statistics.median(ages), 1) if ages else None


class InsightEngine:
    """Computes `InsightsView` for a project from stored data only."""

    def __init__(
        self, db: TimeSeriesDB, positions: PositionStore, actions: ActionStateStore
    ) -> None:
        """Bind the stores; nothing is fetched from a vendor."""
        self._db = db
        self._positions = positions
        self._actions = actions

    # -- public --------------------------------------------------------------------

    def build(
        self,
        project: Project,
        prompts: list[TrackedPrompt],
        *,
        consolidation_id: str | None = None,
        now: datetime | None = None,
    ) -> InsightsView:
        """Verdicts, changes, action cards and the raw-material maps for one project."""
        now = now or datetime.now(UTC)
        view = self._positions.positions(project.id, consolidation_id)
        positions = view.positions
        previous: list[ConsolidatedPosition] = []
        basis: InsightBasis
        if view.consolidation is not None:
            run_ids = list(view.consolidation.run_ids)
            index = next(
                (i for i, c in enumerate(view.history) if c.id == view.consolidation.id), 0
            )
            if index + 1 < len(view.history):
                previous = self._positions.positions(
                    project.id, view.history[index + 1].id
                ).positions
            basis = InsightBasis(
                consolidation_id=view.consolidation.id,
                computed_from="consolidation",
                crawls=len(view.consolidation.project_run_ids),
                samples=sum(p.samples for p in positions),
                low_confidence=len(view.consolidation.project_run_ids) < 2,
            )
        else:
            positions, crawls = self._positions.compute(project, prompts, window_runs=1)
            run_ids = sorted({rid for c in crawls for rid in c.run_ids})
            basis = InsightBasis(
                consolidation_id=None,
                computed_from="latest_crawl" if crawls else "none",
                crawls=len(crawls),
                samples=sum(p.samples for p in positions),
                low_confidence=True,
            )

        prompt_by_id = {p.prompt_id: p for p in prompts}
        records = self._db.prompt_records(list(prompt_by_id))
        subtopic_of = {
            pid: (p.subtopic or str(records.get(pid, {}).get("subtopic") or "") or "General")
            for pid, p in prompt_by_id.items()
        }
        samples = self._load_samples(prompts, run_ids)
        client_domains = list(project.client.domains)
        competitors = [registrable_domain(d) for d in project.client.competitor_domains if d]

        health = self._health(project, prompts, positions, previous, samples, competitors)
        changes = self._changes(prompt_by_id, positions, previous)
        fanout = self._fanout(samples, subtopic_of)
        claims = self._claims(samples, client_domains, competitors)
        trust = self._trust_profile(samples, client_domains, competitors)
        rejected = self._read_but_rejected(samples, client_domains)
        winning, client_pages = self._pages(samples, prompt_by_id, client_domains)
        placement = self._placement(samples)
        freshness = self._freshness(samples, client_domains, competitors, now)
        actions = self._actions_for(
            project,
            prompts,
            positions,
            previous,
            samples,
            records,
            subtopic_of,
            fanout,
            claims,
            rejected,
            winning,
            freshness,
            basis,
            client_domains,
            competitors,
        )
        return InsightsView(
            generated_at=now,
            basis=basis,
            health=health,
            changes=changes,
            actions=actions,
            fanout=fanout,
            claims=claims,
            trust_profile=trust,
            read_but_rejected=rejected,
            winning_pages=winning,
            client_pages=client_pages,
            placement=placement,
            freshness=freshness,
        )

    def update_action(
        self,
        project: Project,
        prompts: list[TrackedPrompt],
        action_id: str,
        update: ActionUpdate,
        *,
        now: datetime | None = None,
    ) -> ActionCard:
        """Store the analyst's status/owner/note; capture a baseline when marked done.

        Raises:
            KeyError: No current action with that id.
        """
        view = self.build(project, prompts, now=now)
        card = next((a for a in view.actions if a.id == action_id), None)
        if card is None:
            raise KeyError(action_id)
        baseline: dict[str, float | str | None] | None = None
        if update.status == "done":
            baseline = {
                "metric": card.metric,
                "value": float(card.evidence.numbers.get(card.metric, 0.0)),
                "direction": "up",
                "basis": view.basis.consolidation_id,
                "title": card.title,
            }
        state = self._actions.set_state(
            project.id,
            action_id,
            status=update.status,
            owner=update.owner,
            note=update.note,
            baseline=baseline,
            now=now,
        )
        return card.model_copy(
            update={
                "status": state.status,
                "owner": state.owner,
                "note": state.note,
                "outcome": "pending",
            }
        )

    # -- loading ---------------------------------------------------------------------

    def _load_samples(self, prompts: list[TrackedPrompt], run_ids: list[str]) -> list[AnswerSample]:
        if not run_ids:
            return []
        out: list[AnswerSample] = []
        for prompt in prompts:
            out.extend(self._db.samples_for(prompt.prompt_id, run_ids=run_ids))
        return out

    # -- health & changes -------------------------------------------------------------

    def _health(
        self,
        project: Project,
        prompts: list[TrackedPrompt],
        positions: list[ConsolidatedPosition],
        previous: list[ConsolidatedPosition],
        samples: list[AnswerSample],
        competitors: list[str],
    ) -> list[EngineHealth]:
        out: list[EngineHealth] = []
        prev_rate = self._engine_rates(previous)
        for engine in project.engines:
            mine = [p for p in positions if p.engine is engine]
            prompt_count = sum(1 for p in prompts if engine in effective_engines(project, p))
            if not mine:
                out.append(
                    EngineHealth(
                        engine=engine,
                        verdict="invisible",
                        losing_to=None,
                        cited_rate=0.0,
                        mention_rate=0.0,
                        best_rank=None,
                        delta_cited_rate=None,
                        samples=0,
                        crawls=0,
                        volatility=0.0,
                        prompts=prompt_count,
                    )
                )
                continue
            ok = sum(p.samples - p.failed_samples for p in mine)
            cited = _rate(sum(p.cited_samples for p in mine), ok)
            mention = _rate(sum(p.mention_samples for p in mine), ok)
            ranks = [p.best_rank for p in mine if p.best_rank is not None]
            volatility = round(
                statistics.mean(min(p.citation_rate, 1 - p.citation_rate) * 2 for p in mine), 3
            )
            share = self._domain_share([s for s in samples if s.engine is engine])
            rival = next(((d, v) for d, v in share if d in competitors), None)
            verdict = "invisible"
            losing_to = None
            if cited >= _ACTION_TARGET and ranks and min(ranks) <= 3:
                verdict = "winning"
            elif rival is not None and rival[1] > cited and cited < _ACTION_TARGET:
                verdict, losing_to = "losing", rival[0]
            elif cited > 0 or mention > 0:
                verdict = "present"
            delta = round(cited - prev_rate[engine], 4) if engine in prev_rate else None
            out.append(
                EngineHealth(
                    engine=engine,
                    verdict=verdict,
                    losing_to=losing_to,
                    cited_rate=cited,
                    mention_rate=mention,
                    best_rank=min(ranks) if ranks else None,
                    delta_cited_rate=delta,
                    samples=sum(p.samples for p in mine),
                    crawls=max(p.runs for p in mine),
                    volatility=volatility,
                    prompts=prompt_count,
                )
            )
        return out

    @staticmethod
    def _engine_rates(positions: list[ConsolidatedPosition]) -> dict[Engine, float]:
        by: dict[Engine, list[ConsolidatedPosition]] = defaultdict(list)
        for p in positions:
            by[p.engine].append(p)
        return {
            e: _rate(
                sum(p.cited_samples for p in ps), sum(p.samples - p.failed_samples for p in ps)
            )
            for e, ps in by.items()
        }

    def _changes(
        self,
        prompt_by_id: dict[str, TrackedPrompt],
        positions: list[ConsolidatedPosition],
        previous: list[ConsolidatedPosition],
    ) -> list[InsightChange]:
        if not previous:
            return []
        before = {(p.prompt_id, p.engine): p for p in previous}
        out: list[InsightChange] = []
        for p in positions:
            old = before.get((p.prompt_id, p.engine))
            text = (
                prompt_by_id[p.prompt_id].prompt_text
                if p.prompt_id in prompt_by_id
                else p.prompt_id
            )
            if old is None:
                continue
            if p.cited and not old.cited:
                out.append(
                    self._change(
                        "flip_up",
                        p,
                        text,
                        f"{old.citation_rate:.0%}",
                        f"{p.citation_rate:.0%}",
                        "now cited",
                    )
                )
            elif old.cited and not p.cited:
                out.append(
                    self._change(
                        "flip_down",
                        p,
                        text,
                        f"{old.citation_rate:.0%}",
                        f"{p.citation_rate:.0%}",
                        "no longer cited",
                    )
                )
            if p.best_rank and old.best_rank and abs(p.best_rank - old.best_rank) >= 2:
                kind = "rank_up" if p.best_rank < old.best_rank else "rank_down"
                out.append(
                    self._change(
                        kind, p, text, f"#{old.best_rank}", f"#{p.best_rank}", "best rank moved"
                    )
                )
            new_rivals = [
                d
                for d, s in p.cited_domain_share.items()
                if s >= _MIN_SHARE and old.cited_domain_share.get(d, 0.0) < _MIN_SHARE
            ]
            for d in new_rivals[:2]:
                out.append(
                    self._change(
                        "new_competitor",
                        p,
                        text,
                        "-",
                        f"{p.cited_domain_share[d]:.0%}",
                        f"{d} entered the sources",
                    )
                )
        current_keys = {(p.prompt_id, p.engine) for p in positions}
        for key, old in before.items():
            if key not in current_keys and key[0] in prompt_by_id:
                out.append(
                    self._change(
                        "lost_platform",
                        old,
                        prompt_by_id[key[0]].prompt_text,
                        f"{old.citation_rate:.0%}",
                        "-",
                        "no data in this window",
                    )
                )
        order = {
            "flip_down": 0,
            "lost_platform": 1,
            "rank_down": 2,
            "new_competitor": 3,
            "flip_up": 4,
            "rank_up": 5,
        }
        return sorted(out, key=lambda c: order.get(c.kind, 9))[:50]

    @staticmethod
    def _change(
        kind: str, p: ConsolidatedPosition, text: str, before: str, after: str, note: str
    ) -> InsightChange:
        return InsightChange(
            kind=kind,
            prompt_id=p.prompt_id,
            prompt_text=text,
            engine=p.engine,
            before=before,
            after=after,
            text=note,
        )

    # -- raw-material maps ---------------------------------------------------------------

    @staticmethod
    def _domain_share(samples: list[AnswerSample]) -> list[tuple[str, float]]:
        if not samples:
            return []
        counts: Counter[str] = Counter()
        for s in samples:
            for d in dict.fromkeys(s.cited_domains):
                counts[d] += 1
        return [(d, round(n / len(samples), 4)) for d, n in counts.most_common(25)]

    def _fanout(
        self, samples: list[AnswerSample], subtopic_of: dict[str, str]
    ) -> list[FanoutQuery]:
        engines: dict[str, set[Engine]] = defaultdict(set)
        prompts: dict[str, set[str]] = defaultdict(set)
        covered: dict[str, bool] = defaultdict(bool)
        display: dict[str, str] = {}
        topics: dict[str, Counter[str]] = defaultdict(Counter)
        for s in samples:
            for q in s.search_queries:
                key = q.casefold().strip()
                if not key:
                    continue
                display.setdefault(key, q.strip())
                engines[key].add(s.engine)
                prompts[key].add(s.prompt_id)
                covered[key] = covered[key] or s.client_cited
                topics[key][subtopic_of.get(s.prompt_id, "General")] += 1
        out = [
            FanoutQuery(
                query=display[k],
                engines=sorted(engines[k], key=lambda e: e.value),
                prompts=len(prompts[k]),
                subtopic=topics[k].most_common(1)[0][0],
                client_covered=covered[k],
            )
            for k in display
        ]
        return sorted(out, key=lambda f: (-f.prompts, f.query))[:200]

    @staticmethod
    def _claims(
        samples: list[AnswerSample], client_domains: list[str], competitors: list[str]
    ) -> list[ClaimEntry]:
        seen: set[tuple[str, str, str]] = set()
        out: list[ClaimEntry] = []
        for s in samples:
            for c in s.citation_claims:
                key = (c.sentence.casefold(), c.url, s.engine.value)
                if key in seen:
                    continue
                seen.add(key)
                domain = registrable_domain(c.url) or c.url
                out.append(
                    ClaimEntry(
                        sentence=c.sentence,
                        url=c.url,
                        domain=domain,
                        engine=s.engine,
                        prompt_id=s.prompt_id,
                        is_client=any(domain_matches(domain, d) for d in client_domains),
                        is_competitor=any(domain_matches(domain, d) for d in competitors),
                    )
                )
        return out[:500]

    @staticmethod
    def _trust_profile(
        samples: list[AnswerSample], client_domains: list[str], competitors: list[str]
    ) -> list[TrustShare]:
        counts: dict[Engine, Counter[str]] = defaultdict(Counter)
        for s in samples:
            for link in s.citation_links:
                counts[s.engine][
                    classify_domain(link.url, link.domain, client_domains, competitors)
                ] += 1
        out: list[TrustShare] = []
        for engine, counter in counts.items():
            total = sum(counter.values())
            for cls, n in counter.most_common():
                out.append(
                    TrustShare(
                        engine=engine, domain_class=cls, share=round(n / total, 4), citations=n
                    )
                )
        return sorted(out, key=lambda t: (t.engine.value, -t.share))

    @staticmethod
    def _read_but_rejected(
        samples: list[AnswerSample], client_domains: list[str]
    ) -> list[RejectedPage]:
        hits: dict[tuple[str, Engine], list[AnswerSample]] = defaultdict(list)
        for s in samples:
            if s.client_cited:
                continue
            for url in s.consulted_urls:
                domain = registrable_domain(url)
                if domain and any(domain_matches(domain, d) for d in client_domains):
                    hits[(url, s.engine)].append(s)
        out = [
            RejectedPage(
                url=url,
                engine=engine,
                samples=len(ss),
                queries=sorted({q for s in ss for q in s.search_queries})[:10],
            )
            for (url, engine), ss in hits.items()
        ]
        return sorted(out, key=lambda r: -r.samples)[:50]

    @staticmethod
    def _pages(
        samples: list[AnswerSample],
        prompt_by_id: dict[str, TrackedPrompt],
        client_domains: list[str],
    ) -> tuple[list[PageInventory], list[PageInventory]]:
        acc: dict[str, dict[str, Any]] = {}
        for s in samples:
            snippet_by_url = {sn.url: sn for sn in s.source_snippets}
            for link in s.citation_links:
                entry = acc.setdefault(
                    link.url,
                    {
                        "domain": link.domain,
                        "title": link.title,
                        "citations": 0,
                        "engines": set(),
                        "prompts": set(),
                        "snippet": None,
                        "date": None,
                    },
                )
                entry["citations"] += 1
                entry["engines"].add(s.engine)
                entry["prompts"].add(s.prompt_id)
                if link.title and not entry["title"]:
                    entry["title"] = link.title
                sn = snippet_by_url.get(link.url)
                if sn is not None:
                    entry["snippet"] = entry["snippet"] or (sn.snippet or None)
                    entry["date"] = entry["date"] or sn.date
                    entry["title"] = entry["title"] or sn.title
        pages = [
            PageInventory(
                url=url,
                domain=e["domain"],
                title=e["title"],
                citations=e["citations"],
                engines=sorted(e["engines"], key=lambda x: x.value),
                prompts=len(e["prompts"]),
                snippet=e["snippet"],
                date=e["date"],
                is_client=any(domain_matches(e["domain"], d) for d in client_domains),
            )
            for url, e in acc.items()
        ]
        pages.sort(key=lambda p: (-p.citations, p.url))
        return [p for p in pages if not p.is_client][:50], [p for p in pages if p.is_client][:50]

    @staticmethod
    def _placement(samples: list[AnswerSample]) -> list[PlacementProfile]:
        by: dict[Engine, PlacementProfile] = {}
        for s in samples:
            client_snippets = [m.snippet for m in s.mentions if m.entity == CLIENT]
            if not client_snippets or not s.answer_text:
                continue
            profile = by.setdefault(
                s.engine,
                PlacementProfile(
                    engine=s.engine,
                    samples_with_mention=0,
                    first_third=0,
                    in_list=0,
                    in_table=0,
                    recommendation_sentence=0,
                ),
            )
            profile.samples_with_mention += 1
            first = s.answer_text.find(client_snippets[0][:40])
            if first >= 0 and first / max(len(s.answer_text), 1) <= _TOP_THIRD:
                profile.first_third += 1
            lines = [
                ln
                for ln in s.answer_text.splitlines()
                if any(sn[:40] in ln for sn in client_snippets)
            ]
            if any(
                ln.lstrip().startswith(("-", "*", "•")) or ln.lstrip()[:2].rstrip(".").isdigit()
                for ln in lines
            ):
                profile.in_list += 1
            if any("|" in ln for ln in lines):
                profile.in_table += 1
            if any(
                word in sn.lower()
                for sn in client_snippets
                for word in ("recommend", "best", "top", "leading", "consider")
            ):
                profile.recommendation_sentence += 1
        return sorted(by.values(), key=lambda p: p.engine.value)

    @staticmethod
    def _freshness(
        samples: list[AnswerSample],
        client_domains: list[str],
        competitors: list[str],
        now: datetime,
    ) -> list[FreshnessProfile]:
        dated: dict[Engine, list[str]] = defaultdict(list)
        mine: dict[Engine, list[str]] = defaultdict(list)
        theirs: dict[Engine, list[str]] = defaultdict(list)
        for s in samples:
            cited = {c.url for c in s.citation_links}
            for sn in s.source_snippets:
                if not sn.date or sn.url not in cited:
                    continue
                dated[s.engine].append(sn.date)
                domain = registrable_domain(sn.url) or ""
                if any(domain_matches(domain, d) for d in client_domains):
                    mine[s.engine].append(sn.date)
                elif any(domain_matches(domain, d) for d in competitors):
                    theirs[s.engine].append(sn.date)
        return [
            FreshnessProfile(
                engine=e,
                dated_sources=len(ds),
                median_age_days=_median_age_days(ds, now),
                client_median_age_days=_median_age_days(mine[e], now),
                competitor_median_age_days=_median_age_days(theirs[e], now),
            )
            for e, ds in sorted(dated.items(), key=lambda kv: kv[0].value)
        ]

    # -- action cards ----------------------------------------------------------------------

    def _actions_for(  # noqa: PLR0913 - one card factory per rule needs all the maps
        self,
        project: Project,
        prompts: list[TrackedPrompt],
        positions: list[ConsolidatedPosition],
        previous: list[ConsolidatedPosition],
        samples: list[AnswerSample],
        records: dict[str, dict[str, object]],
        subtopic_of: dict[str, str],
        fanout: list[FanoutQuery],
        claims: list[ClaimEntry],
        rejected: list[RejectedPage],
        winning: list[PageInventory],
        freshness: list[FreshnessProfile],
        basis: InsightBasis,
        client_domains: list[str],
        competitors: list[str],
    ) -> list[ActionCard]:
        cards: list[ActionCard] = []
        clusters: dict[str, list[str]] = defaultdict(list)
        for pid, topic in subtopic_of.items():
            clusters[topic].append(pid)
        prev_by = {(p.prompt_id, p.engine): p for p in previous}
        pos_by = {(p.prompt_id, p.engine): p for p in positions}
        volume_of = {
            pid: int(str(records.get(pid, {}).get("search_volume") or 0)) for pid in subtopic_of
        }

        for topic, pids in clusters.items():
            weight = 1 + sum(volume_of[p] for p in pids) / 100
            topic_queries = [f.query for f in fanout if f.subtopic == topic][:10]
            for engine in project.engines:
                mine = [pos_by[(p, engine)] for p in pids if (p, engine) in pos_by]
                ok = sum(p.samples - p.failed_samples for p in mine)
                if not mine or not ok:
                    continue
                cited_rate = _rate(sum(p.cited_samples for p in mine), ok)
                mention_rate = _rate(sum(p.mention_samples for p in mine), ok)
                cluster_samples = [s for s in samples if s.engine is engine and s.prompt_id in pids]
                share = self._domain_share(cluster_samples)
                shares = [DomainShare(domain=d, share=v) for d, v in share[:5]]
                quotes = self._quotes(cluster_samples, CLIENT)
                numbers = {
                    "cited_rate": cited_rate,
                    "mention_rate": mention_rate,
                    "samples": float(ok),
                }
                fmt = {
                    "engine": engine.value,
                    "topic": topic,
                    "cited_rate": cited_rate,
                    "mention_rate": mention_rate,
                }

                if mention_rate >= _MIN_MENTION and cited_rate <= _LOW_CITED:
                    cards.append(
                        self._card(
                            "convert_mention",
                            engine,
                            topic,
                            pids,
                            fmt,
                            impact=weight * (_ACTION_TARGET - cited_rate),
                            evidence=ActionEvidence(
                                quotes=quotes,
                                domains=shares,
                                queries=topic_queries,
                                numbers=numbers,
                            ),
                        )
                    )

                rival = next(
                    (
                        (d, v)
                        for d, v in share
                        if d in competitors and v >= _MIN_SHARE and cited_rate < v
                    ),
                    None,
                )
                if rival is not None:
                    domain, rival_share = rival
                    pages = [w for w in winning if w.domain == domain and engine in w.engines]
                    rival_claims = [c for c in claims if c.domain == domain and c.engine is engine]
                    cards.append(
                        self._card(
                            "own_claim",
                            engine,
                            topic,
                            pids,
                            {**fmt, "domain": domain, "rival_share": rival_share},
                            impact=weight * (rival_share - cited_rate),
                            evidence=ActionEvidence(
                                quotes=[
                                    EvidenceQuote(text=c.sentence, engine=c.engine, entity=domain)
                                    for c in rival_claims[:5]
                                ],
                                domains=[DomainShare(domain=domain, share=rival_share)],
                                urls=[w.url for w in pages[:5]],
                                queries=topic_queries,
                                numbers={**numbers, "competitor_share": rival_share},
                            ),
                            key=domain,
                        )
                    )

                if engine is Engine.GOOGLE_AI_OVERVIEW:
                    organic_best = min(
                        (p.organic_prompt_best or p.organic_keyword_best or 99) for p in mine
                    )
                    if organic_best <= 5 and cited_rate < 0.2:
                        cards.append(
                            self._card(
                                "aio_gap",
                                engine,
                                topic,
                                pids,
                                {**fmt, "organic_best": organic_best},
                                impact=weight * (_ACTION_TARGET - cited_rate),
                                evidence=ActionEvidence(
                                    domains=shares,
                                    queries=self._paa(pids),
                                    numbers={**numbers, "organic_best": float(organic_best)},
                                ),
                            )
                        )

                earned = [
                    (d, v)
                    for d, v in share
                    if classify_domain(f"https://{d}/", d, client_domains, competitors)
                    in ("aggregator", "forum", "marketplace", "reference")
                ]
                earned_share = round(sum(v for _, v in earned), 4)
                if earned and earned_share >= _MIN_SHARE and cited_rate < _ACTION_TARGET:
                    earned_domains = {d for d, _ in earned}
                    cards.append(
                        self._card(
                            "earned_placement",
                            engine,
                            topic,
                            pids,
                            {**fmt, "share": earned_share},
                            impact=weight * earned_share,
                            evidence=ActionEvidence(
                                domains=[DomainShare(domain=d, share=v) for d, v in earned[:6]],
                                urls=[
                                    w.url
                                    for w in winning
                                    if w.domain in earned_domains and engine in w.engines
                                ][:8],
                                queries=topic_queries,
                                numbers={**numbers, "third_party_share": earned_share},
                            ),
                        )
                    )

                old = [prev_by[(p, engine)] for p in pids if (p, engine) in prev_by]
                old_ok = sum(p.samples - p.failed_samples for p in old)
                old_rate = _rate(sum(p.cited_samples for p in old), old_ok)
                if old and old_rate - cited_rate >= 0.2:
                    cards.append(
                        self._card(
                            "defend",
                            engine,
                            topic,
                            pids,
                            {**fmt, "drop": old_rate - cited_rate},
                            impact=weight * (old_rate - cited_rate),
                            evidence=ActionEvidence(
                                quotes=quotes[:2],
                                domains=shares,
                                queries=topic_queries,
                                numbers={**numbers, "previous_cited_rate": old_rate},
                            ),
                        )
                    )

            gaps = [p for p in pids if p in records and records[p].get("mapped_url") is None]
            if gaps:
                cards.append(
                    self._card(
                        "landing_page",
                        None,
                        topic,
                        gaps,
                        {"topic": topic, "n": len(gaps), "plural": "s" if len(gaps) > 1 else ""},
                        impact=weight * 0.4,
                        evidence=ActionEvidence(
                            queries=(topic_queries + self._paa(gaps))[:15],
                            numbers={"prompts": float(len(gaps)), "cited_rate": 0.0},
                        ),
                    )
                )

        for page in rejected[:10]:
            first = next((s.prompt_id for s in samples if page.url in s.consulted_urls), "")
            cards.append(
                self._card(
                    "read_but_rejected",
                    page.engine,
                    subtopic_of.get(first, "General"),
                    [],
                    {
                        "engine": page.engine.value,
                        "page": page.url.split("//")[-1][:60],
                        "samples": page.samples,
                    },
                    impact=1.0 + 0.3 * page.samples,
                    evidence=ActionEvidence(
                        urls=[page.url],
                        queries=page.queries,
                        numbers={"samples": float(page.samples), "cited_rate": 0.0},
                    ),
                    key=page.url,
                )
            )

        for f in freshness:
            mine_age, theirs_age = f.client_median_age_days, f.competitor_median_age_days
            if mine_age is not None and theirs_age is not None and mine_age - theirs_age > 365:
                cards.append(
                    self._card(
                        "freshness",
                        f.engine,
                        "All",
                        [],
                        {"engine": f.engine.value, "gap": mine_age - theirs_age},
                        impact=1.5,
                        evidence=ActionEvidence(
                            numbers={
                                "client_median_age_days": mine_age,
                                "competitor_median_age_days": theirs_age,
                                "cited_rate": 0.0,
                            }
                        ),
                    )
                )

        cards.sort(key=lambda c: -c.impact_score)
        return self._apply_states(project.id, cards, basis)

    def _card(  # noqa: PLR0913
        self,
        type_: str,
        engine: Engine | None,
        topic: str,
        pids: list[str],
        fmt: Mapping[str, object],
        *,
        impact: float,
        evidence: ActionEvidence,
        key: str = "",
    ) -> ActionCard:
        return ActionCard(
            id=_action_id(type_, engine.value if engine else "-", topic, key),
            type=type_,
            title=_TITLES[type_].format(**fmt),
            prescription=_PRESCRIPTIONS[type_].format(**fmt),
            impact_score=round(impact, 3),
            engine=engine,
            subtopic=topic,
            prompt_ids=pids,
            evidence=evidence,
            metric="cited_rate",
            status="open",
            outcome="pending",
            owner=None,
            note=None,
        )

    def _apply_states(
        self, project_id: str, cards: list[ActionCard], basis: InsightBasis
    ) -> list[ActionCard]:
        states = self._actions.states(project_id)
        out: list[ActionCard] = []
        for card in cards:
            state = states.get(card.id)
            if state is None:
                out.append(card)
                continue
            outcome = "pending"
            if (
                state.status == "done"
                and state.baseline
                and basis.consolidation_id
                and state.baseline.get("basis") != basis.consolidation_id
            ):
                before = float(state.baseline.get("value") or 0.0)
                after = float(card.evidence.numbers.get(card.metric, 0.0))
                up = state.baseline.get("direction", "up") == "up"
                moved = (after - before) if up else (before - after)
                outcome = (
                    "improved" if moved >= 0.1 else "regressed" if moved <= -0.1 else "unchanged"
                )
            out.append(
                card.model_copy(
                    update={
                        "status": state.status,
                        "owner": state.owner,
                        "note": state.note,
                        "outcome": outcome,
                    }
                )
            )
        return out

    @staticmethod
    def _quotes(samples: list[AnswerSample], entity: str, limit: int = 5) -> list[EvidenceQuote]:
        out: list[EvidenceQuote] = []
        seen: set[str] = set()
        for s in sorted(samples, key=lambda x: x.captured_at, reverse=True):
            for m in s.mentions:
                if m.entity != entity or m.snippet in seen:
                    continue
                seen.add(m.snippet)
                out.append(
                    EvidenceQuote(
                        text=m.snippet,
                        engine=s.engine,
                        run_id=None,
                        captured_at=s.captured_at,
                        entity=m.entity,
                    )
                )
                if len(out) >= limit:
                    return out
        return out

    def _paa(self, prompt_ids: list[str]) -> list[str]:
        questions: list[str] = []
        for pid in prompt_ids:
            for snap in self._db.organic_history(pid, RankQueryKind.PROMPT, limit=1):
                questions.extend(snap.paa_questions)
                questions.extend(snap.related_searches)
        return list(dict.fromkeys(questions))[:12]
