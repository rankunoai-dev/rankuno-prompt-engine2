"""Export the prompt-tracker SQLite store as one JSON file for `docs/prompt-atlas.html`.

The control plane serves the same document live at
`/reports/prompt-atlas-data.json`; this script is for the static page opened
from disk or hosted elsewhere. The logic lives in
`src.modules.prompt_tracking.atlas_export`.

Usage (from the repository root, with the venv python):
    python scripts/export_dashboard.py
    python scripts/export_dashboard.py --db data/prompt_tracker.sqlite
        --out reports/prompt-atlas-data.json --competitor sap.com --domain gep.com
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.modules.prompt_tracking.atlas_export import export_atlas  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "prompt_tracker.sqlite"
DEFAULT_OUT = REPO_ROOT / "reports" / "prompt-atlas-data.json"


def build_parser() -> argparse.ArgumentParser:
    """CLI schema."""
    parser = argparse.ArgumentParser(
        description="Export the tracker SQLite store for Prompt Atlas."
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help="SQLite path (TRACKER_DB_PATH)."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output JSON path.")
    parser.add_argument(
        "--domain", action="append", default=[], help="Client domain (repeatable); shown as client."
    )
    parser.add_argument(
        "--competitor", action="append", default=[], help="Competitor domain (repeatable)."
    )
    parser.add_argument("--lob", default=None, help="Only this line of business.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the export and report where the file landed."""
    args = build_parser().parse_args(argv)
    try:
        document = export_atlas(
            args.db, domains=args.domain, competitors=args.competitor, lob=args.lob
        )
    except FileNotFoundError:
        print(f"No database at {args.db}. Run the tracker first.", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(
        f"Wrote {args.out}: {len(document['prompts'])} prompts, "
        f"{len(document['snapshots'])} snapshots, {len(document['runs'])} runs. "
        "Load it under Data in docs/prompt-atlas.html."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
