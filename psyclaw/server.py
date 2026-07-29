"""Start Psyclaw's local ADK API server with private SQLite sessions."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from google.adk.cli import main as adk_main

from psyclaw.session_service import get_session_service_uri


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]


def build_server_arguments(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    allow_origins: Sequence[str] = (),
) -> list[str]:
    """Return API-server arguments with Psyclaw's durable session storage."""
    arguments = [
        "api_server",
        "--host",
        host,
        "--port",
        str(port),
        "--no-reload",
        "--session_service_uri",
        get_session_service_uri(),
    ]
    for origin in allow_origins:
        arguments.extend(["--allow_origins", origin])
    arguments.append(str(PROJECT_DIRECTORY))
    return arguments


def main(argv: Sequence[str] | None = None) -> None:
    """Invoke ADK's API-only server without its development interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--allow-origin", action="append", default=[])
    options = parser.parse_args(argv)

    adk_main(
        args=build_server_arguments(
            host=options.host,
            port=options.port,
            allow_origins=options.allow_origin,
        ),
        prog_name="psyclaw-server",
    )
