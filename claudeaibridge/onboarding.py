"""
Interactive first-run setup: `claudeaibridge init`.

Walks through the one-time setup a new install needs (ngrok authtoken, at
least one registered project), then hands off to the same server-starting
code path as `claudeaibridge serve`. The authtoken alone is enough — every
ngrok account is given one permanent static domain, and the agent binds to
it automatically once authenticated. Someone with their own domain and
their own way of routing to this machine (a VPS, their own reverse proxy,
an already-running tunnel) doesn't need ngrok at all — see `serve
--base-url` instead.
Re-running `init` later is safe — every step shows what's already
configured and lets you keep it or change it, rather than forcing you
through the whole thing again.
"""

import sys
from pathlib import Path

import questionary

from . import picker, registry, tunnel


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


def _step_public_url():
    """Returns (use_ngrok: bool, base_url: str | None, no_auth: bool)."""
    print("\n--- Public URL ---")

    if not sys.stdin.isatty():
        return True, None, False  # non-interactive: default to ngrok

    choice = questionary.select(
        "How should claude.ai reach this machine?",
        choices=[
            questionary.Choice(
                title="ngrok — I don't have a domain or tunnel of my own (recommended)",
                value="ngrok",
            ),
            questionary.Choice(
                title="My own domain/tunnel — already pointed at this machine",
                value="own",
            ),
            questionary.Choice(
                title="Local only — no public access, just testing on this machine",
                value="local",
            ),
        ],
    ).ask()

    if choice == "own":
        base_url = _ask("Public URL claude.ai should use to reach this server (e.g. https://your-domain.example)")
        if base_url:
            return False, base_url, False
        print("No URL given — falling back to ngrok.")
        return True, None, False

    if choice == "local":
        return False, None, True

    return True, None, False


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
        print("No authtoken provided. You can set one later with: claudeaibridge ngrok set-authtoken <token>")
        return False
    tunnel.set_authtoken(token)
    print("Saved.")
    return True


def _step_projects() -> bool:
    print("\n--- Projects ---")
    existing = registry.list_projects()
    if existing:
        print("Already registered:")
        for path in sorted(existing):
            print(f"  {path}")
        if not _ask_yes_no("Register another project?", default_yes=False):
            return True

    print("Pick the folder(s) claude.ai should be able to work in.")
    for path in picker.prompt_for_projects(str(Path.home())):
        try:
            resolved = registry.add_project(path)
        except (NotADirectoryError, FileNotFoundError) as e:
            print(f"  error: {e}")
            continue
        print(f"  Registered {resolved}")

    return bool(registry.list_projects())


def run() -> int:
    print("claudeaibridge setup")
    print("=====================")
    print(
        "This will let claude.ai read, edit, and run shell commands in "
        "project folders you explicitly choose on this machine."
    )

    use_ngrok, base_url, no_auth = _step_public_url()
    if use_ngrok and not _step_authtoken():
        return 1
    if not _step_projects():
        if use_ngrok:
            next_cmd = "claudeaibridge serve --ngrok"
        elif base_url:
            next_cmd = f"claudeaibridge serve --base-url {base_url}"
        else:
            next_cmd = "claudeaibridge serve --no-auth"
        print(
            "\nNo projects registered. You need at least one before starting "
            f"the server — run `claudeaibridge add-project <path>` and then `{next_cmd}`."
        )
        return 1

    print("\n--- Starting server ---")
    from .cli import run_server

    return run_server(host="127.0.0.1", port=8420, use_ngrok=use_ngrok, base_url=base_url, no_auth=no_auth)
