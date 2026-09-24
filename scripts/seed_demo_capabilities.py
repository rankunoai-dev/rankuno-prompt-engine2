"""Backfill the demonstration project with the capabilities added after it was seeded.

`seed_demo_project.py` built the demo on 2026-09-18. Four cycles have shipped
since, and three of them store data the demo has none of, so the newest screens
open empty on the one project meant to show everything:

* **Sentiment and mention context** (ADR 0021) — `mention_judgements` rows, so
  the "How engines describe you" strip, the drawer's polarity chips and the
  `negative_claim` action cards have something to read.
* **Inbound crawler logs** (ADR 0022) — a synthetic access log fed through the
  real parser, aggregator and store, so Fetch → Consulted → Cited resolves on
  the client's own cited URLs and `fetched_not_cited` cards appear.
* **Market** (ADR 0023) — the project's locale, so the badge and the report's
  methodology page state a market rather than "Server default".

Everything written here is invented. **No vendor is called and nothing is
spent**: the sentiment rows are scored by a deterministic function of the
sentence, not by the judge, and the log lines are generated, not collected.

Two honesty notes, both deliberate:

1. The locale is written through the store, which bypasses the API's freeze
   (ADR 0023 refuses a locale change once a project has crawled). That guard
   exists because real history would then mix two markets; this project's
   history is fictional, so the demo is the one place the bypass is harmless.
2. `mention_judgements` has no `source` column, so these rows are not
   distinguishable from judged ones the way the ledger separates demo spend.
   Nothing real has ever been judged in this environment — the ledger holds no
   Anthropic calls at all — so today every judgement in the database is this
   script's. If a real judge ever runs here, re-check that assumption.

Usage (from the repository root, with the venv python):
    python scripts/seed_demo_capabilities.py [--project-id ID] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import random
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_settings  # noqa: E402
from src.core.locale import Locale  # noqa: E402
from src.modules.control_plane.schemas import ProjectUpdate  # noqa: E402
from src.modules.control_plane.store import ProjectStore  # noqa: E402
from src.modules.crawler_logs.ingest import aggregate  # noqa: E402
from src.modules.crawler_logs.parser import ParseStats, iter_hits  # noqa: E402
from src.modules.crawler_logs.ranges import bundled_ranges  # noqa: E402
from src.modules.crawler_logs.store import CrawlerLogStore, DuplicateImport  # noqa: E402
from src.modules.prompt_tracking.schemas import MentionJudgement  # noqa: E402
from src.modules.prompt_tracking.sentiment import RUBRIC_VERSION  # noqa: E402
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB  # noqa: E402

DEMO_PROJECT_ID: Final = "3e74d5d910c1"
JUDGE_MODEL: Final = "claude-haiku-4-5"

# -- sentiment ---------------------------------------------------------------------

# Attributes are picked from what the sentence actually says where possible, so a
# quote about cost is tagged "pricing" rather than something random. The fallback
# keeps the distribution realistic for sentences that name a brand and little else.
_TOPIC_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "pricing": ("pricing", "price", "cost", "expensive", "licence", "license", "quote", "budget"),
    "implementation": ("implementation", "deploy", "rollout", "onboarding", "timeline", "migrate"),
    "support": ("support", "service", "response", "account manager", "training", "helpdesk"),
    "integration": ("integration", "erp", "sap", "oracle", "api", "connector", "interoperab"),
    "usability": ("usability", "interface", "ui", "intuitive", "learning curve", "complex"),
    "ai capability": ("ai", "machine learning", "automation", "predictive", "intelligent"),
    "analytics": ("analytics", "spend analysis", "reporting", "dashboard", "visibility"),
    "scale": ("enterprise", "global", "scale", "large", "fortune"),
}
_NEGATIVE_WORDS: Final = (
    "expensive", "costly", "complex", "steep", "slow", "difficult", "lacking", "limited",
    "criticis", "criticiz", "drawback", "concern", "long implementation", "overkill",
)  # fmt: skip
_POSITIVE_WORDS: Final = (
    "leading", "strong", "best", "recommended", "robust", "comprehensive", "powerful",
    "trusted", "top", "excellent", "mature", "proven",
)  # fmt: skip

# What the judge would plausibly return, before the sentence's own words are read.
_BASE_MIX: Final = (("positive", 44), ("neutral", 41), ("negative", 12), ("not_about_brand", 3))


def _seeded(*parts: str) -> random.Random:
    """A generator keyed by content, so re-running writes identical rows."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()  # noqa: S324 - not security
    return random.Random(int(digest[:12], 16))  # noqa: S311 - seeded fixtures, not crypto


def _polarity(sentence: str, rng: random.Random) -> str:
    """Score a sentence: its own wording first, the base mix otherwise."""
    lowered = sentence.lower()
    if any(word in lowered for word in _NEGATIVE_WORDS):
        return "negative" if rng.random() < 0.8 else "neutral"
    if any(word in lowered for word in _POSITIVE_WORDS):
        return "positive" if rng.random() < 0.85 else "neutral"
    roll = rng.randrange(sum(weight for _, weight in _BASE_MIX))
    for polarity, weight in _BASE_MIX:
        roll -= weight
        if roll < 0:
            return polarity
    return "neutral"


def _attributes(sentence: str, polarity: str, rng: random.Random) -> list[str]:
    """Up to three attributes, preferring the topics the sentence names."""
    lowered = sentence.lower()
    found = [topic for topic, words in _TOPIC_WORDS.items() if any(w in lowered for w in words)]
    if not found:
        found = rng.sample(sorted(_TOPIC_WORDS), k=2)
    if polarity == "not_about_brand":
        return []
    return found[:3]


def _samples(db: TimeSeriesDB, prompt_ids: list[str], run_ids: list[str]) -> list[object]:
    """Every stored sample of the project, read prompt by prompt."""
    out = []
    for prompt_id in prompt_ids:
        out.extend(db.samples_for(prompt_id, run_ids=run_ids, limit=5000))
    return out


def _judgements(
    db: TimeSeriesDB, prompt_ids: list[str], run_ids: list[str], now: datetime
) -> tuple[list[MentionJudgement], dict[str, int]]:
    """One judgement per (sample, entity, sentence), scored deterministically."""
    rows: list[MentionJudgement] = []
    tally: dict[str, int] = {}
    seen: set[tuple[str, str, str, str, str]] = set()
    for sample in _samples(db, prompt_ids, run_ids):
        for mention in sample.mentions:
            sentence = mention.snippet.strip()[:600]
            if not sentence:
                continue
            sha1 = hashlib.sha1(sentence.encode("utf-8")).hexdigest()  # noqa: S324 - a key
            key = (
                sample.prompt_id,
                sample.run_id,
                sample.engine.value,
                mention.entity,
                sha1,
            )
            if key in seen:
                continue
            seen.add(key)
            rng = _seeded(sha1, mention.entity, sample.engine.value)
            polarity = _polarity(sentence, rng)
            # A client-facing card needs confidence; the judge is surer about
            # sentences that carry an opinion than about bare listings.
            confidence = round(
                rng.uniform(0.72, 0.95) if polarity != "neutral" else rng.uniform(0.55, 0.8), 2
            )
            rows.append(
                MentionJudgement(
                    prompt_id=sample.prompt_id,
                    run_id=sample.run_id,
                    engine=sample.engine,
                    captured_at=sample.captured_at,
                    entity=mention.entity,
                    term=mention.term,
                    sentence_sha1=sha1,
                    sentence=sentence,
                    status="ok",
                    polarity=polarity,
                    attributes=_attributes(sentence, polarity, rng),
                    confidence=confidence,
                    model=JUDGE_MODEL,
                    rubric_version=RUBRIC_VERSION,
                    judged_at=now,
                )
            )
            tally[polarity] = tally.get(polarity, 0) + 1
    return rows, tally


# -- crawler logs ------------------------------------------------------------------

# Bots that carry an engine, so the funnel can join their fetches to citations,
# plus a training crawler and one that no published range can verify.
_BOTS: Final = (
    ("OAI-SearchBot/1.0; +https://openai.com/searchbot", "openai", 0.30),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; "
     "+https://openai.com/bot", "openai", 0.12),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; "
     "+https://openai.com/gptbot", "openai", 0.24),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; PerplexityBot/1.0; "
     "+https://perplexity.ai/perplexitybot", "perplexity", 0.20),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ClaudeBot/1.0; "
     "+claudebot@anthropic.com", None, 0.14),
)  # fmt: skip

# Pages the engines never cite, so `fetched_not_cited` has something to say. The
# first is consulted but not cited, which is the more interesting diagnosis.
_UNCITED_PATHS: Final = (
    "/blog/ai-in-procurement-guide",
    "/blog/procurement-trends-2026",
    "/resources/whitepaper-source-to-pay",
    "/software/gep-nexxe",
)


def _vendor_ips(vendor: str | None, count: int, rng: random.Random) -> list[str]:
    """Addresses inside a vendor's published ranges, so verification resolves.

    `None` yields addresses in no published range at all — that is what an
    unverifiable crawler looks like, and the import reports it as such.
    """
    if vendor is None:
        return [f"203.0.113.{rng.randrange(1, 254)}" for _ in range(count)]
    payload = json.loads((REPO_ROOT / "src/modules/crawler_logs/ranges.json").read_text("utf-8"))
    prefixes: list[str] = []
    for source in payload["vendors"][vendor].get("sources", []):
        prefixes.extend(source.get("prefixes", []))
    out: list[str] = []
    for _ in range(count):
        network = ipaddress.ip_network(rng.choice(prefixes), strict=False)
        offset = rng.randrange(1, max(int(network.num_addresses) - 1, 2))
        out.append(str(network.network_address + offset))
    return out


def _log_lines(host: str, cited_paths: list[str], days: int, rng: random.Random) -> list[str]:
    """A plausible Nginx combined-format access log for the last `days` days."""
    lines: list[str] = []
    start = datetime.now(UTC) - timedelta(days=days)
    paths = [(p, 3.0) for p in cited_paths] + [(p, 1.4) for p in _UNCITED_PATHS]
    ip_pool = {vendor: _vendor_ips(vendor, 24, rng) for _, vendor, _ in _BOTS}
    for day in range(days):
        stamp = start + timedelta(days=day)
        # A quiet weekend and one gap, so the "covered days" strip is not a solid block.
        if stamp.weekday() == 6 and rng.random() < 0.5:
            continue
        for agent, vendor, share in _BOTS:
            for _ in range(max(1, int(rng.gauss(share * 60, 4)))):
                path, weight = rng.choices(paths, weights=[w for _, w in paths])[0]
                when = stamp + timedelta(
                    hours=rng.randrange(24), minutes=rng.randrange(60), seconds=rng.randrange(60)
                )
                status = 200
                roll = rng.random()
                if roll < 0.10:
                    status = 304
                elif roll < 0.13 and "Perplexity" in agent:
                    status = 403  # the firewall story: one vendor blocked at the edge
                elif roll < 0.15:
                    status = 404
                size = 0 if status in (304, 403) else rng.randrange(8000, 60000)
                ip = rng.choice(ip_pool[vendor])
                lines.append(
                    f"{ip} - - [{when.strftime('%d/%b/%Y:%H:%M:%S +0000')}] "
                    f'"GET {path} HTTP/1.1" {status} {size} "-" "{agent}" "{host}"'
                )
    rng.shuffle(lines)
    return lines


def _chunks(data: bytes, size: int = 1 << 16) -> Iterator[bytes]:
    for index in range(0, len(data), size):
        yield data[index : index + size]


# -- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Backfill the demo project; returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default=DEMO_PROJECT_ID)
    parser.add_argument("--days", type=int, default=60, help="Days of crawler log to generate.")
    parser.add_argument("--dry-run", action="store_true", help="Compute, write nothing.")
    parser.add_argument("--skip-sentiment", action="store_true")
    parser.add_argument("--skip-crawler-logs", action="store_true")
    parser.add_argument("--skip-locale", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    store = ProjectStore(settings.tracker_db_path)
    db = TimeSeriesDB(settings.tracker_db_path)
    project = store.get_project(args.project_id)
    now = datetime.now(UTC)
    print(f"Project {project.id} '{project.name}' in {settings.tracker_db_path}")

    with sqlite3.connect(settings.tracker_db_path) as conn:
        run_ids = [
            str(row[0])
            for row in conn.execute(
                "SELECT run_id FROM runs WHERE lob = ? ORDER BY started_at", (project.client.lob,)
            )
        ]
    print(f"  {len(run_ids)} run(s) of stored samples")

    # -- market ----------------------------------------------------------------
    if not args.skip_locale and project.locale is None:
        locale = Locale(
            country="IN",
            language="en",
            city="Mumbai",
            region="Maharashtra",
            serp_location="Mumbai, Maharashtra, India",
        )
        if not args.dry_run:
            store.update_project(args.project_id, ProjectUpdate(locale=locale))
        print(
            f"  market      -> {locale.label} (written through the store; ADR 0023 freeze bypassed)"
        )
    elif project.locale is not None:
        print(f"  market      -> already set ({project.locale.label})")

    # -- sentiment -------------------------------------------------------------
    if not args.skip_sentiment:
        prompt_ids = [p.prompt_id for p in store.list_prompts(args.project_id)]
        rows, tally = _judgements(db, prompt_ids, run_ids, now)
        if not args.dry_run:
            db.record_judgements(rows)
        share = ", ".join(f"{k} {v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1]))
        print(f"  sentiment   -> {len(rows)} judgement(s) [{share}]")
        print(f"                 model {JUDGE_MODEL}, rubric {RUBRIC_VERSION}")

    # -- crawler logs ----------------------------------------------------------
    if not args.skip_crawler_logs:
        prompt_ids = [p.prompt_id for p in store.list_prompts(args.project_id)]
        client_domain = project.client.domains[0]
        cited = sorted(
            {
                link.url
                for sample in _samples(db, prompt_ids, run_ids)
                for link in sample.citation_links
                if client_domain in link.url
            }
        )
        host = "www.gep.com"
        paths = [url.split(host, 1)[-1] for url in cited if host in url] or ["/"]
        rng = _seeded("crawler", project.id, str(args.days))
        lines = _log_lines(host, paths, args.days, rng)
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        stats = ParseStats()
        hits = iter_hits(
            _chunks(payload), stats, max_bytes=len(payload) + 1024, max_json_bytes=1 << 20
        )
        result = aggregate(
            hits,
            client_domains=list(project.client.domains),
            ranges=bundled_ranges(),
            stats=stats,
        )
        print(
            f"  crawler log -> {stats.lines} line(s), {result.matched} bot hit(s), "
            f"{result.verified_hits} verified, {len(result.rows)} aggregate row(s)"
        )
        if not args.dry_run:
            crawler = CrawlerLogStore(settings.tracker_db_path)
            try:
                record = crawler.record_import(
                    args.project_id,
                    result,
                    note="Seeded demonstration log (scripts/seed_demo_capabilities.py)",
                )
                print(
                    f"                 import {record.id} spanning "
                    f"{record.span_from} to {record.span_to}"
                )
            except DuplicateImport as exc:
                print(f"                 already imported as {exc.import_id}; nothing written")

    if args.dry_run:
        print("Dry run: nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
