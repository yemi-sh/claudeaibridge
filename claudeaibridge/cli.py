import argparse
import sys
from typing import Optional

from . import registry


def _cmd_add_project(args) -> int:
    try:
        name = registry.add_project(args.path, name=args.name)
    except (NotADirectoryError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Registered '{name}' -> {registry.get_project_path(name)}")
    return 0


def _cmd_remove_project(args) -> int:
    if registry.remove_project(args.name):
        print(f"Removed '{args.name}'.")
        return 0
    print(f"error: no project named '{args.name}'", file=sys.stderr)
    return 1


def _cmd_list_projects(_args) -> int:
    projects = registry.list_projects()
    if not projects:
        print("No projects registered. Add one with: claudeaibridge add-project <path>")
        return 0
    for name, info in sorted(projects.items()):
        print(f"{name}\t{info['path']}")
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
    p_add.add_argument("path", help="Path to the project folder.")
    p_add.add_argument("--name", default=None, help="Name to register it under (default: folder name).")
    p_add.set_defaults(func=_cmd_add_project)

    p_remove = sub.add_parser("remove-project", help="Unregister a project.")
    p_remove.add_argument("name")
    p_remove.set_defaults(func=_cmd_remove_project)

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
