"""
Human-readable, per-project audit log of every file/shell action taken.

Written to <project_root>/.claudeaibridge/audit.log — the same hidden
folder already used for file_edit's backups and file_trash's trash, so a
project owner has one place to look for "what did Claude actually do
here." Append-only plain text, one line per call, on both success and
failure — this is deliberately not structured JSON: the point is that a
person can open it and read it, not that another program parses it.

Logging failures never break the actual tool call — a write error here
(e.g. a read-only filesystem) is swallowed rather than raised.
"""

import time
from pathlib import Path


def _log_path(root: str) -> Path:
    d = Path(root) / ".claudeaibridge"
    d.mkdir(parents=True, exist_ok=True)
    return d / "audit.log"


def log(root: str, tool: str, detail: str) -> None:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    line = f"{timestamp}  {tool:<14} {detail}\n"
    try:
        with open(_log_path(root), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
