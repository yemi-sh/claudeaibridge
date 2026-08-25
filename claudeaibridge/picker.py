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
  Up/Down    move the cursor (stops at the top/bottom, no wrap)
  Space      toggle the highlighted folder's checkbox
  Enter      descend into the highlighted folder, or activate Done
  type       fuzzy-filter the current folder's subfolders (any letter,
             including j/k — those aren't bound to navigation here)
  Backspace  erase search text (only — never navigates)
  Esc        clear search text, or (when no search) go up one level
             (finishes, if already at the top)
  Ctrl-C     cancel — reverts to whatever was pre-checked at the start
             (nothing, if this was a fresh selection)

Falls back to plain repeated path prompts when stdin isn't a real
terminal (piped input, tests, CI) — prompt_toolkit needs a tty to render
a full-screen UI at all.
"""

import sys
from pathlib import Path
from typing import Iterable, List

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style

_STYLE = Style.from_dict({
    "path": "fg:#61afef bold",
    # Named "searchquery" rather than "search" -- "search" is a style name
    # prompt_toolkit reserves for its own built-in incremental-search
    # highlighting (defaults to a yellow background), so reusing it here
    # just layered our foreground color on top of that inherited yellow
    # block instead of replacing it.
    "searchquery": "fg:#c678dd",
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
_COMPACT_SELECTED_VISIBLE = 3


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


def pick_folders(start_dir: str, initial_selected: Iterable[str] = (), instruction: str = "") -> List[str]:
    """Three-pane checkbox browser. Returns the selected absolute paths.
    `initial_selected` pre-checks paths (e.g. already-registered projects)
    so they show up pinned and checked from the start — used for editing an
    existing set rather than building one from scratch. Ctrl-C aborts with
    no changes: it returns exactly `initial_selected` back, not an empty
    list — otherwise cancelling out of an edit session would look like
    'remove everything that was pre-checked'. `instruction`, if given, is
    pinned at the very top of the Browse pane — this runs full-screen (an
    alternate screen buffer), so anything printed before calling this is
    invisible the moment it opens; text that needs to stay visible has to
    be part of the picker's own layout, not a print() beforehand."""
    current = Path(start_dir).expanduser().resolve()
    starting_selection = list(initial_selected)
    selected: set = set(starting_selection)
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
        search_query = ""
        # Land in the Browse section, not back up at Done/Selected — descending
        # into a folder should keep you where you're looking, not jump you
        # away from what you're browsing.
        cursor_key = ("entry", str(entries[0])) if entries else ("done",)

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

    def cursor_in_top() -> bool:
        return cursor_key[0] in ("done", "selected")

    def render_top():
        rows = top_rows()
        done_row, selected_rows = rows[0], rows[1:]

        # Compact by default (a fixed number of rows, so a big selection
        # can't shrink the Browse pane) — expands to show the whole list
        # once the cursor actually moves into this section.
        if not cursor_in_top() and len(selected_rows) > _COMPACT_SELECTED_VISIBLE:
            visible = selected_rows[:_COMPACT_SELECTED_VISIBLE]
            hidden_count = len(selected_rows) - _COMPACT_SELECTED_VISIBLE
        else:
            visible = selected_rows
            hidden_count = 0

        lines = list(render_row(done_row, full_path=True))
        if visible:
            lines.append(("class:section", " — Selected —\n"))
            for row in visible:
                lines.extend(render_row(row, full_path=True))
            if hidden_count:
                lines.append(("class:hint", f"  ...[+{hidden_count} more]\n"))
        return lines

    def render_browse_header():
        lines = []
        if instruction:
            lines.append(("class:section", f" {instruction}\n"))
        lines.append(("class:path", f" {current}\n"))
        if search_query:
            lines.append(("class:searchquery", f" /{search_query}\n"))
        return lines

    def render_browse_list():
        rows = browse_rows()
        lines = [("class:section", " — Browse —\n")]
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
                "↑/↓ move · type to search · Backspace erase · Space select · "
                "Enter open/done · Esc back · Ctrl-C cancel",
            ),
        ]

    top_control = FormattedTextControl(render_top)
    browse_header_control = FormattedTextControl(render_browse_header)
    browse_list_control = FormattedTextControl(render_browse_list)
    footer_control = FormattedTextControl(render_footer)

    top_window = Window(content=top_control, height=Dimension(max=_TOP_MAX_HEIGHT), always_hide_cursor=True)
    browse_header_window = Window(content=browse_header_control, height=Dimension(min=1, max=3), always_hide_cursor=True)
    browse_list_window = Window(content=browse_list_control, height=Dimension(weight=1), always_hide_cursor=True)
    footer_window = Window(content=footer_control, height=1, always_hide_cursor=True)

    layout = Layout(HSplit([
        top_window,
        Window(height=1, char="─", style="class:sep"),
        browse_header_window,
        browse_list_window,
        Window(height=1, char="─", style="class:sep"),
        footer_window,
    ]))

    def sync_focus():
        # Explicitly park focus (and so the terminal's real cursor) on
        # whichever pane actually holds the cursor — otherwise
        # prompt_toolkit's default-focused window ends up being the first
        # one in the layout with nothing claiming the cursor position,
        # which renders as a stray cursor block sitting on the search field.
        layout.focus(top_window if cursor_in_top() else browse_list_window)

    sync_focus()

    def cursor_valid(rows) -> bool:
        return any(row_key(r) == cursor_key for r in rows)

    def reconcile_cursor(pre_idx: int) -> None:
        """Call after any mutation that might remove the row the cursor was
        on (deselecting from the Selected pane, or search filtering it out
        of view) — otherwise cursor_key points at a row that no longer
        exists, nothing claims the terminal's cursor position, and it
        visually vanishes until the next Up/Down press recomputes it."""
        rows = all_rows()
        if rows and not cursor_valid(rows):
            set_cursor_to_index(rows, pre_idx)
        sync_focus()

    bindings = KeyBindings()

    @bindings.add("up")
    def _up(event):
        rows = all_rows()
        set_cursor_to_index(rows, cursor_index(rows) - 1)
        sync_focus()

    @bindings.add("down")
    def _down(event):
        rows = all_rows()
        set_cursor_to_index(rows, cursor_index(rows) + 1)
        sync_focus()

    @bindings.add("space")
    def _toggle(event):
        rows = all_rows()
        idx = cursor_index(rows)
        row = rows[idx]
        if row["kind"] == "done":
            return
        d = str(row["path"])
        if d in selected:
            selected.discard(d)
        else:
            selected.add(d)
        reconcile_cursor(idx)

    @bindings.add("enter")
    def _activate(event):
        nonlocal current
        row = all_rows()[cursor_index(all_rows())]
        if row["kind"] == "done":
            event.app.exit(result=sorted(selected))
            return
        current = row["path"]
        rebuild_entries()
        sync_focus()

    @bindings.add("backspace")
    def _backspace(event):
        nonlocal search_query
        # Strictly search-text editing — going up a level is Esc's job only.
        if search_query:
            idx = cursor_index(all_rows())
            search_query = search_query[:-1]
            reconcile_cursor(idx)

    @bindings.add("escape")
    def _escape(event):
        nonlocal current, search_query
        if search_query:
            idx = cursor_index(all_rows())
            search_query = ""
            reconcile_cursor(idx)
        elif current.parent != current:
            current = current.parent
            rebuild_entries()
            sync_focus()
        else:
            event.app.exit(result=sorted(selected))

    @bindings.add("c-c")
    def _cancel(event):
        event.app.exit(result=starting_selection)

    @bindings.add(Keys.Any)
    def _search_type(event):
        nonlocal search_query
        if event.data and event.data.isprintable():
            idx = cursor_index(all_rows())
            search_query += event.data
            reconcile_cursor(idx)

    app = Application(layout=layout, key_bindings=bindings, style=_STYLE, full_screen=True)
    result = app.run()
    return result or []


def prompt_for_projects(start_dir: str, instruction: str = "") -> List[str]:
    """Entry point used by onboarding: the interactive picker on a real
    terminal, or a plain repeated-path-prompt fallback otherwise."""
    if not sys.stdin.isatty():
        return _fallback_prompt(instruction)
    return pick_folders(start_dir, instruction=instruction)


def _fallback_prompt(instruction: str = "") -> List[str]:
    if instruction:
        print(instruction)
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
