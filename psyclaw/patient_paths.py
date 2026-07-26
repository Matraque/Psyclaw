"""Canonical configuration for the private patient workspace."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_PATIENT_DIRECTORY = PROJECT_DIRECTORY / ".psyclaw-data" / "patient"


def configured_patient_root() -> Path:
    """Return the current absolute lexical root without import-time freezing."""
    raw_root = os.getenv("PSYCLAW_PATIENT_DIR")
    candidate = Path(raw_root).expanduser() if raw_root else DEFAULT_PATIENT_DIRECTORY
    absolute = Path(os.path.abspath(os.fspath(candidate)))
    # macOS exposes these trusted system aliases. Normalize only these known
    # aliases so every patient subsystem names the same root without resolving
    # patient-controlled symbolic links.
    for alias in (Path("/var"), Path("/tmp")):
        try:
            relative = absolute.relative_to(alias)
        except ValueError:
            continue
        if alias.is_symlink():
            return alias.resolve() / relative
    return absolute
