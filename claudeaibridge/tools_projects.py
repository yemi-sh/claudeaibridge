"""Tools for discovering and selecting the active project.

Registration itself (adding a new folder to the allowlist) is deliberately
NOT exposed here — that only happens locally via the CLI. These tools can
only ever choose among folders a person already approved on the machine.
"""

from typing import Annotated

from fastmcp import Context
from pydantic import Field

from . import registry
from . import session


def register(mcp):
    @mcp.tool(
        name="list_projects",
        annotations={
            "title": "List Available Projects",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def list_projects() -> dict:
        """
        List the project folders this server is allowed to work in.

        Only folders explicitly registered on the local machine (via
        `claudeaibridge add-project`) appear here — call select_project with
        one of these paths before using any file or shell tool.
        """
        return {"projects": sorted(registry.list_projects())}

    @mcp.tool(
        name="select_project",
        annotations={
            "title": "Select Active Project",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def select_project(
        path: Annotated[
            str,
            Field(description="The project's path, as returned by list_projects."),
        ],
        ctx: Context,
    ) -> dict:
        """
        Make `path` the active project for the rest of this session. All
        subsequent file and shell tool calls in this session are scoped to
        that project's folder until select_project is called again.
        """
        resolved = await session.set_active_project(ctx, path)
        return {"selected": resolved}
