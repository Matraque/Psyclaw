"""Start Psyclaw's local ADK API server with private SQLite sessions."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from ipaddress import IPv4Address
from ipaddress import IPv4Network
from ipaddress import IPv6Address
from ipaddress import ip_address
from pathlib import Path

from google.adk.cli import main as adk_main

from psyclaw.session_service import get_session_service_uri


AGENT_DIRECTORY = Path(__file__).resolve().parent
LOOPBACK_IPV4_NETWORK = IPv4Network("127.0.0.0/8")
LOOPBACK_IPV6_ADDRESS = IPv6Address("::1")


def validate_loopback_host(host: str) -> str:
    """Return an explicit loopback host or reject an exposed API server."""
    if host == "localhost":
        return host

    try:
        address = ip_address(host)
    except ValueError as error:
        raise ValueError("--host must be localhost, 127.0.0.0/8, or ::1.") from error

    if isinstance(address, IPv4Address) and address in LOOPBACK_IPV4_NETWORK:
        return host
    if address == LOOPBACK_IPV6_ADDRESS:
        return host
    raise ValueError("--host must be localhost, 127.0.0.0/8, or ::1.")


def build_server_arguments(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    allow_origins: Sequence[str] = (),
) -> list[str]:
    """Return API-server arguments with Psyclaw's durable session storage."""
    host = validate_loopback_host(host)
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
    arguments.append(str(AGENT_DIRECTORY))
    return arguments


def main(argv: Sequence[str] | None = None) -> None:
    """Invoke ADK's API-only server without its development interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--allow-origin", action="append", default=[])
    options = parser.parse_args(argv)
    try:
        arguments = build_server_arguments(
            host=options.host,
            port=options.port,
            allow_origins=options.allow_origin,
        )
    except ValueError as error:
        parser.error(str(error))

    adk_main(
        args=arguments,
        prog_name="psyclaw-server",
    )
