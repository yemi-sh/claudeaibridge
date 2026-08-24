"""
Interactive folder picker for `claudeaibridge init`.

A three-pane, checkbox-style browser built directly on prompt_toolkit
(questionary's underlying library) rather than composing questionary's
canned prompts — the interaction this needs doesn't match any of
questionary's built-in prompt shapes:

  - a pinned "Selected" pane at the top, always visible, so a folder can
    be deselected without navigating back to wherever it was picked
  - a scrollable "Browse" pane in the middle, showing the current
    directory's path, an active search filter, and its subfolders
  - a pinned footer at the bottom, always visible regardless of how much
    content is above it

Controls:
  Up/Down (or j/k)  move the cursor (stops at the top/bottom, no wrap)
  Space             toggle the highlighted folder's checkbox
  Enter             descend into the highlighted folder, or activate Done
  type              fuzzy-filter the current folder's subfolders
  Backspace         erase search text, or (when empty) go up one level
  Esc               clear search text, or (when empty) go up one level
                     (finishes, if already at the top)
  Ctrl-C            cancel — returns nothing selected

Falls back to plain repeated path prompts when stdin isn't a real
terminal (piped input, tests, CI) — prompt_toolkit needs a tty to render
a full-screen UI at all.
"""

import sys
from pathlib import Path
from typing import List

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style

_STYLE = Style.from_dict({
    "path": "fg:#61afef bold",
    "search": "fg:#c678dd",
    "hint": "fg:#5c6370 italic",
    "cursor": "reverse",
    "checked": "fg:#98c379 bold",
    "unchecked": "fg:#5c6370",
    "folder": "fg:#e5c07b",
    "done": "fg:#98c379 bold",
    "section": "fg:#5c6370 bold",
    "count": "fg:#c678dd",
    "sep": "fg:#3e4451",
})

_CHECK = "✓"
_TOP_MAX_HEIGHT = 12


def _list_subdirs(path: Path) -> List[Path]:
    try:
        entries = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except (PermissionError, OSError):
        return []
    return sorted(entries, key=lambda p: p.name.lower())


def _fuzzy_match(query: str, text: str) -> bool:
    """Subsequence match, case-insensitive: every character of `query`
    appears in `text` in order (not necessarily contiguous)."""
    it = iter(text.lower())
    return all(ch in it for ch in query.lower())


def pick_folders(start_dir: str) -> List[str]:
    """Three-pane checkbox browser. Returns the selected absolute paths
    (possibly empty, e.g. on Ctrl-C)."""
    current = Path(start_dir).expanduser().resolve()
    selected: set = set()
    # The cursor is tracked by the *identity* of the row it's on, not a raw
    # index — top_rows() grows/shrinks as folders are (de)selected, which
    # would silently shift what a plain integer index points to every time
    # the Selected section's size changes.
    cursor_key = ("done",)
    entries: List[Path] = []
    search_query = ""

    def rebuild_entries():
        nonlocal entries, cursor_key, search_query
        entries = _list_subdirs(current)
        cursor_key = ("done",)
        search_query = ""

    rebuild_entries()

    def visible_entries() -> List[Path]:
        if not search_query:
            return entries
        return [d for d in entries if _fuzzy_match(search_query, d.name)]

    def top_rows():
        rows = [{"kind": "done"}]
        for p in sorted(selected):
            rows.append({"kind": "selected", "path": Path(p)})
        return rows

    def browse_rows():
        return [{"kind": "entry", "path": d} for d in visible_entries()]

    def all_rows():
        return top_rows() + browse_rows()

    def row_key(row):
        if row["kind"] == "done":
            return ("done",)
        return (row["kind"], str(row["path"]))

    def cursor_index(rows) -> int:
        keys = [row_key(r) for r in rows]
        try:
            return keys.index(cursor_key)
        except ValueError:
            return 0

    def set_cursor_to_index(rows, index: int) -> None:
        nonlocal cursor_key
        index = max(0, min(index, len(rows) - 1))
        cursor_key = row_key(rows[index])

    def is_checked(path: Path) -> bool:
        return str(path) in selected

    def render_row(row, *, full_path: bool):
        is_cursor = row_key(row) == cursor_key
        prefix = "❯ " if is_cursor else "  "
        marker = [("[SetCursorPosition]", "")] if is_cursor else []

        if row["kind"] == "done":
            label = f"{prefix}{_CHECK} Done — {len(selected)} selected"
            style = "class:cursor" if is_cursor else "class:done"
            return marker + [(style, label + "\n")]

        d = row["path"]
        checked = row["kind"] == "selected" or is_checked(d)
        box = f"[{_CHECK}]" if checked else "[ ]"
        box_style = "checked" if checked else "unchecked"
        name = str(d) if full_path else f"{d.name}/"
        if is_cursor:
            return marker + [("class:cursor", f"{prefix}{box} {name}\n")]
        return marker + [
            (f"class:{box_style}", f"{prefix}{box} "),
            ("class:folder", name + "\n"),
        ]

    def render_top():
        rows = top_rows()
        lines = []
        for i, row in enumerate(rows):
            if row["kind"] == "selected" and (i == 0 or rows[i - 1]["kind"] != "selected"):
                lines.append(("class:section", " — Selected —\n"))
            lines.extend(render_row(row, full_path=True))
        return lines

    def render_browse():
        rows = browse_rows()
        lines = [("class:path", f" {current}\n")]
        if search_query:
            lines.append(("class:search", f" /{search_query}\n"))
        lines.append(("class:section", " — Browse —\n"))
        if not entries:
            lines.append(("class:hint", " (no subfolders here)\n"))
        elif search_query and not rows:
            lines.append(("class:hint", " (no matches)\n"))
        for row in rows:
            lines.extend(render_row(row, full_path=False))
        return lines

    def render_footer():
        return [
            ("class:count", f" {len(selected)} folder(s) selected  "),
            (
                "class:hint",
                "↑/↓ move · type to search · Space select · "
                "Enter open/done · Backspace/Esc back · Ctrl-C cancel",
            ),
        ]

    top_control = FormattedTextControl(render_top)
    browse_control = FormattedTextControl(render_browse)
    footer_control = FormattedTextControl(render_footer)

    layout = Layout(HSplit([
        Window(content=top_control, height=Dimension(max=_TOP_MAX_HEIGHT), always_hide_cursor=True),
        Window(height=1, char="─", style="class:sep"),
        Window(content=browse_control, height=Dimension(weight=1), always_hide_cursor=True),
        Window(height=1, char="─", style="class:sep"),
        Window(content=footer_control, height=1),
    ]))

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("k")
    def _up(event):
        rows = all_rows()
        set_cursor_to_index(rows, cursor_index(rows) - 1)

    @bindings.add("down")
    @bindings.add("j")
    def _down(event):
        rows = all_rows()
        set_cursor_to_index(rows, cursor_index(rows) + 1)

    @bindings.add("space")
    def _toggle(event):
        row = all_rows()[cursor_index(all_rows())]
        if row["kind"] == "done":
            return
        d = str(row["path"])
        if d in selected:
            selected.discard(d)
        else:
            selected.add(d)

    @bindings.add("enter")
    def _activate(event):
        nonlocal current
        row = all_rows()[cursor_index(all_rows())]
        if row["kind"] == "done":
            event.app.exit(result=sorted(selected))
            return
        current = row["path"]
        rebuild_entries()

    @bindings.add("backspace")
    def _backspace(event):
        nonlocal current, search_query
        if search_query:
            search_query = search_query[:-1]
        elif current.parent != current:
            current = current.parent
            rebuild_entries()

    @bindings.add("escape")
    def _escape(event):
        nonlocal current, search_query
        if search_query:
            search_query = ""
        elif current.parent != current:
            current = current.parent
            rebuild_entries()
        else:
            event.app.exit(result=sorted(selected))

    @bindings.add("c-c")
    def _cancel(event):
        event.app.exit(result=[])

    @bindings.add(Keys.Any)
    def _search_type(event):
        nonlocal search_query
        if event.data and event.data.isprintable():
            search_query += event.data

    app = Application(layout=layout, key_bindings=bindings, style=_STYLE, full_screen=True)
    result = app.run()
    return result or []


def prompt_for_projects(start_dir: str) -> List[str]:
    """Entry point used by onboarding: the interactive picker on a real
    terminal, or a plain repeated-path-prompt fallback otherwise."""
    if not sys.stdin.isatty():
        return _fallback_prompt()
    return pick_folders(start_dir)


def _fallback_prompt() -> List[str]:
    paths = []
    while True:
        try:
            path = input("Path to a project folder (leave blank to finish): ").strip()
        except EOFError:
            break
        if not path:
            break
        paths.append(path)
    return paths
