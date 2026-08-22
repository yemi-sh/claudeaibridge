"""
Active-project selection, scoped per MCP session.

fastmcp's Context.set_state/get_state are session-scoped by default (the key
is prefixed with the session ID internally), so two concurrent claude.ai
sessions naturally get independent "active project" selections without any
manual bookkeeping here.
"""

from fastmcp import Context
from fastmcp.exceptions import ToolError

from . import registry

_STATE_KEY = "active_project"


async def set_active_project(ctx: Context, name: str) -> str:
    """Validate `name` against the registry and make it this session's active project."""
    path = registry.get_project_path(name)
    if path is None:
        available = ", ".join(sorted(registry.list_projects())) or "(none registered)"
        raise ToolError(
            f"No registered project named '{name}'. Available: {available}"
        )
    await ctx.set_state(_STATE_KEY, {"name": name, "path": path})
    return path


async def get_active_project(ctx: Context) -> tuple[str, str]:
    """Return (name, path) for this session's active project.

    Raises ToolError if none has been selected yet, or if the project was
    removed from the registry after being selected (re-checked on every call
    so a mid-session removal can't leave a stale root usable).
    """
    state = await ctx.get_state(_STATE_KEY)
    if not state:
        raise ToolError(
            "No project selected for this session. Call list_projects to see "
            "what's available, then select_project to choose one before using "
            "file or shell tools."
        )
    name, path = state["name"], state["path"]
    current_path = registry.get_project_path(name)
    if current_path is None:
        raise ToolError(
            f"Project '{name}' was removed from the registry since it was "
            f"selected. Call list_projects and select_project again."
        )
    return name, current_path
