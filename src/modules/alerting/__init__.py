"""Outbound alerts when a project's standing actually moves (ADR 0024).

This is the first thing in the tracker that sends something to the outside
world, so the design is conservative by default:

* **Triggers are pure.** `triggers.py` turns stored analysis into candidate
  events and calls nothing. Whether an event is worth sending is a separate
  question from whether it is true.
* **A drop must clear the noise.** The headline rule fires only when the 95%
  intervals of the two windows do not overlap (ADR 0020). Answer engines are
  non-deterministic; a tracker that alerts on every four-point wobble trains
  its reader to ignore it.
* **Sending is bounded.** A destination is configured by the operator, every
  event is deduplicated for a cooldown window, and a per-project daily
  ceiling caps the blast radius of a flapping metric.
* **A crawl never fails because an alert did.** The dispatcher records the
  event first and reports delivery failures as rows, not exceptions.

Import direction: this package may use `core`, `integrations` and the control
plane's read models; `control_plane` imports it, never the reverse.
"""
