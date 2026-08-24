"""
Interactive folder picker for `claudeaibridge init`.

A single-screen, checkbox-style browser built directly on prompt_toolkit
(questionary's underlying library) rather than composing questionary's
canned prompts — the interaction this needs (mark folders with Space while
also being able to descend with Enter, on the same screen, across
directory levels) doesn't match any of questionary's built-in prompt
shapes.

Controls:
  Up/Down (or j/k)  move the cursor
  Space             toggle the highlighted folder's checkbox
  Enter             descend into the highlighted folder, or activate Done
  Backspace/Esc     go up one level (or finish, at the top)
  Ctrl-C            cancel — returns nothing selected

Falls back to plain repeated path prompts when stdin isn't a real
terminal (piped input, tests, CI) — prompt_toolkit needs a tty to render
a full-screen UI at all.
"""

import sys
from pathlib import Path
from typing import List, Optional

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

_STYLE = Style.from_dict({
    "path": "fg:#61afef bold",
    "hint": "fg:#5c6370 italic",
    "cursor": "reverse",
    "checked": "fg:#98c379 bold",
    "unchecked": "fg:#5c6370",
    "folder": "fg:#e5c07b",
    "done": "fg:#98c379 bold",
    "count": "fg:#c678dd",
})

_DONE = "__done__"


def _list_subdirs(path: Path) -> List[Path]:
    try:
        entries = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except (PermissionError, OSError):
        return []
    return sorted(entries, key=lambda p: p.name.lower())


def pick_folders(start_dir: str) -> List[str]:
    """Full-screen checkbox browser. Returns the selected absolute paths
    (possibly empty, e.g. on Ctrl-C)."""
    current = Path(start_dir).expanduser().resolve()
    selected: set = set()
    cursor = 0
    entries: List[Path] = []  # rebuilt each time `current` changes

    def rebuild_entries():
        nonlocal entries, cursor
        entries = _list_subdirs(current)
        cursor = 0

    rebuild_entries()

    def row_count() -> int:
        return 1 + len(entries)  # Done row + folder rows

    def render():
        lines = [("class:path", f" {current}\n"), ("", "\n")]
        for i in range(row_count()):
            is_cursor = i == cursor
            prefix = "❯ " if is_cursor else "  "
            if i == 0:
                label = f"{prefix}✓ Done — {len(selected)} selected"
                style = "class:cursor" if is_cursor else "class:done"
                lines.append((style, label + "\n"))
            else:
                d = entries[i - 1]
                is_checked = str(d) in selected
                box = "[x]" if is_checked else "[ ]"
                box_style = "checked" if is_checked else "unchecked"
                text = f"{prefix}{box} {d.name}/"
                if is_cursor:
                    lines.append(("class:cursor", text + "\n"))
                else:
                    lines.append((f"class:{box_style}", f"{prefix}{box} "))
                    lines.append(("class:folder", d.name + "/\n"))
        lines.append(("", "\n"))
        lines.append(("class:count", f" {len(selected)} folder(s) selected  "))
        lines.append(("class:hint", "↑/↓ move · Space select · Enter open/done · Backspace/Esc back · Ctrl-C cancel"))
        return lines

    control = FormattedTextControl(render)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("k")
    def _up(event):
        nonlocal cursor
        cursor = (cursor - 1) % row_count()

    @bindings.add("down")
    @bindings.add("j")
    def _down(event):
        nonlocal cursor
        cursor = (cursor + 1) % row_count()

    @bindings.add("space")
    def _toggle(event):
        if cursor == 0:
            return
        d = str(entries[cursor - 1])
        if d in selected:
            selected.discard(d)
        else:
            selected.add(d)

    @bindings.add("enter")
    def _activate(event):
        nonlocal current
        if cursor == 0:
            event.app.exit(result=sorted(selected))
            return
        current = entries[cursor - 1]
        rebuild_entries()

    @bindings.add("backspace")
    @bindings.add("escape")
    def _back(event):
        nonlocal current
        if current.parent != current:
            current = current.parent
            rebuild_entries()
        else:
            event.app.exit(result=sorted(selected))

    @bindings.add("c-c")
    def _cancel(event):
        event.app.exit(result=[])

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
