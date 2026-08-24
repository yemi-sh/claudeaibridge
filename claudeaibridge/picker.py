"""
Interactive folder picker for `claudeaibridge init`.

A key-driven browser: arrow keys (or j/k) move through the current
directory's subfolders, typing filters the list, enter either descends into
a folder or selects it, and you can pick more than one folder before
finishing. Falls back to plain path prompts when stdin isn't a real
terminal (piped input, CI, tests) — questionary/prompt_toolkit needs a tty
to render an interactive menu at all.
"""

import sys
from pathlib import Path
from typing import List

import questionary

_SELECT = "__select__"
_UP = "__up__"
_DONE = "__done__"
_TYPE_PATH = "__type_path__"


def _list_subdirs(path: Path) -> List[Path]:
    try:
        entries = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except (PermissionError, OSError):
        return []
    return sorted(entries, key=lambda p: p.name.lower())


def pick_folders(start_dir: str) -> List[str]:
    """Browse starting at start_dir; return the list of selected absolute
    paths (possibly more than one). Returns [] if the user cancels
    (Ctrl+C) before selecting anything."""
    selected: List[str] = []
    current = Path(start_dir).expanduser().resolve()

    while True:
        subdirs = _list_subdirs(current)

        choices = [questionary.Choice(title=f"✓ Select this folder  ({current})", value=_SELECT)]
        if current.parent != current:
            choices.append(questionary.Choice(title=".. (up one level)", value=_UP))
        for d in subdirs:
            choices.append(questionary.Choice(title=f"{d.name}/", value=str(d)))
        choices.append(questionary.Choice(title="⌨  Type a path instead", value=_TYPE_PATH))
        if selected:
            choices.append(questionary.Choice(title=f"— Done ({len(selected)} selected) —", value=_DONE))

        answer = questionary.select(
            f"{current}\n  (type to filter, ↑/↓ to move, enter to choose)",
            choices=choices,
            use_search_filter=True,
            use_jk_keys=False,
        ).ask()

        if answer is None:
            break

        if answer == _SELECT:
            selected.append(str(current))
            if not questionary.confirm(f"Added {current}. Pick another folder?", default=False).ask():
                break
        elif answer == _UP:
            current = current.parent
        elif answer == _TYPE_PATH:
            typed = questionary.path("Path:", only_directories=True).ask()
            if typed:
                candidate = Path(typed).expanduser().resolve()
                if candidate.is_dir():
                    current = candidate
        elif answer == _DONE:
            break
        else:
            current = Path(answer)

    return selected


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
