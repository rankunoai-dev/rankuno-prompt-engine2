"""Executive PDF reports for a client (ADR 0024).

A crawl already produces everything a monthly client report states; this
package turns it into a document an agency can send under its own brand. The
pipeline is deliberately linear and each stage is pure except the last two:

    InsightsView + PositionsView ─▶ facts.build_fact_sheet()   (pure)
                                 ─▶ narrative.compose()        (one model call)
                                 ─▶ pdf.render()               (ReportLab)
                                 ─▶ store.ReportStore          (row + file)

The model never sees engine prose or raw answer text, only the computed fact
sheet, and every number it writes back is checked against that sheet before a
page is drawn. A report is never allowed to state a number the engine did not
measure.

Import direction: this package may use `core`, `integrations` and
`control_plane`'s read models; `control_plane` imports it, never the reverse.
"""
