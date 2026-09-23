"""Inbound AI-crawler log analytics (ADR 0022).

Access logs are uploaded per project, classified by crawler user agent,
verified against the vendors' published IP ranges, and kept only as per-day
aggregates: never a raw line, never an address. Joined to the engine's own
record of cited and consulted URLs they give the Fetch → Consulted → Cited
funnel per page.

Import direction: this package may use `core`, `integrations.schemas` and
`prompt_tracking`; `control_plane` imports it, never the reverse.
"""
