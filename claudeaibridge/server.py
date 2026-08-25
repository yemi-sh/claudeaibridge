"""
Wires the project, file, and shell tools onto a single FastMCP server.

No auth and no tunnel here yet — this module is deliberately testable on its
own (plain HTTP or stdio, localhost only) before the OAuth and ngrok layers
are added on top.
"""

from fastmcp import FastMCP

from . import tools_debug, tools_files, tools_projects, tools_shell


def create_app(auth_provider=None) -> FastMCP:
    """Build a fresh FastMCP instance with all tools registered. A factory
    rather than a module-level singleton because the auth provider (which
    needs the server's public base URL) is only known once `serve` picks a
    transport/host — stdio and the smoke tests use no auth at all."""
    mcp = FastMCP(
        name="claudeaibridge",
        instructions=(
            "Gives you file and shell access to project folders on the user's own "
            "machine. Call list_projects first to see what's available, then "
            "select_project to choose one — every other tool acts within that "
            "project's folder only, for the rest of this session."
        ),
        auth=auth_provider,
    )
    tools_projects.register(mcp)
    tools_files.register(mcp)
    tools_shell.register(mcp)
    tools_debug.register(mcp)  # TEMPORARY — remove once widget support is confirmed/denied
    return mcp


# Auth-less instance used by stdio mode and by tests/smoke_test.py.
mcp = create_app()


def run_http(host: str = "127.0.0.1", port: int = 8420, auth_provider=None) -> None:
    import uvicorn

    app = create_app(auth_provider=auth_provider).http_app()
    uvicorn.run(app, host=host, port=port)


def run_stdio() -> None:
    mcp.run(transport="stdio", show_banner=False)
