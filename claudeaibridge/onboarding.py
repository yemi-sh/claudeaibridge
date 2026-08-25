"""
Interactive first-run setup: `claudeaibridge init`.

Walks through the one-time setup a new install needs (ngrok authtoken, at
least one registered project), then installs and starts the server as a
background service (systemd/launchd) so it keeps running after this
terminal closes — falling back to a foreground `claudeaibridge serve` if
no service manager is available (e.g. Windows, or a container without
systemd). The authtoken alone is enough — every
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

import pyfiglet
import questionary

from . import picker, registry, term_colors as tc, tunnel

# Same palette as picker.py's _STYLE, for a consistent look across the
# wizard — questionary's own default leaves pointer/highlighted/selected
# completely uncolored.
_MENU_STYLE = questionary.Style([
    ("qmark", "fg:#61afef bold"),
    ("question", "bold"),
    ("pointer", "fg:#98c379 bold"),
    ("highlighted", "fg:#98c379 bold"),
    ("selected", "fg:#98c379"),
    ("answer", "fg:#c678dd bold"),
    ("instruction", "fg:#5c6370 italic"),
])

# Gradient endpoints for the banner, same blue -> purple sweep as the rest
# of the wizard's palette (picker.py's path color -> _MENU_STYLE's answer
# color).
_GRADIENT_START = (0x61, 0xAF, 0xEF)
_GRADIENT_END = (0xC6, 0x78, 0xDD)


def _print_banner() -> None:
    art = pyfiglet.figlet_format("claudeaibridge", font="standard").rstrip("\n").split("\n")
    width = max((len(line) for line in art), default=1)
    colorize = sys.stdout.isatty()

    print()
    for line in art:
        if not colorize:
            print(line)
            continue
        out = []
        for i, ch in enumerate(line):
            if ch.strip():
                t = i / max(width - 1, 1)
                r = round(_GRADIENT_START[0] + (_GRADIENT_END[0] - _GRADIENT_START[0]) * t)
                g = round(_GRADIENT_START[1] + (_GRADIENT_END[1] - _GRADIENT_START[1]) * t)
                b = round(_GRADIENT_START[2] + (_GRADIENT_END[2] - _GRADIENT_START[2]) * t)
                out.append(f"\033[38;2;{r};{g};{b}m{ch}\033[0m")
            else:
                out.append(ch)
        print("".join(out))
    print("  A coding agent on your machine, driven from claude.ai")
    print()


class _Cancelled(Exception):
    """Raised when the user Ctrl-C's out of an interactive prompt. questionary
    itself swallows KeyboardInterrupt and returns None from .ask() -- that's
    otherwise indistinguishable from a real answer, so every call site here
    turns it back into something that actually stops the wizard instead of
    silently falling through to a default."""


def _ask(prompt: str, default: str = "") -> str:
    if sys.stdin.isatty():
        value = questionary.text(prompt, default=default, style=_MENU_STYLE).ask()
        if value is None:
            raise _Cancelled()
        return value
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value or default


def _ask_yes_no(prompt: str, default_yes: bool) -> bool:
    if sys.stdin.isatty():
        value = questionary.confirm(prompt, default=default_yes, style=_MENU_STYLE).ask()
        if value is None:
            raise _Cancelled()
        return value
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
    print(tc.header("\n--- Public URL ---"))

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
        style=_MENU_STYLE,
    ).ask()
    if choice is None:
        raise _Cancelled()

    if choice == "own":
        # No silent fallback to ngrok: this loops until a real URL is given,
        # or the user Ctrl-C's out entirely (via _ask's own _Cancelled).
        while True:
            base_url = _ask("Public URL claude.ai should use to reach this server (e.g. https://your-domain.example)")
            if base_url:
                return False, base_url, False
            print(tc.error("A URL is required for this option."))

    if choice == "local":
        return False, None, True

    return True, None, False


def _step_authtoken() -> bool:
    print(tc.header("\n--- ngrok authtoken ---"))
    existing = tunnel.get_authtoken()
    if existing:
        masked = existing[:4] + "..." + existing[-4:] if len(existing) > 8 else "***"
        if not _ask_yes_no(f"An authtoken is already configured ({masked}). Replace it?", default_yes=False):
            return True
    print(
        "claudeaibridge needs an ngrok account to get a public URL for this "
        "machine. Get a free authtoken at:\n"
        "  " + tc.accent("https://dashboard.ngrok.com/get-started/your-authtoken")
    )
    token = _ask("Paste your ngrok authtoken (leave blank to cancel)")
    if not token:
        print(tc.error("No authtoken provided.") + " You can set one later with: claudeaibridge ngrok set-authtoken <token>")
        return False
    tunnel.set_authtoken(token)
    print(tc.success("Saved."))
    return True


def _step_projects() -> bool:
    print(tc.header("\n--- Projects ---"))
    before = set(registry.list_projects())
    if before:
        print("Already registered:")
        for path in sorted(before):
            print(f"  {path}")

    instruction = "Pick the folder(s) claude.ai should be able to work in."

    if not sys.stdin.isatty():
        for path in picker.prompt_for_projects(str(Path.home()), instruction=instruction):
            try:
                resolved = registry.add_project(path)
            except (NotADirectoryError, FileNotFoundError) as e:
                print(tc.error(f"  error: {e}"))
                continue
            print(tc.success(f"  Registered {resolved}"))
        return bool(registry.list_projects())

    # Always reopens the picker, pre-checked with whatever's already
    # registered -- check/uncheck anything to add or remove it, rather than
    # a separate yes/no gate in front of it.
    after = set(picker.pick_folders(str(Path.home()), initial_selected=before, instruction=instruction))
    for p in sorted(after - before):
        registry.add_project(p)
        print(tc.success(f"  Added {p}"))
    for p in sorted(before - after):
        registry.remove_project(p)
        print(tc.hint(f"  Removed {p}"))

    return bool(registry.list_projects())


def run() -> int:
    _print_banner()
    print(
        "This will let claude.ai read, edit, and run shell commands in "
        "project folders you explicitly choose on this machine."
    )

    try:
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
            print(tc.error(
                "\nNo projects registered."
            ) + " You need at least one before starting "
                f"the server — run `claudeaibridge add-project <path>` and then `{next_cmd}`."
            )
            return 1
    except _Cancelled:
        print(tc.hint("\nSetup cancelled."))
        return 130

    print(tc.header("\n--- Starting server ---"))

    serve_args = ["--host", "127.0.0.1", "--port", "8420"]
    if use_ngrok:
        serve_args.append("--ngrok")
    if base_url:
        serve_args += ["--base-url", base_url]
    if no_auth:
        serve_args.append("--no-auth")

    from .cli import install_and_wait

    return install_and_wait(serve_args, host="127.0.0.1", port=8420,
                             use_ngrok=use_ngrok, base_url=base_url, no_auth=no_auth)
