"""
Manual smoke test exercising the full tool sequence through an in-memory
MCP client (no HTTP/tunnel/auth involved). Registers and cleans up its own
scratch project, so it doesn't depend on anything being set up beforehand.

Run with: .venv/bin/python tests/smoke_test.py
"""

import asyncio
import json
import tempfile

from fastmcp import Client

from claudeaibridge import registry
from claudeaibridge.server import mcp


async def call(client, tool_name, **kwargs):
    result = await client.call_tool(tool_name, kwargs)
    data = result.data if hasattr(result, "data") else result
    print(f"\n>>> {tool_name}({kwargs})")
    print(json.dumps(data, indent=2, default=str))
    return data


async def main():
    with tempfile.TemporaryDirectory(prefix="cab-smoke-") as tmp_dir:
        project_path = registry.add_project(tmp_dir)
        try:
            async with Client(mcp) as client:
                await call(client, "list_projects")
                await call(client, "select_project", path=project_path)
                await call(client, "file_enum", path=".")

                # Widget: file_write and file_edit should both carry a UI
                # resource, still return the same plain dict data they always
                # did (for non-widget hosts and for Claude's own reasoning),
                # and their structured content should embed a rendered diff
                # matching the same `diff` field in that data — no separate
                # tool call needed to see it.
                tools = await client.list_tools()
                for name in ("file_write", "file_edit"):
                    t = next(t for t in tools if t.name == name)
                    assert t.meta and "ui" in t.meta, f"{name} should carry _meta.ui"

                # Note: with structured_content carrying the Prefab widget
                # tree, the plain result data lives in `content` (what
                # Claude actually reads), not `.data`/`structured_content`
                # anymore — parse it out the same way a non-widget host would.
                write_result = await client.call_tool("file_write", {
                    "path": "new_file.py",
                    "content": "def greet():\n    print('hi')\n",
                    "reason": "smoke test create",
                })

                def diff_lines_from_widget(result):
                    # Div(pf-app-root) > Column(title, diff-lines Column)
                    root = result.structured_content["view"]
                    assert root["cssClass"] == "pf-app-root p-2"
                    outer = root["children"][0]
                    assert outer["type"] == "Column"
                    diff_col = outer["children"][1]
                    assert diff_col["type"] == "Column"
                    return [c["content"] for c in diff_col["children"]]

                write_data = json.loads(write_result.content[0].text)
                print(f"\n>>> file_write(...)\n{json.dumps(write_data, indent=2, default=str)}")
                assert write_data["success"] is True
                assert "diff" in write_data
                assert diff_lines_from_widget(write_result) == write_data["diff"].splitlines()

                edit_result = await client.call_tool("file_edit", {
                    "path": "new_file.py",
                    "old_content": "print('hi')",
                    "new_content": "print('hello')",
                    "reason": "smoke test edit",
                })
                edit_data = json.loads(edit_result.content[0].text)
                print(f"\n>>> file_edit(...)\n{json.dumps(edit_data, indent=2, default=str)}")
                assert edit_data["success"] is True
                edit_lines = diff_lines_from_widget(edit_result)
                assert edit_lines == edit_data["diff"].splitlines()
                assert any(c.startswith("-") for c in edit_lines) and any(c.startswith("+") for c in edit_lines)
                print("\n>>> file_write/file_edit widgets: UI meta present, plain dict data "
                      "intact, diff lines rendered with the same content")

                await call(client, "shell_execute", command="cat new_file.py && pwd")

                await call(client, "shell_execute", command="echo blocked > /tmp/should_not_exist_cab_test")

                await call(client, "file_trash", paths=["new_file.py"], reason="smoke test cleanup")

                # Path escape attempt — must be rejected.
                try:
                    await call(client, "file_write", path="../escape.txt", content="x", reason="escape attempt")
                    print("!!! ESCAPE NOT BLOCKED")
                except Exception as e:
                    print(f"\n>>> escape attempt correctly rejected: {e}")
        finally:
            registry.remove_project(project_path)


if __name__ == "__main__":
    asyncio.run(main())
