"""Fixtures for the reporting tests: one window of analysis, no vendor calls."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.branding import Brand
from src.integrations.schemas import Engine
from src.modules.control_plane.schemas import (
    ActionCard,
    ActionEvidence,
    AttributeCount,
    ConsolidatedPosition,
    Consolidation,
    EngineHealth,
    EvidenceQuote,
    InsightBasis,
    InsightChange,
    InsightsView,
    PageInventory,
    PositionsView,
    Project,
    SentimentCoverage,
    SentimentProfile,
)

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
CLIENT = {
    "brand_name": "GEP",
    "aliases": ["GEP SMART"],
    "domains": ["gep.com"],
    "competitor_domains": ["coupa.com", "sap.com"],
    "lob": "Procurement Software",
    "seed_keywords": ["procurement software"],
}


def position(
    prompt_id: str,
    engine: Engine,
    *,
    samples: int,
    cited: int,
    mentions: int = 0,
    failed: int = 0,
    domains: dict[str, float] | None = None,
) -> ConsolidatedPosition:
    """One consolidated row, with the counts the report pools."""
    answered = samples - failed
    return ConsolidatedPosition(
        prompt_id=prompt_id,
        engine=engine,
        runs=3,
        first_run_at=NOW,
        last_run_at=NOW,
        samples=samples,
        failed_samples=failed,
        cited_samples=cited,
        citation_rate=cited / answered if answered else 0.0,
        cited=cited > 0,
        mention_samples=mentions,
        mention_rate=mentions / answered if answered else 0.0,
        mentioned=mentions > 0,
        cited_domain_share=domains or {},
    )


@pytest.fixture
def project(tmp_path) -> Project:
    """A project with a brand, built through the real store."""
    from src.modules.control_plane.schemas import ProjectCreate
    from src.modules.control_plane.store import ProjectStore

    store = ProjectStore(tmp_path / "cp.sqlite")
    return store.create_project(
        ProjectCreate(
            name="GEP procurement",
            client=CLIENT,
            brand=Brand(agency_name="RankUno", primary_colour="#1f3a5f"),
        )
    )


@pytest.fixture
def positions() -> PositionsView:
    """The current window: two prompts on two platforms."""
    rows = [
        position(
            "p1",
            Engine.CHATGPT_SEARCH,
            samples=30,
            cited=18,
            mentions=24,
            domains={"gep.com": 0.2, "sap.com": 0.5},
        ),
        position(
            "p2",
            Engine.CHATGPT_SEARCH,
            samples=30,
            cited=12,
            mentions=20,
            domains={"gep.com": 0.1, "coupa.com": 0.4},
        ),
        position("p1", Engine.GEMINI, samples=30, cited=0, mentions=3),
        position("p2", Engine.GEMINI, samples=30, cited=0, mentions=2),
    ]
    consolidation = Consolidation(
        id="cons0002",
        project_id="proj0001",
        consolidated_at=NOW,
        window_runs=3,
        first_run_at=datetime(2026, 9, 10, tzinfo=UTC),
        last_run_at=datetime(2026, 9, 22, tzinfo=UTC),
        prompts=2,
        trigger="auto",
    )
    history = [
        consolidation,
        Consolidation(
            id="cons0001",
            project_id="proj0001",
            consolidated_at=datetime(2026, 9, 9, tzinfo=UTC),
            window_runs=3,
            prompts=2,
            trigger="auto",
        ),
    ]
    return PositionsView(consolidation=consolidation, positions=rows, history=history)


@pytest.fixture
def previous_positions() -> list[ConsolidatedPosition]:
    """The window before: ChatGPT cited far more often."""
    return [
        position(
            "p1",
            Engine.CHATGPT_SEARCH,
            samples=30,
            cited=27,
            mentions=28,
            domains={"gep.com": 0.5, "sap.com": 0.3},
        ),
        position(
            "p2", Engine.CHATGPT_SEARCH, samples=30, cited=25, mentions=27, domains={"gep.com": 0.4}
        ),
        position("p1", Engine.GEMINI, samples=30, cited=6, mentions=9),
        position("p2", Engine.GEMINI, samples=30, cited=5, mentions=8),
    ]


@pytest.fixture
def insights() -> InsightsView:
    """Health, changes, one action card, one sentiment profile, two pages."""
    return InsightsView(
        generated_at=NOW,
        basis=InsightBasis(
            consolidation_id="cons0002",
            computed_from="consolidation",
            crawls=3,
            samples=120,
            low_confidence=False,
        ),
        health=[
            EngineHealth(
                engine=Engine.CHATGPT_SEARCH,
                verdict="present",
                cited_rate=0.5,
                cited_rate_low=0.38,
                cited_rate_high=0.62,
                mention_rate=0.73,
                delta_cited_rate=-0.37,
                samples=60,
                crawls=3,
                volatility=0.3,
                prompts=2,
            ),
            EngineHealth(
                engine=Engine.GEMINI,
                verdict="invisible",
                losing_to="sap.com",
                cited_rate=0.0,
                mention_rate=0.08,
                samples=60,
                crawls=3,
                volatility=0.0,
                prompts=2,
            ),
        ],
        changes=[
            InsightChange(
                kind="flip_down",
                prompt_id="p2",
                prompt_text="Is GEP SMART good for source to pay?",
                engine=Engine.GEMINI,
                before="40%",
                after="0%",
                text="no longer cited",
            ),
            InsightChange(
                kind="new_competitor",
                prompt_id="p1",
                prompt_text="What is the best procurement software?",
                engine=Engine.CHATGPT_SEARCH,
                before="0%",
                after="coupa.com",
                text="coupa.com now cited",
            ),
        ],
        actions=[
            ActionCard(
                id="act1",
                type="negative_claim",
                title="ChatGPT Search describes the brand negatively in 'pricing'",
                prescription="Correct the record on the page these answers cite.",
                impact_score=9.1,
                engine=Engine.CHATGPT_SEARCH,
                subtopic="pricing",
                metric="cited_rate",
                evidence=ActionEvidence(
                    quotes=[
                        EvidenceQuote(
                            text="Some users report GEP's implementation timelines run long.",
                            engine=Engine.CHATGPT_SEARCH,
                            url="https://www.g2.com/products/gep-smart/reviews",
                        )
                    ],
                    urls=["https://www.g2.com/products/gep-smart/reviews"],
                ),
            )
        ],
        client_pages=[
            PageInventory(
                url="https://www.gep.com/smart",
                domain="gep.com",
                citations=12,
                engines=[Engine.CHATGPT_SEARCH],
                prompts=2,
                is_client=True,
            )
        ],
        winning_pages=[
            PageInventory(
                url="https://www.g2.com/categories/procurement",
                domain="g2.com",
                citations=30,
                engines=[Engine.CHATGPT_SEARCH],
                prompts=2,
                is_client=False,
            )
        ],
        sentiment=[
            SentimentProfile(
                engine=Engine.CHATGPT_SEARCH,
                entity="client",
                judged=44,
                positive=20,
                neutral=17,
                negative=7,
                not_about_brand=0,
                unscored=0,
                negative_share=0.16,
                negative_share_low=0.08,
                negative_share_high=0.28,
                attributes=[
                    AttributeCount(attribute="pricing", count=4, example="pricing is opaque"),
                    AttributeCount(attribute="support", count=3, example="support is slow"),
                ],
                worst=[
                    EvidenceQuote(
                        text="Some users report GEP's implementation timelines run long.",
                        engine=Engine.CHATGPT_SEARCH,
                        url="https://www.g2.com/products/gep-smart/reviews",
                    )
                ],
                model="claude-haiku-4-5",
                rubric_version="2026-09-23.1",
            )
        ],
        sentiment_coverage=SentimentCoverage(configured=True, judged=44, unscored=0),
    )
