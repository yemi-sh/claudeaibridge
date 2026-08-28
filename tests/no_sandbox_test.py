"""
Verifies --no-sandbox actually does what it claims: shell commands escape
project-folder containment when it's set, and stay contained by default.
Registers and cleans up its own scratch project and target file.

Run with: .venv/bin/python tests/no_sandbox_test.py
"""

import asyncio
import tempfile
from pathlib import Path

from fastmcp import Client

from claudeaibridge import registry
from claudeaibridge.server import create_app


async def try_write_outside_project(no_sandbox: bool, project_path: str, target: Path) -> dict:
    app = create_app(no_sandbox=no_sandbox)
    async with Client(app) as client:
        await client.call_tool("select_project", {"path": project_path})
        result = await client.call_tool("shell_execute", {
            "command": f"echo written > {target}",
        })
        return result.data


async def main():
    with tempfile.TemporaryDirectory(prefix="cab-nosandbox-project-") as project_dir, \
         tempfile.TemporaryDirectory(prefix="cab-nosandbox-outside-") as outside_dir:
        project_path = registry.add_project(project_dir)
        target = Path(outside_dir) / "escaped.txt"
        try:
            # Default: sandboxed, write to a path outside the project must fail.
            target.unlink(missing_ok=True)
            sandboxed_result = await try_write_outside_project(False, project_path, target)
            print("default (sandboxed):", sandboxed_result["sandboxed"], sandboxed_result["exit_code"])
            assert sandboxed_result["sandboxed"] is True
            assert sandboxed_result["exit_code"] != 0, "write outside the project should have failed"
            assert not target.exists(), "sandboxing should have blocked this write"

            # --no-sandbox: the same write must now succeed.
            target.unlink(missing_ok=True)
            unsandboxed_result = await try_write_outside_project(True, project_path, target)
            print("--no-sandbox:", unsandboxed_result["sandboxed"], unsandboxed_result["exit_code"])
            assert unsandboxed_result["sandboxed"] is False
            assert unsandboxed_result["exit_code"] == 0, "write outside the project should have succeeded"
            assert target.exists() and target.read_text().strip() == "written"

            print("\nSUCCESS -- sandboxing blocks writes outside the project by default, "
                  "and --no-sandbox genuinely disables that containment")
        finally:
            registry.remove_project(project_path)
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
