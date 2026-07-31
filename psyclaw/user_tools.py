from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_USER_DIRECTORY = PROJECT_DIRECTORY / ".psyclaw-data" / "user"
USER_DIRECTORY = Path(
    os.getenv("PSYCLAW_USER_DIR", str(DEFAULT_USER_DIRECTORY))
).expanduser()
MEMORY_DIRECTORY_NAME = "memory"
DEFAULT_USER_FILES_DIRECTORY = Path(__file__).resolve().parent / "default_user"
ALLOWED_SUFFIXES = {".md"}
MAX_FILE_SIZE_BYTES = 128 * 1024
MAX_CONTEXT_CHARS_PER_FILE = 8_000
CORE_RECORD_PATHS = (
    "references.md",
    "user_profile.md",
    "memory.md",
    "care_plan.md",
)
WORKSPACE_MARKER = ".psyclaw-workspace.json"
WORKSPACE_SCHEMA_VERSION = 1
SESSION_NOTE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:_[a-z0-9][a-z0-9-]*)?\.md$")


class UserFileError(ValueError):
    """Raised when a user-file operation violates the local policy."""


def get_date() -> dict[str, str]:
    """Return today's calendar date in UTC."""
    now = datetime.now(timezone.utc)
    return {
        "status": "ok",
        "date": now.date().isoformat(),
    }


def _private_user_root() -> Path:
    """Return the canonical private user directory, creating it when necessary."""
    USER_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return USER_DIRECTORY.resolve()


def _memory_root() -> Path:
    """Return the canonical Markdown workspace, creating it when necessary."""
    user_directory = _private_user_root()
    memory_directory = user_directory / MEMORY_DIRECTORY_NAME
    if memory_directory.is_symlink():
        raise UserFileError("The memory workspace cannot be a symbolic link.")
    memory_directory.mkdir(exist_ok=True)
    return memory_directory.resolve()


def _normalise_relative_path(path: str, *, allow_root: bool = False) -> PurePosixPath:
    if not isinstance(path, str):
        raise UserFileError("The path must be a string.")

    if "\\" in path:
        raise UserFileError("Use relative paths with forward slashes ('/').")

    cleaned_path = path.strip()
    if not cleaned_path:
        if allow_root:
            return PurePosixPath(".")
        raise UserFileError("The path cannot be empty.")

    candidate = PurePosixPath(cleaned_path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise UserFileError("The path must remain relative to the user directory.")
    if any(part.startswith(".") for part in candidate.parts):
        raise UserFileError("Hidden files and directories are not allowed.")
    return candidate


def _resolve_user_file(path: str, *, must_exist: bool = False) -> tuple[Path, str]:
    """Resolve an allowed Markdown file and prove that it stays under the root."""
    relative_path = _normalise_relative_path(path)
    if relative_path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise UserFileError("Only Markdown (.md) files are allowed.")

    root = _memory_root()
    requested_path = root / relative_path
    current_path = root
    for part in relative_path.parts:
        current_path = current_path / part
        if current_path.is_symlink():
            raise UserFileError("Symbolic links are not allowed.")

    resolved_path = requested_path.resolve(strict=False)
    try:
        resolved_path.relative_to(root)
    except ValueError as exc:
        raise UserFileError("The path escapes the user directory.") from exc

    if must_exist and not resolved_path.is_file():
        raise UserFileError(f"File not found: {relative_path.as_posix()}")
    return resolved_path, relative_path.as_posix()


def _resolve_user_directory(path: str = "") -> Path:
    """Resolve a user subdirectory without permitting symbolic links."""
    relative_path = _normalise_relative_path(path, allow_root=True)
    root = _memory_root()
    requested_path = root / relative_path
    current_path = root
    for part in relative_path.parts:
        if part == ".":
            continue
        current_path = current_path / part
        if current_path.is_symlink():
            raise UserFileError("Symbolic links are not allowed.")

    resolved_path = requested_path.resolve(strict=False)
    try:
        resolved_path.relative_to(root)
    except ValueError as exc:
        raise UserFileError("The path escapes the user directory.") from exc
    if not resolved_path.is_dir():
        raise UserFileError("The requested path is not a user directory.")
    return resolved_path


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_SIZE_BYTES:
        raise UserFileError(
            f"The file exceeds the {MAX_FILE_SIZE_BYTES // 1024} KiB limit."
        )
    return path.read_text(encoding="utf-8")


def _initialise_user_workspace() -> bool:
    """Seed a private user workspace once without overwriting existing data."""
    root = _memory_root()

    marker_path = _private_user_root() / WORKSPACE_MARKER
    if marker_path.is_file():
        return False
    if not DEFAULT_USER_FILES_DIRECTORY.is_dir():
        raise UserFileError("Packaged default user files are missing.")

    for source_path in sorted(DEFAULT_USER_FILES_DIRECTORY.rglob("*")):
        relative_path = source_path.relative_to(DEFAULT_USER_FILES_DIRECTORY)
        target_path = root / relative_path
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        if source_path.is_symlink() or source_path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target_path.open("x", encoding="utf-8") as target_file:
                target_file.write(_read_text(source_path))
        except FileExistsError:
            pass

    marker_path.write_text(
        json.dumps({"schema_version": WORKSPACE_SCHEMA_VERSION}, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def _result_error(error: UserFileError) -> dict[str, Any]:
    return {"status": "error", "error": str(error)}


def list_files(path: str = "") -> dict[str, Any]:
    """List directories and Markdown notes in the user directory.

    Args:
        path: Optional relative subdirectory, for example ``session_notes``.
    """
    try:
        root = _memory_root()
        directory = _resolve_user_directory(path)

        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.name.startswith(".") or child.is_symlink():
                continue
            if child.is_dir():
                entries.append({"path": child.relative_to(root).as_posix(), "type": "directory"})
            elif child.is_file() and child.suffix.lower() in ALLOWED_SUFFIXES:
                entries.append(
                    {
                        "path": child.relative_to(root).as_posix(),
                        "type": "file",
                        "size_bytes": child.stat().st_size,
                    }
                )
        return {"status": "ok", "entries": entries}
    except UserFileError as error:
        return _result_error(error)


def read_file(path: str) -> dict[str, Any]:
    """Read a Markdown note from the user directory.

    Args:
        path: Relative path within the user directory, for example ``memory.md``.
    """
    try:
        file_path, relative_path = _resolve_user_file(path, must_exist=True)
        return {"status": "ok", "path": relative_path, "content": _read_text(file_path)}
    except UserFileError as error:
        return _result_error(error)


def get_context() -> dict[str, Any]:
    """Initialise the private workspace once, then load user context.

    Public default files are copied into the private user directory only on
    first session and are never overwritten during later starts. A user is new
    until a valid dated session note exists. Only the latest session note is
    loaded; use ``list_files`` and ``read_file`` for older records.
    TODO : implement RAG recall on older notes when relevant based on user query and context length.
    Relevant snippets from older discussions will be included in the context.
    """
    try:
        workspace_bootstrapped = _initialise_user_workspace()
        root = _memory_root()
        session_notes_directory = root / "session_notes"
        notes: list[Path] = []
        if session_notes_directory.is_dir() and not session_notes_directory.is_symlink():
            notes = sorted(
                (
                    note
                    for note in session_notes_directory.iterdir()
                    if note.is_file()
                    and not note.is_symlink()
                    and SESSION_NOTE_PATTERN.fullmatch(note.name)
                ),
                key=lambda note: note.name,
            )

        records: dict[str, str] = {}
        missing: list[str] = []
        empty: list[str] = []
        truncated: list[str] = []
        for relative_path in CORE_RECORD_PATHS:
            file_path, safe_path = _resolve_user_file(relative_path)
            if not file_path.is_file():
                missing.append(safe_path)
                continue
            content = _read_text(file_path)
            if not content.strip():
                empty.append(safe_path)
            if len(content) > MAX_CONTEXT_CHARS_PER_FILE:
                content = content[:MAX_CONTEXT_CHARS_PER_FILE]
                truncated.append(safe_path)
            records[safe_path] = content

        latest_session_note: str | None = None
        if notes:
            latest_note = notes[-1]
            latest_session_note = latest_note.relative_to(root).as_posix()
            content = _read_text(latest_note)
            if len(content) > MAX_CONTEXT_CHARS_PER_FILE:
                content = content[:MAX_CONTEXT_CHARS_PER_FILE]
                truncated.append(latest_session_note)
            records[latest_session_note] = content
        else:
            first_session_guide, guide_path = _resolve_user_file("session_notes/README.md")
            if first_session_guide.is_file():
                records[guide_path] = _read_text(first_session_guide)
            else:
                missing.append(guide_path)

        return {
            "status": "ok",
            "workspace_bootstrapped": workspace_bootstrapped,
            "new_user": not notes,
            "latest_session_note": latest_session_note,
            "records": records,
            "missing": missing,
            "empty": empty,
            "truncated": truncated,
        }
    except UserFileError as error:
        return _result_error(error)
