"""Sentiment profiles, mention context and the negative_claim card (ADR 0021)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.integrations.schemas import Citation, CitationClaim, Engine
from src.modules.control_plane.actions import ActionStateStore
from src.modules.control_plane.insights import InsightEngine, _block_size, _container
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.schemas import ProjectRunRecord
from src.modules.prompt_tracking.schemas import AnswerSample, MentionJudgement, MentionSnippet
from src.modules.prompt_tracking.sentiment import RUBRIC_VERSION, sentence_key
from tests.modules.control_plane.conftest import NOW
from tests.modules.control_plane.test_insights import _record, _snapshot

G2 = "https://www.g2.com/categories/procurement"
COUPA = "https://www.coupa.com/p2p"
GEP_BAD = "Pricing for GEP SMART is on the high side for mid-market buyers."
GEP_LIST = "GEP SMART"
ANSWER = (
    "Top procurement suites [1]:\n"
    "1. Coupa\n"
    "2. GEP SMART\n"
    "3. Ivalua\n"
    "\n"
    f"{GEP_BAD} [2] Coupa is the safer choice for spend analysis."
)


def _sample(pid: str, engine: Engine, at, run_id: str) -> AnswerSample:
    return AnswerSample(
        prompt_id=pid,
        run_id=run_id,
        engine=engine,
        model="m",
        captured_at=at,
        web_triggered=True,
        client_cited=False,
        cited_domains=["g2.com", "coupa.com"],
        citation_links=[
            Citation(url=G2, domain="g2.com", title="G2", position=1),
            Citation(url=COUPA, domain="coupa.com", title="Coupa", position=2),
        ],
        mention_detected=True,
        mentions=[
            MentionSnippet(entity="client", term="GEP SMART", snippet=GEP_LIST),
            MentionSnippet(entity="client", term="GEP SMART", snippet=GEP_BAD),
            MentionSnippet(entity="coupa", term="Coupa", snippet="Coupa"),
        ],
        answer_text=ANSWER,
        citation_claims=[CitationClaim(url=COUPA, sentence=GEP_BAD + " [2]", start=60, end=130)],
    )


def _judgement(
    pid: str,
    engine: Engine,
    at,
    run_id: str,
    sentence: str,
    polarity: str,
    *,
    entity: str = "client",
    status: str = "ok",
    rubric: str = RUBRIC_VERSION,
    confidence: float = 0.9,
    attributes: list[str] | None = None,
) -> MentionJudgement:
    return MentionJudgement(
        prompt_id=pid,
        run_id=run_id,
        engine=engine,
        captured_at=at,
        entity=entity,
        term="GEP SMART" if entity == "client" else "Coupa",
        sentence_sha1=sentence_key(sentence),
        sentence=sentence,
        status=status,
        polarity=polarity,
        attributes=attributes or [],
        confidence=confidence,
        model="claude-haiku-4-5",
        rubric_version=rubric,
    )


@pytest.fixture
def judged(store, db, project, prompts):
    """One crawl on Perplexity with a list mention, a negative sourced sentence, judgements."""
    positions = PositionStore(db.path)
    for prompt in prompts:
        db.upsert_prompt(_record(prompt.prompt_id, project.client.lob, None), "GEP")
    at = NOW
    run = "run-0"
    rows: list[MentionJudgement] = []
    for prompt in prompts:
        db.record_snapshot(prompt.prompt_id, _snapshot(Engine.PERPLEXITY, at, 0, mention=1.0), run)
        db.record_samples(
            prompt.prompt_id, [_sample(prompt.prompt_id, Engine.PERPLEXITY, at, run)], run
        )
        rows += [
            _judgement(prompt.prompt_id, Engine.PERPLEXITY, at, run, GEP_LIST, "neutral"),
            _judgement(
                prompt.prompt_id,
                Engine.PERPLEXITY,
                at,
                run,
                GEP_BAD,
                "negative",
                attributes=["expensive", "mid-market pricing"],
            ),
            _judgement(
                prompt.prompt_id, Engine.PERPLEXITY, at, run, "Coupa", "positive", entity="coupa"
            ),
        ]
    # one row under an older rubric and one unscored: both count as unscored, never as verdicts
    rows.append(
        _judgement(
            prompts[0].prompt_id,
            Engine.PERPLEXITY,
            at,
            run,
            "old rubric row",
            "negative",
            rubric="2020.1",
        )
    )
    rows.append(
        _judgement(
            prompts[0].prompt_id,
            Engine.PERPLEXITY,
            at,
            run,
            "no verdict",
            "neutral",
            status="unscored",
        )
    )
    db.record_judgements(rows)
    positions.record_project_run(
        ProjectRunRecord(
            id="crawl-0000",
            project_id=project.id,
            started_at=at,
            finished_at=at + timedelta(minutes=5),
            run_ids=[run],
            prompts_run=len(prompts),
            batches=1,
            statuses=["success"],
            full=True,
        )
    )
    return positions


def _engine(db, positions):
    return InsightEngine(db, positions, ActionStateStore(db.path))


def test_sentiment_profiles_split_by_engine_and_entity_with_band(db, project, prompts, judged):
    view = _engine(db, judged).build(project, prompts)
    assert view.sentiment_coverage.configured
    assert view.sentiment_coverage.judged == 6 and view.sentiment_coverage.unscored == 2
    assert view.sentiment_coverage.model == "claude-haiku-4-5"
    client = next(
        p for p in view.sentiment if p.engine is Engine.PERPLEXITY and p.entity == "client"
    )
    assert (client.positive, client.neutral, client.negative) == (0, 2, 2)
    assert client.unscored == 2
    assert client.negative_share == 0.5
    assert (
        client.negative_share_low is not None
        and client.negative_share_low < 0.5 < client.negative_share_high
    )
    assert [a.attribute for a in client.attributes] == ["expensive", "mid-market pricing"]
    assert client.attributes[0].count == 2 and client.attributes[0].example == GEP_BAD
    assert client.worst[0].text == GEP_BAD and client.worst[0].url == COUPA
    coupa = next(p for p in view.sentiment if p.entity == "coupa")
    assert coupa.positive == 2 and coupa.negative_share == 0.0
    # engines with no rows still get an empty client profile so the UI can say "not scored"
    empty = next(
        p for p in view.sentiment if p.engine is Engine.CHATGPT_SEARCH and p.entity == "client"
    )
    assert empty.judged == 0 and empty.negative_share_low is None


def test_mention_context_reads_list_membership_and_attached_source(db, project, prompts, judged):
    view = _engine(db, judged).build(project, prompts)
    ctx = [c for c in view.mention_context if c.prompt_id == prompts[0].prompt_id]
    listed = next(c for c in ctx if c.sentence == GEP_LIST)
    assert listed.container == "list" and listed.listed_with == 2  # three items in the block
    assert listed.first_third is True
    assert listed.sourced_via_domain is None  # a list item with no marker and no claim
    assert listed.polarity == "neutral"
    bad = next(c for c in ctx if c.sentence == GEP_BAD)
    assert bad.container == "prose" and bad.first_third is False
    assert bad.sourced_via_domain == "coupa.com" and bad.sourced_via_class == "competitor"
    assert bad.polarity == "negative"
    coupa = next(c for c in ctx if c.entity == "coupa")
    assert coupa.container == "list" and coupa.polarity == "positive"


def test_negative_claim_card_carries_the_quote_and_its_source(db, project, prompts, judged):
    view = _engine(db, judged).build(project, prompts)
    cards = [a for a in view.actions if a.type == "negative_claim"]
    assert len(cards) == 1
    card = cards[0]
    assert card.engine is Engine.PERPLEXITY
    assert "describes the brand negatively" in card.title
    assert "high side" in card.prescription and "2 answer(s)" in card.prescription
    assert card.evidence.quotes[0].text == GEP_BAD and card.evidence.quotes[0].url == COUPA
    assert card.evidence.urls == [COUPA]
    assert card.evidence.numbers["negative_sentences"] == 2.0
    assert set(card.prompt_ids) == {p.prompt_id for p in prompts}


def test_low_confidence_negatives_do_not_raise_a_card(db, project, prompts, judged):
    db.record_judgements(
        [
            _judgement(
                p.prompt_id, Engine.PERPLEXITY, NOW, "run-0", GEP_BAD, "negative", confidence=0.3
            )
            for p in prompts
        ]
    )
    view = _engine(db, judged).build(project, prompts)
    assert not [a for a in view.actions if a.type == "negative_claim"]
    client = next(
        p for p in view.sentiment if p.engine is Engine.PERPLEXITY and p.entity == "client"
    )
    assert client.negative == 2  # still counted in the profile, just not actionable


def test_no_judgements_means_not_configured(db, project, prompts, store):
    positions = PositionStore(db.path)
    view = _engine(db, positions).build(project, prompts)
    assert view.sentiment == [] and view.sentiment_coverage.configured is False
    assert view.mention_context == []


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("- GEP SMART", "list"),
        ("* Coupa", "list"),
        ("• Ivalua", "list"),
        ("1. GEP SMART", "list"),
        ("12) Coupa", "list"),
        ("2024 saw a shift in procurement.", "prose"),
        ("| GEP | strong |", "table"),
        ("## Vendors", "heading"),
        ("GEP SMART is recommended.", "prose"),
    ],
)
def test_container_detection(line, kind):
    assert _container(line) == kind


def test_block_size_counts_items_not_separator_rows():
    lines = ["intro", "| a | b |", "|---|---|", "| GEP | 1 |", "| Coupa | 2 |", "outro"]
    assert _block_size(lines, 3, "table") == 3
    assert _block_size(["- a", "- b", "", "- c"], 0, "list") == 2
    assert _block_size(["x"], 0, "prose") == 1
