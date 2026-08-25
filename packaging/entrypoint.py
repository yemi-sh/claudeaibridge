"""
PyInstaller entry point.

Pointing PyInstaller directly at claudeaibridge/cli.py makes it treat that
file as a standalone top-level script rather than part of the
claudeaibridge package, which breaks every `from . import X` relative
import in the codebase. This tiny wrapper imports the package properly
instead, so PyInstaller bundles and runs it the same way `python -m
claudeaibridge.cli` or the installed console-script entry point would.
"""

from claudeaibridge.cli import main

if __name__ == "__main__":
    main()
