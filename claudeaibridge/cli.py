import argparse
import sys
from pathlib import Path
from typing import Optional

from . import registry


def _cmd_add_project(args) -> int:
    if args.path:
        try:
            resolved = registry.add_project(args.path)
        except (NotADirectoryError, FileNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"Registered {resolved}")
        return 0

    from . import picker

    selected = picker.prompt_for_projects(str(Path.home()))
    if not selected:
        print("No folders selected.")
        return 0
    for p in selected:
        try:
            resolved = registry.add_project(p)
            print(f"Registered {resolved}")
        except (NotADirectoryError, FileNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr)
    return 0


def _cmd_edit_project(args) -> int:
    if args.path:
        if registry.remove_project(args.path):
            print(f"Removed {args.path}")
            return 0
        print(f"error: '{args.path}' is not registered.", file=sys.stderr)
        return 1

    if not sys.stdin.isatty():
        print(
            "Interactive editing needs a real terminal. To remove one project "
            "directly: claudeaibridge edit-project <path>",
            file=sys.stderr,
        )
        return 1

    from . import picker

    before = set(registry.list_projects())
    if not before:
        print("No projects registered yet. Use `claudeaibridge add-project` first.")
        return 0

    after = set(picker.pick_folders(str(Path.home()), initial_selected=before))
    for p in sorted(after - before):
        registry.add_project(p)
        print(f"Added {p}")
    for p in sorted(before - after):
        registry.remove_project(p)
        print(f"Removed {p}")
    if after == before:
        print("No changes.")
    return 0


def _cmd_list_projects(_args) -> int:
    projects = registry.list_projects()
    if not projects:
        print("No projects registered. Add one with: claudeaibridge add-project <path>")
        return 0
    for path in sorted(projects):
        print(path)
    return 0


def run_server(host: str, port: int, use_ngrok: bool, base_url: Optional[str], no_auth: bool) -> int:
    """Shared by `serve` and `init` (init gathers config interactively, then
    ends by calling straight into this — same code path either way)."""
    from . import server

    listener = None
    if use_ngrok:
        from . import tunnel

        try:
            listener = tunnel.start(port)
        except (RuntimeError, ValueError) as e:
            print(f"error: could not start ngrok tunnel: {e}", file=sys.stderr)
            return 1
        base_url = listener.url()
        print(f"ngrok tunnel up: {base_url}")

    auth_provider = None
    if not no_auth:
        from .oauth import ConsentOAuthProvider

        base_url = base_url or f"http://{host}:{port}"
        auth_provider = ConsentOAuthProvider(base_url=base_url, state_dir=registry.config_dir())

    connector_url = f"{(base_url or f'http://{host}:{port}').rstrip('/')}/mcp"
    print()
    print("=" * 60)
    print(f"Connector URL for claude.ai (Settings -> Connectors -> Add custom connector):")
    print(f"  {connector_url}")
    print("=" * 60)
    print()
    # Explicit flush: stdout is fully buffered (not line-buffered) whenever
    # it isn't a live terminal — piped, redirected to a file, captured by a
    # process supervisor — and server.run_http() below blocks forever, so
    # without this the most important line we print could just sit in the
    # buffer and never actually reach the reader.
    sys.stdout.flush()

    try:
        server.run_http(host=host, port=port, auth_provider=auth_provider)
    finally:
        if listener is not None:
            listener.close()
    return 0


def _cmd_serve(args) -> int:
    if args.transport == "stdio":
        from . import server

        server.run_stdio()
        return 0
    return run_server(args.host, args.port, args.ngrok, args.base_url, args.no_auth)


def _cmd_init(_args) -> int:
    from . import onboarding

    return onboarding.run()


def _cmd_ngrok_set_authtoken(args) -> int:
    from . import tunnel

    tunnel.set_authtoken(args.token)
    print("ngrok authtoken saved.")
    return 0


def _cmd_ngrok_status(_args) -> int:
    from . import tunnel

    s = tunnel.status()
    print(f"authtoken configured: {s['authtoken_configured']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="claudeaibridge")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Interactive first-run setup: ngrok, a project, and start serving.")
    p_init.set_defaults(func=_cmd_init)

    p_add = sub.add_parser("add-project", help="Register a folder as an allowed project.")
    p_add.add_argument("path", nargs="?", default=None, help="Path to the project folder. Omit to pick one (or several) with the interactive folder browser.")
    p_add.set_defaults(func=_cmd_add_project)

    p_edit = sub.add_parser("edit-project", help="Add/remove registered projects.")
    p_edit.add_argument("path", nargs="?", default=None, help="Path to unregister directly. Omit to open the interactive browser, pre-checked with everything currently registered — check/uncheck anything to add or remove it.")
    p_edit.set_defaults(func=_cmd_edit_project)

    p_list = sub.add_parser("list-projects", help="List registered projects.")
    p_list.set_defaults(func=_cmd_list_projects)

    p_serve = sub.add_parser("serve", help="Run the MCP server.")
    p_serve.add_argument("--transport", choices=["http", "stdio"], default="http")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8420)
    p_serve.add_argument("--base-url", default=None, help="Public URL this server is reachable at (e.g. your own domain/tunnel). Overridden automatically when --ngrok is used. Defaults to http://<host>:<port>.")
    p_serve.add_argument("--no-auth", action="store_true", help="Disable OAuth (local testing only — do not use with a public tunnel).")
    p_serve.add_argument("--ngrok", action="store_true", help="Expose the server publicly via ngrok. Requires 'claudeaibridge ngrok set-authtoken' first. Omit this if you're using your own domain/tunnel via --base-url instead.")
    p_serve.set_defaults(func=_cmd_serve)

    p_ngrok = sub.add_parser("ngrok", help="Configure the ngrok tunnel.")
    ngrok_sub = p_ngrok.add_subparsers(dest="ngrok_command", required=True)

    p_nset_token = ngrok_sub.add_parser("set-authtoken", help="Store your ngrok authtoken.")
    p_nset_token.add_argument("token")
    p_nset_token.set_defaults(func=_cmd_ngrok_set_authtoken)

    p_nstatus = ngrok_sub.add_parser("status", help="Show current tunnel configuration.")
    p_nstatus.set_defaults(func=_cmd_ngrok_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
