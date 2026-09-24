# ADR 0024 — Executive PDF reports and outbound alerting

**Date**: 2026-09-24
**Status**: Accepted (cycle 0021; the operator chose ReportLab, SMTP, a fact-checked model narrative and `citation_drop` as the only default rule on 2026-09-23)
**Builds on**: ADR 0019 (everyone reads, only the holder writes), ADR 0020 (confidence bands), ADR 0021 (the Anthropic client and sentiment), ADR 0023 (per-project locale)

## Context

Two requests, one cycle: a white-label PDF an agency can send its client, and
alerts when a project's standing actually moves.

Everything the report states already exists as typed data — `InsightsView`,
`PositionsView`, `CostReport` — so this cycle adds no analytics. What it adds
is the first **outbound** behaviour in the tracker: until now nothing left the
process except vendor requests the operator paid for. That changes the risk
profile more than the feature list suggests.

Three constraints shaped every decision below.

1. **A client-facing number that is wrong is worse than no report.** A PDF is
   forwarded, quoted in a meeting and believed long after the dashboard has
   moved on.
2. **A Slack webhook is a credential and a recipient list is personal data.**
   `GET /api/projects/{id}` is readable by anyone who can reach the deployment
   (ADR 0019), and the project is one JSON payload.
3. **The runtime is `python:3.11-slim` and the operator develops on Windows.**
   Anything needing system libraries costs both an image change and a painful
   local install.

## Decision

### A. ReportLab, not an HTML engine

| Option | Verdict |
|---|---|
| **ReportLab** | Chosen. Pure-Python wheel, no system libraries either side, its own chart package, and the layout is code so a test asserts sections rather than pixels. |
| WeasyPrint | Prettier and reuses the UI's CSS, but needs pango/cairo/gdk-pixbuf in the image and GTK on Windows, and its charts would be hand-written SVG anyway. |
| Headless Chromium | Best fidelity, ~450 MB in the image, a browser running on the server, seconds per report. |

### B. The narrative is delegated, the numbers are not

`facts.py` computes a `FactSheet` — every number the report is allowed to
state, pooled from counts rather than averaged from rates. The model
(`claude-sonnet-5`) sees **only** that sheet: no answer text, no citation
bodies, no client copy. It returns a fixed JSON schema at temperature 0.

Then every numeric token it wrote is checked against the sheet, and any
section containing a number the engine did not measure — or any URL — is
dropped and replaced by the deterministic template. The report says which
sections that happened to, and the methodology page names the model.

With no key, a zero budget, a refusal or a transport failure, the whole
narrative falls back to templates. **A report always renders.**

### C. A drop is only a drop when the bands separate

`citation_drop` fires when the current window's 95% Wilson interval sits
entirely below the previous window's (ADR 0020), with at least ten answered
samples on each side. Answer engines are non-deterministic; a tracker that
alerts on every four-point wobble trains its reader to ignore the channel.

The cost is stated plainly: small windows will miss real drops. A missed alert
is recoverable from the dashboard; a channel nobody reads is not.

Six other rules exist — lost starred prompt, competitor surge, sourced
negative claim, silent engine, spend, crawl failure — and **only
`citation_drop` is on for a new project.** Every other rule is a message
somebody has to actively want.

### D. Three gates before anything is sent

1. The destination is enabled and the rule is on.
2. The cooldown has expired: the same `(rule, engine, subject)` stays quiet for
   `ALERT_COOLDOWN_HOURS` (72) after a *delivered* alert.
3. The daily ceiling has room: `ALERTS_MAX_PER_PROJECT_PER_DAY` (5), and zero
   disables sending entirely.

Suppressed events are still written to the history with the reason, so "why
didn't I hear about this?" has an answer. The row is written *before* delivery
is attempted, so a crash can duplicate a message but never lose the record.

### E. Credentials and personal data leave the project payload

`alert_destinations` is its own table, like `project_credentials`. The API
returns `AlertDestinationView`: `slack_configured`, a masked
`hooks.slack.com/services/T04A…` hint, and `p***@client.com` recipients. The
webhook is never returned, never logged, and validated to
`https://hooks.slack.com/services/...` — a host pin, because the alternative is
an application that POSTs to an arbitrary URL on a schedule.

Branding is *not* a credential, so `Brand` lives on the project: client and
agency names, colour, logo id, footer note, and whether vendor cost is shown
(off by default — a client report should not show what the agency pays).

### F. Its own worker, not the crawl queue

Reports run on a dedicated thread that starts on the first submit. A PDF must
not wait behind a twenty-minute crawl, and it spends no engine quota worth
serialising. Alerting, by contrast, runs *inside* the crawl as a phase after
`_record_crawl`, mirroring the judging phase — and, like judging, it can never
fail the run.

## Edge cases and breaking points, with the handling

**Report**

- *No consolidation yet.* The loader raises; the row is `failed` with the
  reason and the worker serves the next report.
- *A logo that is not an image, or is a decompression bomb.* Size is checked
  **before** `Image.open`, then Pillow verifies the format and the pixel
  dimensions are bounded at 4000px a side. Only PNG and JPEG are stored.
- *A crafted `report_id` or `logo_id`.* Every path is built from an id this
  application minted; `logo_id` is refused by the model's own validator, so it
  cannot be set through `PUT /api/projects/{id}`.
- *A deleted logo file.* The cover loses its image; the report still renders.
- *Engine text in a quote containing ReportLab markup.* Escaped before it
  reaches a `Paragraph`.
- *Raw platform ids in card titles.* The insight engine writes
  `CHATGPT_SEARCH`, which is fine beside a platform filter and reads like a
  leaked internal id in a client PDF; every string the report prints is
  humanised first.
- *Retention.* Files older than `REPORT_RETENTION_DAYS` (400) are deleted and
  the row marked `purged_at`; the row stays, because "what did you send me in
  March" deserves an answer.
- *Email.* Optional everywhere. An invalid address is dropped with a log line
  rather than failing the send; a delivery failure is recorded on the row and
  the PDF is still downloadable.

**Alerting**

- *First window.* Every rule is a delta, so a project with one consolidation
  raises nothing at all.
- *A dead webhook.* The Slack call raises, the record keeps the error, and
  because the cooldown keys on *delivered* alerts the next crawl retries.
- *Partial delivery.* Slack fails, email succeeds: the alert counts as
  delivered and the error is kept.
- *Alerting itself broken.* The whole phase is inside one guard; the crawl
  adds a warning and still succeeds.
- *A project deleted.* Its destination, its history and its reports go with it.

## Alternatives rejected

- **Alerts as a poller.** A separate schedule would re-read windows that have
  not changed. A window only closes when a crawl consolidates it, so that is
  where the evaluation belongs.
- **Slack app with OAuth.** Scopes to review, tokens to refresh, and a larger
  blast radius than "can post into one channel".
- **An email provider API (Resend, SendGrid).** Would fit `BaseAPIClient`
  exactly, but needs another account and a verified domain; the operator
  already pays for a mailbox, and a client report should leave from the
  agency's own domain.
- **Letting the model write freely and reviewing before sending.** The review
  never happens at month twelve.

## Consequences

- The tracker now sends things. `ALERTS_MAX_PER_PROJECT_PER_DAY=0` is the one
  setting that stops all of it.
- A report costs about $0.02 with the narrative on, booked to the ledger as
  `source=report` and bounded by `REPORT_MAX_SPEND_USD`.
- `reportlab` and `pillow` join the `ui` extra; the runtime image is unchanged.
- Two new tables in the tracker database (`report_runs`, `alert_destinations`,
  `alert_events`) and PDFs on the volume under `REPORTS_DIR`.
