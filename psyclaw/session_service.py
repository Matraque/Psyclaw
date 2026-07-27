"""Private, durable ADK session storage configuration.

Google ADK owns the session schema and event persistence.  Psyclaw only
chooses the private on-disk location for that official service.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.adk.sessions.sqlite_session_service import SqliteSessionService


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_PATIENT_DIRECTORY = PROJECT_DIRECTORY / ".psyclaw-data" / "patient"
ADK_DIRECTORY_NAME = ".adk"
SESSION_DATABASE_NAME = "session.db"


def get_patient_directory() -> Path:
    """Return the configured private patient directory without creating it."""
    configured_directory = os.getenv("PSYCLAW_PATIENT_DIR")
    if configured_directory:
        return Path(configured_directory).expanduser()
    return DEFAULT_PATIENT_DIRECTORY


def _restrict_directory_permissions(directory: Path) -> None:
    """Restrict local directory access where POSIX permissions are available."""
    if os.name != "nt":
        directory.chmod(0o700)


def get_session_database_path(
    patient_directory: Path | None = None,
) -> Path:
    """Create and return the ADK SQLite database path in private storage.

    The hidden ``.adk`` directory is deliberately outside the Markdown-only
    patient tool allowlist.  A symlink at that boundary is rejected so the
    database cannot be redirected outside the configured private workspace.
    """
    root = (patient_directory or get_patient_directory()).expanduser()
    root_was_created = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    if root_was_created:
        _restrict_directory_permissions(root)

    adk_directory = root / ADK_DIRECTORY_NAME
    if adk_directory.is_symlink():
        raise RuntimeError("The private ADK storage directory cannot be a symbolic link.")
    adk_directory.mkdir(exist_ok=True)
    _restrict_directory_permissions(adk_directory)
    database_path = adk_directory / SESSION_DATABASE_NAME
    if database_path.is_symlink():
        raise RuntimeError("The private ADK session database cannot be a symbolic link.")
    return database_path


def create_session_service(
    patient_directory: Path | None = None,
) -> SqliteSessionService:
    """Create ADK's durable SQLite service without an in-memory fallback."""
    database_path = get_session_database_path(patient_directory)
    return SqliteSessionService(db_path=str(database_path))


def get_session_service_uri(patient_directory: Path | None = None) -> str:
    """Build the SQLite URI expected by ADK Web for the private database."""
    database_path = get_session_database_path(patient_directory).resolve()
    return f"sqlite:///{database_path.as_posix()}"
