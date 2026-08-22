"""
Manual smoke test exercising the full tool sequence through an in-memory
MCP client (no HTTP/tunnel/auth involved) against the 'testproj' project
registered by the shell commands in the conversation.

Run with: .venv/bin/python tests/smoke_test.py
"""

import asyncio
import json

from fastmcp import Client

from claudeaibridge.server import mcp


async def call(client, tool_name, **kwargs):
    result = await client.call_tool(tool_name, kwargs)
    data = result.data if hasattr(result, "data") else result
    print(f"\n>>> {tool_name}({kwargs})")
    print(json.dumps(data, indent=2, default=str))
    return data


async def main():
    async with Client(mcp) as client:
        await call(client, "list_projects")
        await call(client, "select_project", name="testproj")

        await call(client, "file_enum", path=".")

        await call(client, "file_write", path="new_file.py",
                    content="def greet():\n    print('hi')\n",
                    reason="smoke test create")

        await call(client, "file_edit", path="new_file.py",
                    old_content="print('hi')", new_content="print('hello')",
                    reason="smoke test edit")

        await call(client, "shell_execute", command="cat new_file.py && pwd")

        await call(client, "shell_execute", command="echo blocked > /tmp/should_not_exist_cab_test")

        await call(client, "file_trash", paths=["new_file.py"], reason="smoke test cleanup")

        # Path escape attempt — must be rejected.
        try:
            await call(client, "file_write", path="../escape.txt", content="x", reason="escape attempt")
            print("!!! ESCAPE NOT BLOCKED")
        except Exception as e:
            print(f"\n>>> escape attempt correctly rejected: {e}")


if __name__ == "__main__":
    asyncio.run(main())
