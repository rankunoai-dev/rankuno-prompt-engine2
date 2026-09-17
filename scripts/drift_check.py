"""Architecture and documentation drift audit (SDLC Step 8).

Fails when a domain module, connector or environment variable exists in code
but is mentioned in neither `README.md` nor `docs/ARCHITECTURE.md`. The check is
deliberately shallow — it catches the common failure (a new module nobody
documented) without pretending to verify prose accuracy.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
README_PATH = REPO_ROOT / "README.md"
ARCH_PATH = REPO_ROOT / "docs" / "ARCHITECTURE.md"
GAPS_PATH = REPO_ROOT / "docs" / "KNOWN_GAPS.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _settings_field_names() -> list[str]:
    """Environment variable names declared on `Settings`, by static scan."""
    config = _read(SRC_DIR / "core" / "config.py")
    body = config.split("class Settings", 1)[-1]
    return sorted(
        {m.group(1).upper() for m in re.finditer(r"^\s{4}([a-z][a-z0-9_]+):", body, re.M)}
    )


def check_drift() -> int:
    """Run the audit and return a process exit code."""
    issues: list[str] = []
    for path, label in ((README_PATH, "README.md"), (ARCH_PATH, "docs/ARCHITECTURE.md")):
        if not path.exists():
            issues.append(f"{label} is missing.")
    if not GAPS_PATH.exists():
        issues.append("docs/KNOWN_GAPS.md is missing (required by .agents/rules/repo_layout.md).")

    docs = _read(README_PATH) + _read(ARCH_PATH)

    modules_dir = SRC_DIR / "modules"
    if modules_dir.exists():
        for item in modules_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_") and item.name not in docs:
                issues.append(f"Module 'src/modules/{item.name}' is undocumented.")

    integrations_dir = SRC_DIR / "integrations"
    if integrations_dir.exists():
        for item in integrations_dir.glob("*.py"):
            if not item.name.startswith("_") and item.stem not in docs:
                issues.append(f"Connector 'src/integrations/{item.name}' is undocumented.")

    env_example = _read(REPO_ROOT / ".env.example")
    for name in _settings_field_names():
        if name not in env_example and name not in docs:
            issues.append(f"Setting '{name}' is in neither .env.example nor the docs.")

    print("--- Drift Audit Results ---")
    if issues:
        print("Architecture drift detected:")
        for issue in issues:
            print(f"  - {issue}")
        print("\nUpdate README.md, docs/ARCHITECTURE.md or .env.example in the same change.")
        return 1
    print("No documentation drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(check_drift())
