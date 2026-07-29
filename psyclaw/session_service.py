"""Private, durable ADK session storage configuration.

Google ADK owns the session schema and event persistence.  Psyclaw only
chooses the private on-disk location for that official service.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.adk.sessions.sqlite_session_service import SqliteSessionService


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_USER_DIRECTORY = PROJECT_DIRECTORY / ".psyclaw-data" / "user"
ADK_DIRECTORY_NAME = ".adk"
SESSION_DATABASE_NAME = "session.db"


def get_user_directory() -> Path:
    """Return the configured private user directory without creating it."""
    configured_directory = os.getenv("PSYCLAW_USER_DIR")
    if configured_directory:
        return Path(configured_directory).expanduser()
    return DEFAULT_USER_DIRECTORY


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
    root = (patient_directory or get_user_directory()).expanduser()
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
    """Build the SQLite URI used by the local ADK API server."""
    database_path = get_session_database_path(patient_directory).resolve()
    return f"sqlite:///{database_path.as_posix()}"
