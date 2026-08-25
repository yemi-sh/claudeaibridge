"""
Run `claudeaibridge serve` as a background service instead of a foreground
terminal process — a systemd user service on Linux, a launchd user agent on
macOS. Neither requires root: both are per-user mechanisms (systemd
`--user`, launchd LaunchAgents), matching how the rest of this tool never
needs elevated privileges.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

SERVICE_NAME = "claudeaibridge"
LAUNCHD_LABEL = "com.claudeaibridge.serve"


def _env_overrides() -> dict:
    """Environment variables to carry into the service process. systemd/
    launchd give spawned services a bare default environment, not the
    installing shell's -- without this, a custom XDG_CONFIG_HOME would
    make the interactive CLI and the background service silently read/write
    two different config directories."""
    overrides = {}
    if "XDG_CONFIG_HOME" in os.environ:
        overrides["XDG_CONFIG_HOME"] = os.environ["XDG_CONFIG_HOME"]
    return overrides


def _executable_argv() -> List[str]:
    """How to re-invoke this same tool: the PyInstaller binary if frozen,
    otherwise the installed console script, falling back to `python -m
    claudeaibridge.cli` if that's not on PATH (e.g. a dev install whose
    venv/bin isn't globally linked)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    found = shutil.which("claudeaibridge")
    if found:
        return [found]
    return [sys.executable, "-m", "claudeaibridge.cli"]


# -- Linux: systemd --user -------------------------------------------------

def _systemd_unit_path() -> Path:
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{SERVICE_NAME}.service"


def _install_linux(serve_args: List[str]) -> str:
    argv = _executable_argv() + ["serve", "--foreground"] + serve_args
    env_lines = "".join(f"Environment={k}={v}\n" for k, v in _env_overrides().items())
    unit = f"""[Unit]
Description=claudeaibridge - claude.ai coding agent bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
{env_lines}ExecStart={" ".join(argv)}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    path = _systemd_unit_path()
    path.write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=True)
    return str(path)


def _uninstall_linux() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
    path = _systemd_unit_path()
    if path.exists():
        path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


def _status_linux() -> str:
    result = subprocess.run(
        ["systemctl", "--user", "status", SERVICE_NAME], capture_output=True, text=True
    )
    return (result.stdout + result.stderr).strip()


# -- macOS: launchd ----------------------------------------------------------

def _launchd_plist_path() -> Path:
    d = Path.home() / "Library" / "LaunchAgents"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{LAUNCHD_LABEL}.plist"


def _install_macos(serve_args: List[str]) -> str:
    argv = _executable_argv() + ["serve", "--foreground"] + serve_args
    log_path = Path.home() / "Library" / "Logs" / "claudeaibridge.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    args_xml = "\n".join(f"        <string>{a}</string>" for a in argv)
    env_overrides = _env_overrides()
    env_xml = ""
    if env_overrides:
        entries = "\n".join(f"        <key>{k}</key><string>{v}</string>" for k, v in env_overrides.items())
        env_xml = f"    <key>EnvironmentVariables</key>\n    <dict>\n{entries}\n    </dict>\n"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
{env_xml}    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log_path}</string>
    <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""
    path = _launchd_plist_path()
    path.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    return str(path)


def _uninstall_macos() -> None:
    path = _launchd_plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)
        path.unlink()


def _status_macos() -> str:
    result = subprocess.run(
        ["launchctl", "list", LAUNCHD_LABEL], capture_output=True, text=True
    )
    return (result.stdout + result.stderr).strip()


# -- dispatch ----------------------------------------------------------------

def install(serve_args: List[str]) -> str:
    system = platform.system()
    if system == "Linux":
        if not shutil.which("systemctl"):
            raise RuntimeError("no systemd user session available (systemctl not found)")
        return _install_linux(serve_args)
    if system == "Darwin":
        if not shutil.which("launchctl"):
            raise RuntimeError("launchctl not found")
        return _install_macos(serve_args)
    raise RuntimeError(f"background service install is not supported on {system}")


def uninstall() -> None:
    system = platform.system()
    if system == "Linux":
        _uninstall_linux()
    elif system == "Darwin":
        _uninstall_macos()
    else:
        raise RuntimeError(f"Background service is not supported on {system}.")


def status() -> str:
    system = platform.system()
    if system == "Linux":
        return _status_linux()
    if system == "Darwin":
        return _status_macos()
    raise RuntimeError(f"Background service is not supported on {system}.")


def is_active() -> bool:
    """True if the background service is currently installed and running.
    False (not an exception) for 'not installed' or 'not running' -- only
    raises for a platform with no service manager concept at all."""
    system = platform.system()
    if system == "Linux":
        result = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME], capture_output=True, text=True
        )
        return result.stdout.strip() == "active"
    if system == "Darwin":
        result = subprocess.run(["launchctl", "list", LAUNCHD_LABEL], capture_output=True, text=True)
        return result.returncode == 0
    raise RuntimeError(f"Background service is not supported on {system}.")
