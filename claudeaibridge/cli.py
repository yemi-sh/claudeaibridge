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


def run_server(host: str, port: int, tunnel_choice: str, base_url: Optional[str], no_auth: bool) -> int:
    """Shared by `serve` and `init` (init gathers config interactively, then
    ends by calling straight into this — same code path either way)."""
    from . import server

    listener = None
    if tunnel_choice == "ngrok":
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
    return run_server(args.host, args.port, args.tunnel, args.base_url, args.no_auth)


def _cmd_init(_args) -> int:
    from . import onboarding

    return onboarding.run()


def _cmd_tunnel_set_authtoken(args) -> int:
    from . import tunnel

    tunnel.set_authtoken(args.token)
    print("ngrok authtoken saved.")
    return 0


def _cmd_tunnel_set_domain(args) -> int:
    from . import tunnel

    tunnel.set_domain(args.domain)
    print(f"ngrok static domain set to '{args.domain}'.")
    return 0


def _cmd_tunnel_status(_args) -> int:
    from . import tunnel

    s = tunnel.status()
    print(f"authtoken configured: {s['authtoken_configured']}")
    print(f"static domain override: {s['domain'] or '(none — using the account default domain)'}")
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
    p_serve.add_argument("--base-url", default=None, help="Public URL this server is reachable at. Overridden automatically when --tunnel ngrok is used. Defaults to http://<host>:<port>.")
    p_serve.add_argument("--no-auth", action="store_true", help="Disable OAuth (local testing only — do not use with a public tunnel).")
    p_serve.add_argument("--tunnel", choices=["none", "ngrok"], default="none", help="Expose the server publicly via ngrok. Requires 'claudeaibridge tunnel set-authtoken' first.")
    p_serve.set_defaults(func=_cmd_serve)

    p_tunnel = sub.add_parser("tunnel", help="Configure the ngrok tunnel.")
    tunnel_sub = p_tunnel.add_subparsers(dest="tunnel_command", required=True)

    p_tset_token = tunnel_sub.add_parser("set-authtoken", help="Store your ngrok authtoken.")
    p_tset_token.add_argument("token")
    p_tset_token.set_defaults(func=_cmd_tunnel_set_authtoken)

    p_tset_domain = tunnel_sub.add_parser("set-domain", help="Override the domain to use (default: your account's own assigned domain).")
    p_tset_domain.add_argument("domain", help="e.g. a custom paid domain. Not needed for the free plan's own auto-assigned domain.")
    p_tset_domain.set_defaults(func=_cmd_tunnel_set_domain)

    p_tstatus = tunnel_sub.add_parser("status", help="Show current tunnel configuration.")
    p_tstatus.set_defaults(func=_cmd_tunnel_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
