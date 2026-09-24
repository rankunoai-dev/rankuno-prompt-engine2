"""The rendered document: every section present, and nothing invented."""

from __future__ import annotations

import io

from pypdf import PdfReader

from src.core.branding import Brand, contrasting_ink
from src.modules.reporting.facts import build_fact_sheet
from src.modules.reporting.narrative import template_narrative
from src.modules.reporting.pdf import cover_title, render


def _text(data: bytes) -> str:
    """Everything the PDF says, as one string."""
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def _render(project, insights, positions, previous_positions, brand=None):
    facts = build_fact_sheet(
        project, insights, positions, brand=project.brand, previous_positions=previous_positions
    )
    return render(
        facts,
        template_narrative(facts),
        brand or project.brand,
        title=cover_title(facts, "September 2026 review"),
    )


def test_the_document_carries_every_section_it_has_data_for(
    project, insights, positions, previous_positions
):
    """Cover, numbers, platforms, changes, tone, actions, pages, method."""
    data, pages = _render(project, insights, positions, previous_positions)
    text = _text(data)
    assert pages >= 3
    assert "September 2026 review" in text  # cover title
    assert "GEP" in text and "Procurement Software" in text
    assert "CITATION RATE" in text and "95% CI" in text
    assert "Platform by platform" in text
    assert "What changed" in text
    assert "How the engines describe you" in text
    assert "Recommended actions" in text
    assert "Your pages the engines cite" in text
    assert "How this was measured" in text


def test_exact_urls_reach_the_page(project, insights, positions, previous_positions):
    """A client acts on a URL, not on a domain (cycle ui-0006)."""
    text = _text(_render(project, insights, positions, previous_positions)[0])
    assert "https://www.gep.com/smart" in text
    assert "https://www.g2.com/categories/procurement" in text


def test_the_method_page_states_what_gemini_cannot_do(
    project, insights, positions, previous_positions
):
    """ADR 0023's honesty rule survives into the client-facing artefact."""
    text = _text(_render(project, insights, positions, previous_positions)[0])
    assert "Gemini has no location field" in text
    assert "billing account" in text


def test_platform_labels_are_names_not_enum_values(
    project, insights, positions, previous_positions
):
    """'Chatgpt Search' is what a raw enum looks like on a client's desk."""
    text = _text(_render(project, insights, positions, previous_positions)[0])
    assert "ChatGPT Search" in text
    assert "CHATGPT_SEARCH" not in text
    assert "Chatgpt Search" not in text


def test_a_template_narrative_says_so_and_a_model_one_names_the_model(
    project, insights, positions, previous_positions
):
    """The reader can always tell who wrote the words."""
    facts = build_fact_sheet(
        project, insights, positions, brand=project.brand, previous_positions=previous_positions
    )
    words = template_narrative(facts)
    text = _text(render(facts, words, project.brand, title="t")[0])
    assert "without a model" in text

    from src.modules.reporting.schemas import NarrativeSource

    words.source = NarrativeSource.MODEL
    words.model = "claude-sonnet-5"
    text = _text(render(facts, words, project.brand, title="t")[0])
    assert "drafted by claude-sonnet-5" in text
    assert "checked against them" in text


def test_spend_appears_only_when_the_brand_asked_for_it(
    project, insights, positions, previous_positions
):
    """A client report should not show what the agency pays per crawl."""
    facts = build_fact_sheet(
        project,
        insights,
        positions,
        brand=Brand(show_spend=True),
        previous_positions=previous_positions,
        spend_usd=12.40,
    )
    assert "$12.40" in _text(render(facts, template_narrative(facts), Brand(), title="t")[0])

    quiet = build_fact_sheet(
        project,
        insights,
        positions,
        brand=Brand(),
        previous_positions=previous_positions,
        spend_usd=12.40,
    )
    assert "12.40" not in _text(render(quiet, template_narrative(quiet), Brand(), title="t")[0])


def test_a_dark_brand_colour_gets_light_ink_and_a_pale_one_dark(
    project, insights, positions, previous_positions
):
    """The cover title has to stay readable on whatever colour a client uses."""
    assert contrasting_ink("#1f3a5f") == "#ffffff"
    assert contrasting_ink("#f5e663") == "#000000"
    data, _ = _render(
        project, insights, positions, previous_positions, brand=Brand(primary_colour="#f5e663")
    )
    assert b"%PDF" in data[:8]


def test_engine_text_cannot_inject_layout_markup(project, insights, positions, previous_positions):
    """A quote is data; ReportLab's markup must not open from it."""
    insights.sentiment[0].worst[0].text = "<para><b>injected</b> & unbalanced"
    data, _ = _render(project, insights, positions, previous_positions)
    assert "injected" in _text(data)  # rendered as text, not interpreted
