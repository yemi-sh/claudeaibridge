"""
Shell sandboxing, per active project root.

Linux: bwrap mounts the whole filesystem read-only except the active
project's folder, which is bind-mounted read-write. This is the same
approach shell-agent used, just parametrized by `root` per call instead of
a single directory fixed at server startup — the active project can change
between calls (and differs between concurrent sessions).

macOS: no bwrap equivalent exists, so we use the built-in `sandbox-exec`
with a profile that denies all file writes except under `root`. It is a
narrower guarantee than bwrap's (network and process visibility are not
restricted), which is disclosed to the caller via `sandbox_kind()`.

Windows is not supported yet — `sandbox_available()` returns False there and
the caller must decide whether to refuse to run unsandboxed or warn loudly.
"""

import platform
import shutil


def _bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


def _sandbox_exec_available() -> bool:
    return shutil.which("sandbox-exec") is not None


def sandbox_kind() -> str:
    """One of 'bwrap', 'sandbox-exec', or 'none'."""
    system = platform.system()
    if system == "Linux" and _bwrap_available():
        return "bwrap"
    if system == "Darwin" and _sandbox_exec_available():
        return "sandbox-exec"
    return "none"


def sandbox_available() -> bool:
    return sandbox_kind() != "none"


def _bwrap_command(command: str, root: str) -> list[str]:
    # --tmpfs /tmp must come BEFORE the project bind-mount: mount order
    # determines which one wins when root lives under /tmp (a real case —
    # /tmp is itself a tmpfs on many distros). The later mount at a given
    # path wins, so binding root after the /tmp tmpfs keeps it visible
    # instead of being shadowed by the fresh, empty scratch tmpfs.
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", root, root,
        "--chdir", root,
        "/bin/bash", "-c", command,
    ]


def _sandbox_exec_profile(root: str) -> str:
    # Deny all file writes by default; allow them under `root`. Reads,
    # network, and process spawning are left unrestricted — real but
    # narrower containment than bwrap's read-only-everything-else.
    escaped_root = root.replace('"', '\\"')
    return f'''
(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath "{escaped_root}"))
(allow file-write* (subpath "/tmp"))
(allow file-write* (subpath "/private/tmp"))
(allow file-write* (subpath "/dev"))
'''


def _sandbox_exec_command(command: str, root: str) -> list[str]:
    profile = _sandbox_exec_profile(root)
    return ["sandbox-exec", "-p", profile, "/bin/bash", "-c", command]


def build_sandboxed_command(command: str, root: str) -> list[str]:
    """
    Wrap `command` so its filesystem writes are confined to `root`.
    Caller must check sandbox_available() first — this raises if no
    sandboxing mechanism exists on this platform.
    """
    kind = sandbox_kind()
    if kind == "bwrap":
        return _bwrap_command(command, root)
    if kind == "sandbox-exec":
        return _sandbox_exec_command(command, root)
    raise RuntimeError("No sandboxing mechanism available on this platform.")
