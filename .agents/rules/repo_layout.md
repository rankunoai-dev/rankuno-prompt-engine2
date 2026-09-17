# Repository Layout

```
src/
├── core/            # Domain-agnostic. Governed pipeline, schemas, config,
│                    # logging, guardrails, rate limiting, retry, registry,
│                    # url_safety (SSRF guard), robots (RFC 9309).
├── integrations/    # External API connectors. All subclass BaseAPIClient.
└── modules/         # Domain engines (e.g. seo/page_classifier/, ppc/, research/)
tests/               # Mirrors src/ package-for-package. External calls mocked.
docs/                # Specifications and standards.
docs/adr/            # Architecture Decision Records.
docs/build-log/      # Per-cycle build log entries.
.agents/rules/       # Workspace-scoped agent rules (this directory).
skills/              # Procedural knowledge for subagents.
scripts/             # verify.ps1, bootstrap.ps1, drift_check.py
```

---

## Dependency Direction (repeated for visibility)

```
modules → integrations → core
```

No arrow ever points outward. Violations are build failures.

---

## Known Gaps Register

Maintain a `docs/KNOWN_GAPS.md` file listing things that are **not yet implemented**
but might look like they are. Format:

```markdown
- `path/to/file.py` — description of what is missing or not wired up.
```

When a gap is closed, move it to a "Closed" section with the build-log cycle number.
Do not delete closed entries — they are part of the audit trail.
