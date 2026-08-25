"""
Temporary diagnostic tool — not meant to stay in the codebase. Reports what
the connected MCP client (e.g. claude.ai) actually declared during
initialization, specifically whether it advertised support for the MCP Apps
UI extension (io.modelcontextprotocol/ui) that the widget tools depend on.
Remove this file and its registration in server.py once the question is
answered.
"""

from fastmcp import Context
from fastmcp.apps.config import UI_EXTENSION_ID


def register(mcp):
    @mcp.tool(
        name="debug_client_capabilities",
        annotations={
            "title": "Debug Client Capabilities",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def debug_client_capabilities(ctx: Context) -> dict:
        """Diagnostic only: reports the connected client's declared name,
        version, and capabilities (including MCP extensions), and whether it
        supports the Apps/UI extension widgets rely on."""
        session = ctx.request_context.session
        client_params = getattr(session, "_client_params", None)
        result = {"ui_extension_supported": ctx.client_supports_extension(UI_EXTENSION_ID)}
        if client_params is not None:
            result["client_info"] = (
                client_params.clientInfo.model_dump() if client_params.clientInfo else None
            )
            result["capabilities"] = (
                client_params.capabilities.model_dump() if client_params.capabilities else None
            )
        return result
