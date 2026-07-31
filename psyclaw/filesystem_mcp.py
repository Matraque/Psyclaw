"""Bounded filesystem MCP tools for the private user workspace."""

from __future__ import annotations

import sys
from pathlib import Path

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

from .user_tools import _initialise_user_workspace, _memory_root


FILESYSTEM_SERVER_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.7.10"
NOTE_TAKER_TOOL_ALLOWLIST = (
    "read_text_file",
    "list_directory",
    "create_directory",
    "write_file",
    "edit_file",
)


def create_user_filesystem_toolset() -> McpToolset:
    """Create the official filesystem MCP toolset bounded to the user workspace.

    The npm package version is intentionally pinned: updates to its tool surface
    require an explicit dependency review and test update.
    """
    _initialise_user_workspace()
    memory_root = _memory_root()
    return McpToolset(
        connection_params=_filesystem_connection_params(memory_root),
        tool_filter=list(NOTE_TAKER_TOOL_ALLOWLIST),
    )


def _filesystem_connection_params(memory_root: Path) -> StdioConnectionParams:
    """Build ADK's recommended stdio connection wrapper."""
    return StdioConnectionParams(
        server_params=_filesystem_server_parameters(memory_root),
    )


def _filesystem_server_parameters(memory_root: Path) -> StdioServerParameters:
    """Build platform-correct stdio parameters for the official npm server."""
    canonical_root = memory_root.resolve()
    if sys.platform == "win32":
        return StdioServerParameters(
            command="cmd",
            args=["/c", "npx", "-y", FILESYSTEM_SERVER_PACKAGE, str(canonical_root)],
        )
    return StdioServerParameters(
        command="npx",
        args=["-y", FILESYSTEM_SERVER_PACKAGE, str(canonical_root)],
    )
