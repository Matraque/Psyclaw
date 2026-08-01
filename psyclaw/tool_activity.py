"""Privacy-safe ADK tool activity logging."""

import logging
import re
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin


logger = logging.getLogger(__name__)

_SAFE_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _tool_name(tool: Any) -> str:
    """Return a bounded developer-defined tool name, or a safe fallback."""
    try:
        name = getattr(tool, "name", None)
    except Exception:
        return "unknown"
    return name if isinstance(name, str) and _SAFE_TOOL_NAME.fullmatch(name) else "unknown"


class ToolActivityPlugin(BasePlugin):
    """Log only safe tool lifecycle markers; never log tool data."""

    def __init__(self) -> None:
        super().__init__(name="tool_activity")

    async def before_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
    ) -> None:
        logger.info("Tool started: %s", _tool_name(tool))
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict[str, Any],
    ) -> None:
        logger.info("Tool succeeded: %s", _tool_name(tool))
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> None:
        logger.error("Tool failed: %s", _tool_name(tool))
        return None
