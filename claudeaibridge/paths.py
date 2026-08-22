"""
Path containment
-----------------
Every file tool must call resolve_within() before touching disk. It is the
one piece of logic standing between "Claude can edit files in the project
you picked" and "Claude can edit any file this OS user can reach" — so it
resolves symlinks too, not just '..' segments, since a symlink inside the
project root that points outside it is just as much an escape as a literal
'../../' in the path.
"""

from pathlib import Path


class PathEscapesProject(Exception):
    def __init__(self, requested: str, root: str):
        self.requested = requested
        self.root = root
        super().__init__(
            f"'{requested}' resolves outside the active project root '{root}'."
        )


def resolve_within(root: str, requested_path: str) -> Path:
    """
    Resolve `requested_path` (relative or absolute) against `root` and
    return it — only if the result is inside `root`. Raises
    PathEscapesProject otherwise.

    Relative paths are joined onto root first, since a bare 'foo.py' should
    mean "foo.py in the project", not "foo.py in whatever the server
    process's cwd happens to be".
    """
    root_resolved = Path(root).resolve()

    candidate = Path(requested_path)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate

    # resolve(strict=False): the target may not exist yet (e.g. file_write
    # creating a new file), but every existing ancestor's symlinks are still
    # followed and collapsed, which is what catches a symlink escape.
    resolved = candidate.resolve(strict=False)

    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise PathEscapesProject(requested_path, str(root_resolved))

    return resolved
