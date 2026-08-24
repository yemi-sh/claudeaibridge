"""
Interactive folder picker for `claudeaibridge init`.

A single-screen, checkbox-style browser built directly on prompt_toolkit
(questionary's underlying library) rather than composing questionary's
canned prompts — the interaction this needs (mark folders with Space while
also being able to descend with Enter, fuzzy-filter by typing, and see
everything selected so far pinned at the top, all on one screen) doesn't
match any of questionary's built-in prompt shapes.

Controls:
  Up/Down (or j/k)  move the cursor
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
})

_CHECK = "✓"
_EMPTY_BOX = " "


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
    """Full-screen checkbox browser. Returns the selected absolute paths
    (possibly empty, e.g. on Ctrl-C)."""
    current = Path(start_dir).expanduser().resolve()
    selected: set = set()
    cursor = 0
    entries: List[Path] = []
    search_query = ""

    def rebuild_entries():
        nonlocal entries, cursor, search_query
        entries = _list_subdirs(current)
        cursor = 0
        search_query = ""

    rebuild_entries()

    def visible_entries() -> List[Path]:
        if not search_query:
            return entries
        return [d for d in entries if _fuzzy_match(search_query, d.name)]

    def build_rows():
        rows = [{"kind": "done"}]
        for p in sorted(selected):
            rows.append({"kind": "selected", "path": Path(p)})
        for d in visible_entries():
            rows.append({"kind": "entry", "path": d})
        return rows

    def clamp_cursor():
        nonlocal cursor
        cursor = max(0, min(cursor, len(build_rows()) - 1))

    def render():
        rows = build_rows()
        lines = [("class:path", f" {current}\n")]
        if search_query:
            lines.append(("class:search", f" /{search_query}\n"))
        else:
            lines.append(("", "\n"))
        lines.append(("", "\n"))

        printed_selected_header = False
        printed_entries_header = False
        for i, row in enumerate(rows):
            is_cursor = i == cursor
            prefix = "❯ " if is_cursor else "  "
            cursor_marker = [("[SetCursorPosition]", "")] if is_cursor else []

            if row["kind"] == "done":
                label = f"{prefix}{_CHECK} Done — {len(selected)} selected"
                style = "class:cursor" if is_cursor else "class:done"
                lines.extend(cursor_marker)
                lines.append((style, label + "\n"))
                continue

            if row["kind"] == "selected" and not printed_selected_header:
                lines.append(("class:section", " — Selected —\n"))
                printed_selected_header = True
            if row["kind"] == "entry" and not printed_entries_header:
                if printed_selected_header:
                    lines.append(("", "\n"))
                lines.append(("class:section", " — Browse —\n"))
                printed_entries_header = True

            d = row["path"]
            is_checked = row["kind"] == "selected" or str(d) in selected
            box = f"[{_CHECK}]" if is_checked else f"[{_EMPTY_BOX}]"
            box_style = "checked" if is_checked else "unchecked"
            lines.extend(cursor_marker)
            if is_cursor:
                lines.append(("class:cursor", f"{prefix}{box} {d.name}/\n"))
            else:
                lines.append((f"class:{box_style}", f"{prefix}{box} "))
                lines.append(("class:folder", d.name + "/\n"))

        if not rows or (len(rows) == 1 and not entries and not search_query):
            lines.append(("class:hint", " (no subfolders here)\n"))
        elif search_query and not visible_entries():
            lines.append(("class:hint", " (no matches)\n"))

        lines.append(("", "\n"))
        lines.append(("class:count", f" {len(selected)} folder(s) selected  "))
        lines.append((
            "class:hint",
            "↑/↓ move · type to search · Space select · "
            "Enter open/done · Backspace/Esc back · Ctrl-C cancel",
        ))
        return lines

    control = FormattedTextControl(render)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("k")
    def _up(event):
        nonlocal cursor
        cursor = (cursor - 1) % len(build_rows())

    @bindings.add("down")
    @bindings.add("j")
    def _down(event):
        nonlocal cursor
        cursor = (cursor + 1) % len(build_rows())

    @bindings.add("space")
    def _toggle(event):
        row = build_rows()[cursor]
        if row["kind"] == "done":
            return
        d = str(row["path"])
        if d in selected:
            selected.discard(d)
        else:
            selected.add(d)
        clamp_cursor()

    @bindings.add("enter")
    def _activate(event):
        nonlocal current
        row = build_rows()[cursor]
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
            clamp_cursor()
        elif current.parent != current:
            current = current.parent
            rebuild_entries()

    @bindings.add("escape")
    def _escape(event):
        nonlocal current, search_query
        if search_query:
            search_query = ""
            clamp_cursor()
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
            clamp_cursor()

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
