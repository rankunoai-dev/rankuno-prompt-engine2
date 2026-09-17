"""Folds SerpApi snapshots into an `OrganicRankSnapshot`.

Classic organic rankings are far more stable than AI answers, but the same SERP
call is already sampled N times for the AI Overview, so every sample is used:
the client's position is the best seen, competitor positions likewise, and the
top-10 domain list comes from the first successful sample.
"""

from __future__ import annotations

from src.core.domains import domain_matches, registrable_domain
from src.integrations.schemas import SerpSnapshot
from src.modules.prompt_tracking.schemas import ClientProfile, OrganicRankSnapshot, RankQueryKind

__all__ = ["TOP_N", "build_organic_snapshot", "client_position"]

TOP_N = 10


def client_position(serp: SerpSnapshot, client_domains: list[str]) -> tuple[int, str] | None:
    """Best client position and its URL in one SERP, or None if absent."""
    for result in sorted(serp.organic_results, key=lambda r: r.position):
        if any(domain_matches(result.domain, d) for d in client_domains):
            return result.position, result.url
    return None


def build_organic_snapshot(
    kind: RankQueryKind,
    query: str,
    serps: list[SerpSnapshot],
    client: ClientProfile,
) -> OrganicRankSnapshot | None:
    """Aggregate one or more SERP samples for `query` into a ranking snapshot.

    Returns None when there are no samples, so callers can simply skip.
    """
    if not serps:
        return None

    best: tuple[int, str] | None = None
    competitors = [registrable_domain(d) for d in client.competitor_domains]
    competitor_best: dict[str, int] = {}
    for serp in serps:
        hit = client_position(serp, client.domains)
        if hit and (best is None or hit[0] < best[0]):
            best = hit
        for result in serp.organic_results:
            for competitor in competitors:
                if competitor and domain_matches(result.domain, competitor):
                    current = competitor_best.get(competitor)
                    if current is None or result.position < current:
                        competitor_best[competitor] = result.position

    first = serps[0]
    top = [r.domain for r in sorted(first.organic_results, key=lambda r: r.position)[:TOP_N]]
    return OrganicRankSnapshot(
        query=query,
        query_kind=kind,
        device=first.device,
        samples=len(serps),
        client_position=best[0] if best else None,
        client_url=best[1] if best else None,
        top_domains=top,
        competitor_positions=dict(sorted(competitor_best.items(), key=lambda kv: kv[1])),
        organic_results=sorted(first.organic_results, key=lambda r: r.position)[:TOP_N],
        paa_questions=list(dict.fromkeys(q for s in serps for q in s.paa_questions)),
        related_searches=list(dict.fromkeys(q for s in serps for q in s.related_searches)),
    )
