"""Aggregates sampled engine answers into a `CitationSnapshot`.

"Is the client cited?" is answered with `core.domains.domain_matches`, so a
citation of `blog.gep.com` counts for `gep.com` and `notgep.com` never does.
Rank is the 1-indexed position of the client's first citation in the engine's
own ordering; across samples the best and mean ranks are both kept.

Beyond domains, the snapshot keeps every cited URL (best position first), the
client's own cited URLs, the consulted-but-not-cited URLs, and the brand
mentions found in the answer text with their sentences.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean

from src.core.domains import domain_matches, registrable_domain
from src.integrations.schemas import Citation, Engine, EngineAnswer
from src.modules.prompt_tracking.mentions import CLIENT, detect_mentions
from src.modules.prompt_tracking.schemas import CitationSnapshot, ClientProfile, MentionSnippet

__all__ = ["build_snapshot", "client_rank", "merge_links"]

_EXCERPT_CHARS = 300
_MAX_SNIPPETS = 10


def client_rank(answer: EngineAnswer, client_domains: list[str]) -> int | None:
    """1-indexed position of the first citation belonging to the client."""
    for citation in answer.citations:
        if any(domain_matches(citation.domain, d) for d in client_domains):
            return citation.position
    return None


def merge_links(answers: list[EngineAnswer]) -> list[Citation]:
    """Union of citations across samples, one per URL, at its best position."""
    best: dict[str, Citation] = {}
    for answer in answers:
        for citation in answer.citations:
            current = best.get(citation.url)
            if current is None or citation.position < current.position:
                best[citation.url] = citation.model_copy()
    return sorted(best.values(), key=lambda c: (c.position, c.url))


def build_snapshot(
    engine: Engine,
    answers: list[EngineAnswer],
    client: ClientProfile,
    *,
    failed_samples: int = 0,
) -> CitationSnapshot:
    """Fold N sampled answers for one engine into one snapshot.

    Args:
        engine: Engine the answers came from.
        answers: Successful samples. May be empty if every call failed.
        client: Supplies client and competitor domains and brand terms.
        failed_samples: Calls that raised and produced no answer.
    """
    total = len(answers) + failed_samples
    if total == 0:
        msg = "A snapshot needs at least one attempted sample."
        raise ValueError(msg)

    ranks = [client_rank(a, client.domains) for a in answers]
    cited_ranks = [r for r in ranks if r is not None]
    successful = len(answers)

    domain_counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    competitor_best: dict[str, int] = {}
    competitors = [registrable_domain(d) for d in client.competitor_domains]
    for answer in answers:
        for domain in answer.cited_domains:
            domain_counts[domain] += 1
            first_seen.setdefault(domain, len(first_seen))
        for citation in answer.citations:
            for competitor in competitors:
                if competitor and domain_matches(citation.domain, competitor):
                    best = competitor_best.get(competitor)
                    if best is None or citation.position < best:
                        competitor_best[competitor] = citation.position

    links = merge_links(answers)
    client_urls = [c.url for c in links if any(domain_matches(c.domain, d) for d in client.domains)]
    consulted = list(dict.fromkeys(u for a in answers for u in a.consulted_urls))

    mention_samples = 0
    snippets: list[MentionSnippet] = []
    seen_snippets: set[str] = set()
    competitor_mentions: Counter[str] = Counter()
    for answer in answers:
        mentions = detect_mentions(answer.answer_text, client)
        entities = {m.entity for m in mentions}
        if CLIENT in entities:
            mention_samples += 1
        for entity in entities - {CLIENT}:
            competitor_mentions[entity] += 1
        for m in mentions:
            if m.entity != CLIENT:
                continue
            key = m.snippet.lower()
            if key not in seen_snippets and len(snippets) < _MAX_SNIPPETS:
                seen_snippets.add(key)
                snippets.append(m)

    ordered_domains = sorted(domain_counts, key=lambda d: (-domain_counts[d], first_seen[d]))
    model = answers[0].model if answers else "unavailable"
    excerpt = answers[0].answer_text[:_EXCERPT_CHARS] if answers else ""

    return CitationSnapshot(
        engine=engine,
        model=model,
        samples=total,
        failed_samples=failed_samples,
        web_trigger_rate=(sum(a.web_triggered for a in answers) / successful)
        if successful
        else 0.0,
        client_cited_samples=len(cited_ranks),
        client_citation_rate=(len(cited_ranks) / successful) if successful else 0.0,
        client_cited=bool(cited_ranks) and len(cited_ranks) * 2 >= successful,
        client_best_rank=min(cited_ranks) if cited_ranks else None,
        client_mean_rank=round(mean(cited_ranks), 2) if cited_ranks else None,
        cited_domains=ordered_domains,
        competitor_citations=dict(sorted(competitor_best.items(), key=lambda kv: kv[1])),
        answer_excerpt=excerpt,
        citation_links=links,
        client_urls=client_urls,
        consulted_urls=consulted,
        mention_detected=mention_samples > 0,
        mention_rate=(mention_samples / successful) if successful else 0.0,
        mention_snippets=snippets,
        competitor_mentions=dict(sorted(competitor_mentions.items())),
    )
