"""Draws the report with ReportLab (ADR 0024).

ReportLab rather than an HTML engine for three reasons: it is a pure-Python
wheel, so the `python:3.11-slim` runtime image needs no system libraries and
the operator's Windows machine needs no GTK; it ships its own chart package,
so no second graphics dependency; and the layout is code, so a test can assert
that a section exists instead of comparing pixels.

Everything drawn here comes from a `FactSheet` and a `Narrative`. This module
computes no metric of its own — if a number is not in the fact sheet it does
not appear on the page.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from src.core.branding import Brand
from src.integrations.schemas import Engine
from src.modules.reporting.facts import ENGINE_LABELS, window_label
from src.modules.reporting.schemas import FactSheet, Kpi, Narrative, NarrativeSource

__all__ = ["PAGE_HEIGHT", "PAGE_WIDTH", "render"]

PAGE_WIDTH, PAGE_HEIGHT = A4
_MARGIN: Final = 18 * mm
_CONTENT_WIDTH: Final = PAGE_WIDTH - 2 * _MARGIN
_MUTED: Final = colors.HexColor("#5b6472")
_RULE: Final = colors.HexColor("#d7dce3")
_BG: Final = colors.HexColor("#f4f6f9")
_MAX_LOGO_H: Final = 18 * mm


def _styles(primary: colors.Color) -> dict[str, ParagraphStyle]:
    """Paragraph styles, tinted with the client's colour where it reads well."""
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    return {
        "body": body,
        "small": ParagraphStyle("Small", parent=body, fontSize=8, leading=11, textColor=_MUTED),
        "h1": ParagraphStyle(
            "H1",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=primary,
            spaceBefore=2,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=15,
            textColor=primary,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "lead": ParagraphStyle("Lead", parent=body, fontSize=11, leading=16),
        "cell": ParagraphStyle("Cell", parent=body, fontSize=8.5, leading=11, spaceAfter=0),
        "cellbold": ParagraphStyle(
            "CellBold",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            spaceAfter=0,
        ),
        "quote": ParagraphStyle(
            "Quote",
            parent=body,
            fontName="Helvetica-Oblique",
            fontSize=9,
            leading=13,
            leftIndent=8,
            textColor=colors.HexColor("#2b3340"),
        ),
    }


def _pct(value: float | None) -> str:
    """Whole-number percentage, the only rate format the report uses."""
    return "—" if value is None else f"{round(value * 100)}%"


def _band(low: float | None, high: float | None) -> str:
    """The 95% interval as printed under a headline number."""
    if low is None or high is None:
        return ""
    return f"95% CI {round(low * 100)}–{round(high * 100)}%"


def _delta(value: float | None) -> str:
    """Signed change in percentage points, or an em dash."""
    if value is None:
        return "—"
    points = round(value * 100)
    if points == 0:
        return "no change"
    return f"{'+' if points > 0 else ''}{points} pts"


def _label(engine: Engine) -> str:
    """The platform's display name; never the raw enum value."""
    return ENGINE_LABELS.get(engine, engine.value.replace("_", " ").title())


def _escape(text: str) -> str:
    """Engine text is data: never let it open a ReportLab markup tag."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Rule(Flowable):
    """A hairline the width of the frame."""

    def __init__(self, colour: colors.Color = _RULE, thickness: float = 0.6) -> None:
        """Keep the colour and thickness for `draw`."""
        super().__init__()
        self._colour = colour
        self._thickness = thickness
        self.width = _CONTENT_WIDTH
        self.height = thickness

    def draw(self) -> None:
        """Stroke the line."""
        self.canv.setStrokeColor(self._colour)
        self.canv.setLineWidth(self._thickness)
        self.canv.line(0, 0, self.width, 0)


def _draw_tiles(
    canvas: Canvas,
    kpis: list[Kpi],
    primary: colors.Color,
    *,
    x0: float,
    y0: float,
    width: float,
    height: float,
    on_dark: bool = False,
) -> None:
    """Draw the headline numbers as tiles, left to right, from `(x0, y0)`.

    Shared by the cover (on the brand band) and the summary page (on white),
    so a number is formatted identically wherever the reader meets it.
    """
    if not kpis:
        return
    gap = 4 * mm
    tile = (width - gap * (len(kpis) - 1)) / len(kpis)
    label_ink = colors.Color(1, 1, 1, 0.75) if on_dark else _MUTED
    value_ink = colors.white if on_dark else primary
    for index, kpi in enumerate(kpis):
        x = x0 + index * (tile + gap)
        if not on_dark:
            canvas.setFillColor(_BG)
            canvas.roundRect(x, y0, tile, height, 2 * mm, stroke=0, fill=1)
        canvas.setFillColor(label_ink)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(
            x + (4 * mm if not on_dark else 0), y0 + height - 6 * mm, kpi.label.upper()
        )
        canvas.setFillColor(value_ink)
        canvas.setFont("Helvetica-Bold", 18)
        value = _pct(kpi.value) if kpi.unit == "percent" else f"{int(kpi.value)}"
        canvas.drawString(x + (4 * mm if not on_dark else 0), y0 + height - 14 * mm, value)
        canvas.setFillColor(label_ink)
        canvas.setFont("Helvetica", 7)
        band = _band(kpi.low, kpi.high)
        if band:
            canvas.drawString(x + (4 * mm if not on_dark else 0), y0 + height - 18.5 * mm, band)
        if kpi.delta is not None:
            if on_dark:
                canvas.setFillColor(colors.Color(1, 1, 1, 0.85))
            else:
                canvas.setFillColor(
                    colors.HexColor("#1f7a4d") if kpi.delta >= 0 else colors.HexColor("#b3261e")
                )
            canvas.setFont("Helvetica-Bold", 7.5)
            canvas.drawString(
                x + (4 * mm if not on_dark else 0), y0 + height - 22.5 * mm, _delta(kpi.delta)
            )


class _KpiTiles(Flowable):
    """The headline numbers on the summary page.

    A tile carries the value, its confidence band and the change on the
    previous window. The band is not decoration: a rate without one invites a
    reader to believe a two-point move that the sampling cannot support.
    """

    def __init__(self, facts: FactSheet, primary: colors.Color) -> None:
        """Lay out one tile per KPI across the content width."""
        super().__init__()
        self._facts = facts
        self._primary = primary
        self.width = _CONTENT_WIDTH
        self.height = 26 * mm

    def draw(self) -> None:
        """Draw every tile left to right."""
        _draw_tiles(
            self.canv,
            self._facts.kpis[:4],
            self._primary,
            x0=0,
            y0=0,
            width=self.width,
            height=self.height,
        )


def _bar_chart(
    rows: list[tuple[str, float]], primary: colors.Color, *, highlight: set[str] | None = None
) -> Drawing:
    """A horizontal bar chart of rates, labelled with its own values.

    Horizontal because the categories are platform and domain names, which do
    not fit under vertical bars at this width.
    """
    height = max(30, 16 * len(rows) + 22)
    drawing = Drawing(_CONTENT_WIDTH, height)
    chart = HorizontalBarChart()
    # ReportLab stacks the first category at the BOTTOM of the axis. The caller
    # passes rows in reading order (biggest first), so they are reversed here
    # and every later index — colours and value labels — uses the same order.
    ordered = list(reversed(rows))
    chart.x = 92
    chart.y = 12
    chart.height = height - 24
    chart.width = _CONTENT_WIDTH - 150
    chart.data = [[round(value * 100, 1) for _, value in ordered]]
    chart.categoryAxis.categoryNames = [label[:28] for label, _ in ordered]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.labels.dx = -4
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max([round(v * 100) for _, v in rows] + [10]) * 1.15
    chart.valueAxis.visible = False
    chart.barSpacing = 3
    chart.barWidth = 9
    chart.bars.strokeWidth = 0
    dim = colors.HexColor("#b9c2cf")
    for index, (label, _) in enumerate(ordered):
        chart.bars[(0, index)].fillColor = (
            primary if highlight is None or label in highlight else dim
        )
    drawing.add(chart)
    for index, (_, value) in enumerate(ordered):
        y = chart.y + (index + 0.5) * (chart.height / max(len(ordered), 1)) - 3
        drawing.add(
            String(
                _CONTENT_WIDTH - 52,
                y,
                f"{round(value * 100)}%",
                fontName="Helvetica-Bold",
                fontSize=8.5,
                fillColor=colors.HexColor("#2b3340"),
            )
        )
    return drawing


def _table(data: list[list[Any]], widths: list[float], primary: colors.Color) -> Table:
    """A header-banded table in the house style."""
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), primary),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, _RULE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _BG]),
            ]
        )
    )
    return table


class _Doc(BaseDocTemplate):
    """Two page templates: a bare cover and the running body."""

    def __init__(self, buffer: io.BytesIO, *, brand: Brand, footer: str) -> None:
        """Register the frames and remember what the footer prints."""
        super().__init__(
            buffer,
            pagesize=A4,
            leftMargin=_MARGIN,
            rightMargin=_MARGIN,
            topMargin=_MARGIN,
            bottomMargin=_MARGIN,
            title=footer,
            author=brand.agency_name or "",
        )
        self._brand = brand
        self._footer = footer
        frame = Frame(
            _MARGIN, _MARGIN + 8 * mm, _CONTENT_WIDTH, PAGE_HEIGHT - 2 * _MARGIN - 8 * mm, id="body"
        )
        cover = Frame(_MARGIN, _MARGIN, _CONTENT_WIDTH, PAGE_HEIGHT - 2 * _MARGIN, id="cover")
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[cover]),
                PageTemplate(id="body", frames=[frame], onPage=self._decorate),
            ]
        )

    def _decorate(self, canvas: Canvas, _doc: BaseDocTemplate) -> None:
        """Footer rule, page number and the agency's line on every body page."""
        canvas.saveState()
        canvas.setStrokeColor(_RULE)
        canvas.setLineWidth(0.5)
        canvas.line(_MARGIN, _MARGIN + 6 * mm, PAGE_WIDTH - _MARGIN, _MARGIN + 6 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_MUTED)
        canvas.drawString(_MARGIN, _MARGIN + 1.5 * mm, self._footer[:110])
        canvas.drawRightString(
            PAGE_WIDTH - _MARGIN, _MARGIN + 1.5 * mm, f"Page {canvas.getPageNumber()}"
        )
        canvas.restoreState()


def _cover(canvas: Canvas, facts: FactSheet, brand: Brand, title: str, logo: Path | None) -> None:
    """Paint the cover: brand band, headline numbers, then the run's details."""
    primary = colors.HexColor(brand.primary_colour)
    band = 118 * mm
    canvas.setFillColor(primary)
    canvas.rect(0, PAGE_HEIGHT - band, PAGE_WIDTH, band, stroke=0, fill=1)
    ink = colors.HexColor(brand.ink)
    if logo is not None:
        try:
            image = ImageReader(str(logo))
            width, height = image.getSize()
            scale = min(_MAX_LOGO_H / height, 60 * mm / width)
            canvas.drawImage(
                image,
                _MARGIN,
                PAGE_HEIGHT - 32 * mm,
                width=width * scale,
                height=height * scale,
                mask="auto",
                preserveAspectRatio=True,
                anchor="sw",
            )
        except OSError:  # a corrupt file must not cost the whole report
            pass
    canvas.setFillColor(ink)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(_MARGIN, PAGE_HEIGHT - 44 * mm, facts.client_brand.upper())
    canvas.setFont("Helvetica-Bold", 26)
    for index, line in enumerate(_wrap(title, 34)[:2]):
        canvas.drawString(_MARGIN, PAGE_HEIGHT - (54 + index * 10) * mm, line)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(_MARGIN, PAGE_HEIGHT - 76 * mm, window_label(facts.window))
    # The same tiles the summary page opens with, so the cover leads with the
    # number the reader came for rather than a page of labels.
    _draw_tiles(
        canvas,
        facts.kpis[:4],
        primary,
        x0=_MARGIN,
        y0=PAGE_HEIGHT - band + 8 * mm,
        width=_CONTENT_WIDTH,
        height=26 * mm,
        on_dark=True,
    )
    rows = [
        ("Line of business", facts.lob),
        ("Market", facts.locale_label),
        ("Platforms tracked", ", ".join(engine.label for engine in facts.engines) or "—"),
        ("Prompts", str(facts.window.prompts)),
        ("Answers sampled", str(facts.window.samples)),
        ("Crawls in window", str(facts.window.crawls)),
        ("Prepared", facts.generated_at.strftime("%d %B %Y")),
    ]
    if brand.agency_name:
        rows.append(("Prepared by", brand.agency_name))
    top = PAGE_HEIGHT - band - 18 * mm
    for index, (label, value) in enumerate(rows):
        y = top - index * 9 * mm
        canvas.setFillColor(_MUTED)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(_MARGIN, y, label.upper())
        canvas.setFillColor(colors.HexColor("#2b3340"))
        canvas.setFont("Helvetica", 10)
        canvas.drawString(_MARGIN + 46 * mm, y, value[:70])
        canvas.setStrokeColor(_RULE)
        canvas.setLineWidth(0.4)
        canvas.line(_MARGIN, y - 3 * mm, PAGE_WIDTH - _MARGIN, y - 3 * mm)
    if facts.window.low_confidence:
        canvas.setFillColor(colors.HexColor("#8a5a00"))
        canvas.setFont("Helvetica-Oblique", 8.5)
        canvas.drawString(
            _MARGIN,
            _MARGIN + 12 * mm,
            "Early window: fewer crawls than the consolidation asks for. Treat as early signals.",
        )
    if brand.footer_note:
        canvas.setFillColor(_MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(_MARGIN, _MARGIN + 5 * mm, brand.footer_note[:120])


def _wrap(text: str, width: int) -> list[str]:
    """Greedy wrap for the cover title, which is drawn on the canvas."""
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) > width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _summary_story(
    facts: FactSheet, narrative: Narrative, style: dict[str, ParagraphStyle], primary: colors.Color
) -> list[Flowable]:
    """Page one: the numbers, then the words, then what to do."""
    story: list[Flowable] = [
        Paragraph(_escape(narrative.headline), style["h1"]),
        _KpiTiles(facts, primary),
        Spacer(1, 6 * mm),
    ]
    for block in narrative.summary.split("\n\n"):
        if block.strip():
            story.append(Paragraph(_escape(block.strip()), style["lead"]))
    columns: list[list[Any]] = []
    for label, items in (
        ("What is working", narrative.wins),
        ("What is at risk", narrative.risks),
        ("Next steps", narrative.next_steps),
    ):
        cell = [Paragraph(label.upper(), style["cellbold"])]
        cell += [Paragraph(f"• {_escape(item)}", style["cell"]) for item in items] or [
            Paragraph("—", style["cell"])
        ]
        columns.append(cell)
    if any(len(column) > 1 for column in columns):
        story.append(Spacer(1, 4 * mm))
        width = _CONTENT_WIDTH / 3
        grid = Table([columns], colWidths=[width] * 3, hAlign="LEFT")
        grid.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("LINEABOVE", (0, 0), (-1, 0), 0.6, _RULE),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(grid)
    return story


def _platform_story(
    facts: FactSheet, style: dict[str, ParagraphStyle], primary: colors.Color
) -> list[Flowable]:
    """Per-platform standing, as a table and a chart of the same numbers."""
    if not facts.engines:
        return []
    header = ["Platform", "Verdict", "Cited", "95% CI", "Mentioned", "Change", "Answers"]
    rows: list[list[Any]] = [[Paragraph(h, style["cellbold"]) for h in header]]
    for engine in facts.engines:
        verdict = engine.verdict + (f" to {engine.losing_to}" if engine.losing_to else "")
        rows.append(
            [
                Paragraph(_escape(engine.label), style["cell"]),
                Paragraph(_escape(verdict), style["cell"]),
                Paragraph(_pct(engine.cited_rate), style["cellbold"]),
                Paragraph(
                    _band(engine.cited_rate_low, engine.cited_rate_high) or "—", style["cell"]
                ),
                Paragraph(_pct(engine.mention_rate), style["cell"]),
                Paragraph(_delta(engine.delta_cited_rate), style["cell"]),
                Paragraph(str(engine.samples), style["cell"]),
            ]
        )
    widths = [34 * mm, 30 * mm, 16 * mm, 28 * mm, 22 * mm, 20 * mm, 18 * mm]
    story: list[Flowable] = [
        Paragraph("Platform by platform", style["h2"]),
        _table(rows, widths, primary),
        Spacer(1, 3 * mm),
        Paragraph(
            "Cited means the answer linked the brand; mentioned means it named the brand, "
            "linked or not. The interval is where the true rate sits 95 times in 100 at this "
            "sample size.",
            style["small"],
        ),
    ]
    story.append(Spacer(1, 4 * mm))
    story.append(_bar_chart([(e.label, e.cited_rate) for e in facts.engines], primary))
    if facts.competitors:
        story.append(Paragraph("Who the answers cite", style["h2"]))
        client = {c.domain for c in facts.competitors if c.is_client}
        story.append(
            _bar_chart([(c.domain, c.share) for c in facts.competitors], primary, highlight=client)
        )
        story.append(
            Paragraph(
                "Share of every source these answers cited. The brand's own domains are "
                "highlighted.",
                style["small"],
            )
        )
    return story


def _changes_story(
    facts: FactSheet, style: dict[str, ParagraphStyle], primary: colors.Color
) -> list[Flowable]:
    """What moved since the previous window."""
    if not facts.changes:
        return []
    header = ["Prompt", "Platform", "Was", "Now", "What happened"]
    rows: list[list[Any]] = [[Paragraph(h, style["cellbold"]) for h in header]]
    for change in facts.changes:
        rows.append(
            [
                Paragraph(_escape(change.prompt_text[:90]), style["cell"]),
                Paragraph(_escape(_label(change.engine)), style["cell"]),
                Paragraph(_escape(change.before), style["cell"]),
                Paragraph(_escape(change.after), style["cellbold"]),
                Paragraph(_escape(change.text), style["cell"]),
            ]
        )
    return [
        Paragraph("What changed", style["h2"]),
        _table(rows, [58 * mm, 26 * mm, 16 * mm, 16 * mm, 52 * mm], primary),
    ]


def _sentiment_story(
    facts: FactSheet, style: dict[str, ParagraphStyle], primary: colors.Color
) -> list[Flowable]:
    """How the engines describe the brand, when a judge scored the window."""
    if not facts.sentiment:
        return []
    header = ["Platform", "Negative share", "95% CI", "Sentences", "Themes"]
    rows: list[list[Any]] = [[Paragraph(h, style["cellbold"]) for h in header]]
    for entry in facts.sentiment:
        rows.append(
            [
                Paragraph(_escape(_label(entry.engine)), style["cell"]),
                Paragraph(_pct(entry.negative_share), style["cellbold"]),
                Paragraph(
                    _band(entry.negative_share_low, entry.negative_share_high) or "—", style["cell"]
                ),
                Paragraph(str(entry.judged), style["cell"]),
                Paragraph(_escape(", ".join(entry.attributes) or "—"), style["cell"]),
            ]
        )
    story: list[Flowable] = [
        Paragraph("How the engines describe you", style["h2"]),
        _table(rows, [32 * mm, 26 * mm, 28 * mm, 22 * mm, 60 * mm], primary),
    ]
    worst = next((s for s in facts.sentiment if s.worst_quote), None)
    if worst and worst.worst_quote:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(f"“{_escape(worst.worst_quote[:400])}”", style["quote"]))
        source = worst.worst_url or "no source attached"
        story.append(
            Paragraph(
                f"{_label(worst.engine)} · {_escape(source)}",
                style["small"],
            )
        )
    return story


def _actions_story(
    facts: FactSheet, style: dict[str, ParagraphStyle], primary: colors.Color
) -> list[Flowable]:
    """The recommendations, each with the evidence behind it."""
    if not facts.actions:
        return []
    story: list[Flowable] = [Paragraph("Recommended actions", style["h2"])]
    for index, action in enumerate(facts.actions, start=1):
        block: list[Flowable] = [
            Paragraph(f"{index}. {_escape(action.title)}", style["cellbold"]),
            Paragraph(_escape(action.prescription), style["cell"]),
        ]
        if action.urls:
            block.append(Spacer(1, 1.5 * mm))
            for url in action.urls:
                block.append(Paragraph(_escape(url), style["small"]))
        block.append(Spacer(1, 3.5 * mm))
        story.append(KeepTogether(block))
    return story


def _pages_story(
    facts: FactSheet, style: dict[str, ParagraphStyle], primary: colors.Color
) -> list[Flowable]:
    """The exact URLs the engines cited, ours and theirs (cycle ui-0006)."""
    story: list[Flowable] = []
    for title, pages in (
        ("Your pages the engines cite", facts.client_pages),
        ("Pages cited instead of yours", facts.winning_pages),
    ):
        if not pages:
            continue
        header = ["URL", "Citations", "Platforms"]
        rows: list[list[Any]] = [[Paragraph(h, style["cellbold"]) for h in header]]
        for page in pages:
            engines = ", ".join(_label(e) for e in page.engines)
            rows.append(
                [
                    Paragraph(_escape(page.url), style["cell"]),
                    Paragraph(str(page.citations), style["cell"]),
                    Paragraph(_escape(engines), style["cell"]),
                ]
            )
        story.append(Paragraph(title, style["h2"]))
        story.append(_table(rows, [104 * mm, 20 * mm, 44 * mm], primary))
        story.append(Spacer(1, 3 * mm))
    return story


def _method_story(
    facts: FactSheet, narrative: Narrative, style: dict[str, ParagraphStyle]
) -> list[Flowable]:
    """What was measured and how, including what this tracker cannot do."""
    lines = [
        f"Window: {window_label(facts.window)}, computed from "
        f"{facts.window.computed_from.replace('_', ' ')}.",
        f"{facts.window.prompts} prompt(s) were asked on "
        f"{len(facts.engines)} platform(s), producing {facts.window.samples} sampled answer(s).",
        "Each prompt is asked several times per crawl, because answer engines are "
        "non-deterministic; a rate is the share of those samples, and every rate carries a "
        "95% Wilson interval.",
        f"Market: {facts.locale_label}.",
    ]
    if facts.engines_without_locale:
        lines.append(
            f"{', '.join(facts.engines_without_locale)} has no location field in its API, so "
            "its answers follow the billing account's country whatever the market says."
        )
    if not facts.sentiment_configured:
        lines.append("Sentiment was not scored for this window, so no tone or theme is reported.")
    if facts.window.low_confidence:
        lines.append(
            "This window holds fewer crawls than the project's consolidation window, so the "
            "readings are early signals rather than settled positions."
        )
    if narrative.source is NarrativeSource.MODEL:
        lines.append(
            f"The summary was drafted by {narrative.model} from the measured figures above and "
            "checked against them; it states no number this report does not also show."
        )
    elif narrative.source is NarrativeSource.MIXED:
        lines.append(
            f"Part of the summary was drafted by {narrative.model}; sections that did not match "
            f"the measured figures ({', '.join(narrative.rejected_sections)}) were replaced with "
            "the standard wording."
        )
    else:
        lines.append("The summary is generated from the measured figures, without a model.")
    if facts.spend_usd is not None:
        lines.append(f"Vendor cost of the crawls in this window: ${facts.spend_usd:,.2f}.")
    block: list[Flowable] = [Paragraph("How this was measured", style["h2"])]
    block += [Paragraph(_escape(line), style["small"]) for line in lines]
    return [KeepTogether(block)]


def render(
    facts: FactSheet,
    narrative: Narrative,
    brand: Brand,
    *,
    title: str,
    logo: Path | None = None,
) -> tuple[bytes, int]:
    """Draw the report and return `(pdf_bytes, page_count)`."""
    buffer = io.BytesIO()
    primary = colors.HexColor(brand.primary_colour)
    style = _styles(primary)
    footer = (
        " · ".join(
            part for part in (brand.agency_name, facts.client_brand, brand.footer_note) if part
        )
        or facts.client_brand
    )
    doc = _Doc(buffer, brand=brand, footer=footer)
    # The cover is page one and carries no running furniture; everything after
    # it uses the body template, which draws the footer rule and page numbers.
    # Without the explicit switch ReportLab keeps the cover template forever.
    story: list[Flowable] = [Spacer(1, 1), NextPageTemplate("body"), PageBreak()]
    story += _summary_story(facts, narrative, style, primary)
    for section in (
        _platform_story(facts, style, primary),
        _changes_story(facts, style, primary),
        _sentiment_story(facts, style, primary),
        _actions_story(facts, style, primary),
        _pages_story(facts, style, primary),
        _method_story(facts, narrative, style),
    ):
        if section:
            story.append(Spacer(1, 5 * mm))
            story += section
    first = True

    def on_first(canvas: Canvas, _doc: BaseDocTemplate) -> None:
        """Paint the cover once, when the first page is laid out."""
        nonlocal first
        if first:
            _cover(canvas, facts, brand, title, logo)
            first = False

    doc.pageTemplates[0].onPage = on_first
    doc.build(story)
    pages = doc.page
    return buffer.getvalue(), pages


def cover_title(facts: FactSheet, requested: str | None, now: datetime | None = None) -> str:
    """The document's title: what the operator asked for, or a sensible default."""
    if requested:
        return requested[:120]
    stamp = (now or facts.generated_at).strftime("%B %Y")
    return f"AI visibility report — {stamp}"
