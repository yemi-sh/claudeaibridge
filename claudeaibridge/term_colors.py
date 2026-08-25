"""
Small ANSI color helpers shared by the CLI and the onboarding wizard.

Colors mirror the palette already used in picker.py/_MENU_STYLE, so plain
print() status lines look like part of the same tool instead of switching
to bare text between the interactive screens and everything around them.
Falls back to plain text whenever stdout isn't a real terminal.
"""

import sys

_BLUE = "97;175;239"
_PURPLE = "198;120;221"
_GREEN = "152;195;121"
_GRAY = "92;99;112"
_RED = "224;108;117"


def _wrap(text: str, rgb: str, *, bold: bool = False) -> str:
    if not sys.stdout.isatty():
        return text
    prefix = "\033[1m" if bold else ""
    return f"{prefix}\033[38;2;{rgb}m{text}\033[0m"


def header(text: str) -> str:
    """Section headers, e.g. '--- Projects ---'."""
    return _wrap(text, _BLUE, bold=True)


def success(text: str) -> str:
    """Confirmations: 'Saved.', 'Registered <path>', etc."""
    return _wrap(text, _GREEN)


def error(text: str) -> str:
    return _wrap(text, _RED)


def hint(text: str) -> str:
    """De-emphasized supporting text."""
    return _wrap(text, _GRAY)


def accent(text: str) -> str:
    """The one thing on screen that should draw the eye, e.g. a URL."""
    return _wrap(text, _PURPLE, bold=True)
