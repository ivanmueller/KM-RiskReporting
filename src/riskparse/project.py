"""Files the project keeps in ``config/``, found from wherever the command is run."""

from __future__ import annotations

from pathlib import Path

# the repository root (src/riskparse/project.py -> ../..); valid for the editable install
REPO_ROOT = Path(__file__).resolve().parents[2]


def project_file(relative: str | Path) -> Path | None:
    """``relative`` under the current folder, else under the repository; None if neither."""
    for base in (Path.cwd(), REPO_ROOT):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None
