import argparse
import sys

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


def _cmd_serve(args) -> int:
    from . import server

    if args.transport == "stdio":
        server.run_stdio()
    else:
        server.run_http(host=args.host, port=args.port)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="claudeaibridge")
    sub = parser.add_subparsers(dest="command", required=True)

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
    p_serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
