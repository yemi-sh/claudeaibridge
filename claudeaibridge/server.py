"""
Wires the project, file, and shell tools onto a single FastMCP server.

No auth and no tunnel here yet — this module is deliberately testable on its
own (plain HTTP or stdio, localhost only) before the OAuth and ngrok layers
are added on top.
"""

from fastmcp import FastMCP

from . import tools_files, tools_projects, tools_shell

mcp = FastMCP(
    name="claudeaibridge",
    instructions=(
        "Gives you file and shell access to project folders on the user's own "
        "machine. Call list_projects first to see what's available, then "
        "select_project to choose one — every other tool acts within that "
        "project's folder only, for the rest of this session."
    ),
)

tools_projects.register(mcp)
tools_files.register(mcp)
tools_shell.register(mcp)


def run_http(host: str = "127.0.0.1", port: int = 8420) -> None:
    import uvicorn

    app = mcp.http_app()
    uvicorn.run(app, host=host, port=port)


def run_stdio() -> None:
    mcp.run(transport="stdio", show_banner=False)
