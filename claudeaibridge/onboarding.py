"""
Interactive first-run setup: `claudeaibridge init`.

Walks through the one-time setup a new install needs (ngrok authtoken,
optionally a persistent domain, at least one registered project), then
hands off to the same server-starting code path as `claudeaibridge serve`.
Re-running `init` later is safe — every step shows what's already
configured and lets you keep it or change it, rather than forcing you
through the whole thing again.
"""

import sys
from pathlib import Path

from . import registry, tunnel


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value or default


def _ask_yes_no(prompt: str, default_yes: bool) -> bool:
    suffix = " [Y/n]" if default_yes else " [y/N]"
    try:
        value = input(f"{prompt}{suffix}: ").strip().lower()
    except EOFError:
        value = ""
    if not value:
        return default_yes
    return value.startswith("y")


def _step_authtoken() -> bool:
    print("\n--- ngrok authtoken ---")
    existing = tunnel.get_authtoken()
    if existing:
        masked = existing[:4] + "..." + existing[-4:] if len(existing) > 8 else "***"
        if not _ask_yes_no(f"An authtoken is already configured ({masked}). Replace it?", default_yes=False):
            return True
    print(
        "claudeaibridge needs an ngrok account to get a public URL for this "
        "machine. Get a free authtoken at:\n"
        "  https://dashboard.ngrok.com/get-started/your-authtoken"
    )
    token = _ask("Paste your ngrok authtoken (leave blank to cancel)")
    if not token:
        print("No authtoken provided. You can set one later with: claudeaibridge tunnel set-authtoken <token>")
        return False
    tunnel.set_authtoken(token)
    print("Saved.")
    return True


def _step_domain() -> None:
    print("\n--- Persistent URL ---")
    existing = tunnel.get_domain()
    if existing:
        if not _ask_yes_no(f"A static domain is already configured ({existing}). Replace it?", default_yes=False):
            return
    print(
        "Every ngrok account is automatically given one static domain — "
        "it's already sitting in your dashboard, nothing to create:\n"
        "  https://dashboard.ngrok.com/domains\n"
        "(looks like your-name.ngrok-free.app). Using it means the "
        "connector URL stays the same across restarts, so you don't have "
        "to re-add it in claude.ai every time."
    )
    domain = _ask("Paste it here (leave blank to skip — the URL will then change on every restart)")
    if domain:
        tunnel.set_domain(domain)
        print("Saved.")


def _step_projects() -> bool:
    print("\n--- Projects ---")
    existing = registry.list_projects()
    if existing:
        print("Already registered:")
        for name, info in sorted(existing.items()):
            print(f"  {name} -> {info['path']}")
        if not _ask_yes_no("Register another project?", default_yes=False):
            return True

    while True:
        path = _ask("Path to a project folder claude.ai should be able to work in (leave blank to finish)")
        if not path:
            break
        try:
            name = registry.add_project(path)
        except (NotADirectoryError, FileNotFoundError) as e:
            print(f"  error: {e}")
            continue
        print(f"  Registered '{name}' -> {registry.get_project_path(name)}")
        if not _ask_yes_no("Register another?", default_yes=False):
            break

    return bool(registry.list_projects())


def run() -> int:
    print("claudeaibridge setup")
    print("=====================")
    print(
        "This will let claude.ai read, edit, and run shell commands in "
        "project folders you explicitly choose on this machine."
    )

    if not _step_authtoken():
        return 1
    _step_domain()
    if not _step_projects():
        print(
            "\nNo projects registered. You need at least one before starting "
            "the server — run `claudeaibridge add-project <path>` and then "
            "`claudeaibridge serve --tunnel ngrok`."
        )
        return 1

    print("\n--- Starting server ---")
    from .cli import run_server

    return run_server(host="127.0.0.1", port=8420, tunnel_choice="ngrok", base_url=None, no_auth=False)
