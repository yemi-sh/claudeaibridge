import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from . import registry
from . import term_colors as tc


def _cmd_add_project(args) -> int:
    if args.path:
        try:
            resolved = registry.add_project(args.path)
        except (NotADirectoryError, FileNotFoundError) as e:
            print(tc.error(f"error: {e}"), file=sys.stderr)
            return 1
        print(tc.success(f"Registered {resolved}"))
        return 0

    from . import picker

    selected = picker.prompt_for_projects(
        str(Path.home()), instruction="Pick the folder(s) claude.ai should be able to work in."
    )
    if not selected:
        print(tc.hint("No folders selected."))
        return 0
    for p in selected:
        try:
            resolved = registry.add_project(p)
            print(tc.success(f"Registered {resolved}"))
        except (NotADirectoryError, FileNotFoundError) as e:
            print(tc.error(f"error: {e}"), file=sys.stderr)
    return 0


def _cmd_edit_project(args) -> int:
    if args.path:
        if registry.remove_project(args.path):
            print(tc.success(f"Removed {args.path}"))
            return 0
        print(tc.error(f"error: '{args.path}' is not registered."), file=sys.stderr)
        return 1

    if not sys.stdin.isatty():
        print(tc.error(
            "Interactive editing needs a real terminal. To remove one project "
            "directly: claudeaibridge edit-project <path>"
        ), file=sys.stderr)
        return 1

    from . import picker

    before = set(registry.list_projects())
    if not before:
        print(tc.hint("No projects registered yet. Use `claudeaibridge add-project` first."))
        return 0

    after = set(picker.pick_folders(
        str(Path.home()), initial_selected=before,
        instruction="Check/uncheck folders to add or remove them.",
    ))
    for p in sorted(after - before):
        registry.add_project(p)
        print(tc.success(f"Added {p}"))
    for p in sorted(before - after):
        registry.remove_project(p)
        print(tc.hint(f"Removed {p}"))
    if after == before:
        print(tc.hint("No changes."))
    return 0


def _cmd_list_projects(_args) -> int:
    projects = registry.list_projects()
    if not projects:
        print(tc.hint("No projects registered. Add one with: claudeaibridge add-project <path>"))
        return 0
    for path in sorted(projects):
        print(path)
    return 0


def run_server(host: str, port: int, use_ngrok: bool, base_url: Optional[str], no_auth: bool,
                no_sandbox: bool = False) -> int:
    """Shared by `serve` and `init` (init gathers config interactively, then
    ends by calling straight into this — same code path either way). Caller
    is responsible for the --no-sandbox warning, since this can be reached
    either directly or as a fallback from install_and_wait — printing it
    here too would show it twice on that fallback path."""
    from . import server

    listener = None
    if use_ngrok:
        from . import tunnel

        try:
            listener = tunnel.start(port)
        except (RuntimeError, ValueError) as e:
            print(tc.error(f"error: could not start ngrok tunnel: {e}"), file=sys.stderr)
            return 1
        base_url = listener.url()
        print(tc.success(f"ngrok tunnel up: {base_url}"))

    auth_provider = None
    if not no_auth:
        from .oauth import ConsentOAuthProvider

        base_url = base_url or f"http://{host}:{port}"
        auth_provider = ConsentOAuthProvider(base_url=base_url, state_dir=registry.config_dir())

    connector_url = f"{(base_url or f'http://{host}:{port}').rstrip('/')}/mcp"
    registry.write_connector_url(connector_url)
    print()
    print(tc.header("=" * 60))
    print("Connector URL for claude.ai (Settings -> Connectors -> Add custom connector):")
    print(f"  {tc.accent(connector_url)}")
    print(tc.header("=" * 60))
    print()
    # Explicit flush: stdout is fully buffered (not line-buffered) whenever
    # it isn't a live terminal — piped, redirected to a file, captured by a
    # process supervisor — and server.run_http() below blocks forever, so
    # without this the most important line we print could just sit in the
    # buffer and never actually reach the reader.
    sys.stdout.flush()

    try:
        server.run_http(host=host, port=port, auth_provider=auth_provider, no_sandbox=no_sandbox)
    finally:
        if listener is not None:
            listener.close()
    return 0


def install_and_wait(serve_args: list, *, host: str, port: int, use_ngrok: bool,
                      base_url: Optional[str], no_auth: bool, no_sandbox: bool = False) -> int:
    """Install serve_args as the background service and report the connector
    URL once it comes up. Falls back to running in the foreground right here
    if the platform has no service manager. Shared by `init` and `serve`."""
    from . import service

    if no_sandbox:
        print(tc.error(
            "WARNING: --no-sandbox is set. Shell commands will run WITHOUT "
            "filesystem containment — they can read/write anywhere this OS "
            "user can, not just the active project folder. Only use this if "
            "you understand and accept that."
        ))

    try:
        service_path = service.install(serve_args)
    except Exception as e:
        print(tc.hint(f"Could not run as a background service ({e}) — running in the foreground instead."))
        return run_server(host=host, port=port, use_ngrok=use_ngrok, base_url=base_url, no_auth=no_auth,
                           no_sandbox=no_sandbox)

    print(tc.success(f"Installed and running in the background: {service_path}"))
    print("Waiting for it to come up...")

    registry.clear_connector_url()
    url = None
    for _ in range(20):
        url = registry.read_connector_url()
        if url:
            break
        time.sleep(0.5)

    if url:
        print()
        print(tc.header("=" * 60))
        print("Connector URL for claude.ai (Settings -> Connectors -> Add custom connector):")
        print(f"  {tc.accent(url)}")
        print(tc.header("=" * 60))
    else:
        print(tc.error("Could not confirm the server started — check `claudeaibridge status`."))
        return 1

    print(tc.hint("\nIt'll keep running in the background. Check on it anytime with: claudeaibridge status"))
    return 0


def _cmd_serve(args) -> int:
    if args.transport == "stdio":
        from . import server

        server.run_stdio()
        return 0

    from . import service

    try:
        active = service.is_active()
    except RuntimeError:
        active = None

    if args.foreground:
        if active:
            print(tc.hint("Stopping the background service so this can run in the foreground..."))
            service.uninstall()
            registry.clear_connector_url()
        if args.no_sandbox:
            print(tc.error(
                "WARNING: --no-sandbox is set. Shell commands will run WITHOUT "
                "filesystem containment — they can read/write anywhere this OS "
                "user can, not just the active project folder. Only use this if "
                "you understand and accept that."
            ))
        return run_server(args.host, args.port, args.ngrok, args.base_url, args.no_auth,
                           no_sandbox=args.no_sandbox)

    if active:
        print(tc.success("Background service already running."))
        url = registry.read_connector_url()
        if url:
            print(f"Connector URL: {tc.accent(url)}")
        else:
            print(tc.hint("No connector URL recorded yet — check `claudeaibridge status`."))
        return 0

    serve_args = ["--host", args.host, "--port", str(args.port)]
    if args.ngrok:
        serve_args.append("--ngrok")
    if args.base_url:
        serve_args += ["--base-url", args.base_url]
    if args.no_auth:
        serve_args.append("--no-auth")
    if args.no_sandbox:
        serve_args.append("--no-sandbox")

    return install_and_wait(serve_args, host=args.host, port=args.port,
                             use_ngrok=args.ngrok, base_url=args.base_url, no_auth=args.no_auth,
                             no_sandbox=args.no_sandbox)


def _cmd_init(_args) -> int:
    from . import onboarding

    return onboarding.run()


def _cmd_ngrok_set_authtoken(args) -> int:
    from . import tunnel

    tunnel.set_authtoken(args.token)
    print(tc.success("ngrok authtoken saved."))
    return 0


def _cmd_ngrok_status(_args) -> int:
    from . import tunnel

    s = tunnel.status()
    print(f"authtoken configured: {tc.success(str(s['authtoken_configured'])) if s['authtoken_configured'] else tc.hint('False')}")
    return 0


def _cmd_status(_args) -> int:
    from . import service

    try:
        active = service.is_active()
    except RuntimeError:
        active = None

    if active is True:
        print(tc.success("Background service: running"))
    elif active is False:
        print(tc.hint("Background service: not running"))
    else:
        print(tc.hint("Background service: not supported on this platform"))

    url = registry.read_connector_url()
    if url:
        print(f"Last known connector URL: {tc.accent(url)}")
    else:
        print(tc.hint("No connector URL recorded yet — run `claudeaibridge init` or `claudeaibridge serve`."))
    return 0


def _cmd_stop(_args) -> int:
    from . import service

    try:
        service.uninstall()
    except RuntimeError as e:
        print(tc.error(f"error: {e}"), file=sys.stderr)
        return 1
    registry.clear_connector_url()
    print(tc.success("Background service stopped and uninstalled."))
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

    p_serve = sub.add_parser("serve", help="Install/start the MCP server as the background service (unless --foreground).")
    p_serve.add_argument("--transport", choices=["http", "stdio"], default="http")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8420)
    p_serve.add_argument("--base-url", default=None, help="Public URL this server is reachable at (e.g. your own domain/tunnel). Overridden automatically when --ngrok is used. Defaults to http://<host>:<port>.")
    p_serve.add_argument("--no-auth", action="store_true", help="Disable OAuth (local testing only — do not use with a public tunnel).")
    p_serve.add_argument("--ngrok", action="store_true", help="Expose the server publicly via ngrok. Requires 'claudeaibridge ngrok set-authtoken' first. Omit this if you're using your own domain/tunnel via --base-url instead.")
    p_serve.add_argument("--foreground", action="store_true", help="Run directly in this terminal instead of installing/using the background service. Stops the background service first if one is running.")
    p_serve.add_argument("--no-sandbox", action="store_true", help="DANGEROUS: disable filesystem sandboxing for shell_execute. Commands can then read/write anywhere this OS user can, not just the active project folder. A local, startup-time decision only — never exposed as something the connected client can toggle.")
    p_serve.set_defaults(func=_cmd_serve)

    p_ngrok = sub.add_parser("ngrok", help="Configure the ngrok tunnel.")
    ngrok_sub = p_ngrok.add_subparsers(dest="ngrok_command", required=True)

    p_nset_token = ngrok_sub.add_parser("set-authtoken", help="Store your ngrok authtoken.")
    p_nset_token.add_argument("token")
    p_nset_token.set_defaults(func=_cmd_ngrok_set_authtoken)

    p_nstatus = ngrok_sub.add_parser("status", help="Show current tunnel configuration.")
    p_nstatus.set_defaults(func=_cmd_ngrok_status)

    p_status = sub.add_parser("status", help="Show whether the background service is running and its connector URL.")
    p_status.set_defaults(func=_cmd_status)

    p_stop = sub.add_parser("stop", help="Stop and uninstall the background service.")
    p_stop.set_defaults(func=_cmd_stop)

    args = parser.parse_args()
    try:
        sys.exit(args.func(args))
    except KeyboardInterrupt:
        # Blanket safety net: anything that Ctrl-C's out of a raw input(),
        # a blocking network call (ngrok, uvicorn), or anywhere else that
        # isn't already caught closer to the source (questionary/prompt_toolkit
        # prompts handle their own) should still exit cleanly, not dump a
        # traceback.
        print(tc.hint("\nCancelled."))
        sys.exit(130)


if __name__ == "__main__":
    main()
