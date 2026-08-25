"""
Active-project selection, scoped per MCP session.

fastmcp's Context.set_state/get_state are session-scoped by default (the key
is prefixed with the session ID internally), so two concurrent claude.ai
sessions naturally get independent "active project" selections without any
manual bookkeeping here.
"""

from pathlib import Path

from fastmcp import Context
from fastmcp.exceptions import ToolError

from . import registry

_STATE_KEY = "active_project"


async def set_active_project(ctx: Context, path: str) -> str:
    """Validate `path` against the registry and make it this session's active project."""
    if not registry.is_registered(path):
        available = ", ".join(sorted(registry.list_projects())) or "(none registered)"
        raise ToolError(
            f"'{path}' is not a registered project. Available: {available}"
        )
    resolved = str(Path(path).expanduser().resolve())
    await ctx.set_state(_STATE_KEY, resolved)
    return resolved


async def get_active_project(ctx: Context) -> str:
    """Return the resolved path of this session's active project.

    Raises ToolError if none has been selected yet, or if the project was
    removed from the registry after being selected (re-checked on every call
    so a mid-session removal can't leave a stale root usable).
    """
    path = await ctx.get_state(_STATE_KEY)
    if not path:
        raise ToolError(
            "No project selected for this session. Call list_projects to see "
            "what's available, then select_project to choose one before using "
            "file or shell tools."
        )
    if not registry.is_registered(path):
        raise ToolError(
            f"'{path}' was removed from the registry since it was selected. "
            f"Call list_projects and select_project again."
        )
    return path
