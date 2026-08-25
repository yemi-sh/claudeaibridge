"""
File tools, all confined to the active project's folder.

Ported from a personal-use tool collection and adapted for a public,
multi-user server: every path argument is resolved and checked with
paths.resolve_within() before anything touches disk — this is the boundary
that keeps "edit files in my project" from becoming "edit any file this OS
account can reach." Backups (file_edit) and trash (file_trash) are kept
inside the project's own .claudeaibridge/ folder rather than system /tmp, so
they stay within the same containment boundary and don't collide across
projects or users on a shared machine.
"""

import ast
import shutil
import time
from datetime import datetime
from difflib import SequenceMatcher, unified_diff
from pathlib import Path
from typing import Annotated, Any, List, Optional, Tuple

from fastmcp import Context
from fastmcp.apps.config import PrefabAppConfig
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import Field

from prefab_ui import PrefabApp
from prefab_ui.actions import ToggleState
from prefab_ui.components import Button, Column, Text
from prefab_ui.components.control_flow import Else, If

from . import audit, session
from .paths import PathEscapesProject, resolve_within


def _resolve_or_raise(root: str, path: str) -> Path:
    try:
        return resolve_within(root, path)
    except PathEscapesProject as e:
        raise ToolError(str(e))


def _backups_dir(root: str) -> Path:
    d = Path(root) / ".claudeaibridge" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trash_dir(root: str) -> Path:
    d = Path(root) / ".claudeaibridge" / "trash"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generate_diff(old_content: str, new_content: str, filename: str = "") -> str:
    old_lines = old_content.splitlines(keepends=True) if old_content else []
    new_lines = new_content.splitlines(keepends=True) if new_content else []
    diff = unified_diff(old_lines, new_lines, fromfile=f"a/{filename}", tofile=f"b/{filename}", lineterm="\n")
    return "".join(diff)


# --------------------------------------------------------------------------
# file_edit matching helpers
# --------------------------------------------------------------------------

def _find_matches(content: str, search_str: str, normalize: bool) -> List[Tuple[int, int]]:
    matches = []
    needle = search_str.strip() if normalize else search_str
    start = 0
    while True:
        pos = content.find(needle, start)
        if pos == -1:
            break
        end = pos + len(needle)
        matches.append((pos, end))
        start = end
    return matches


def _find_similar_matches(content: str, search_str: str, threshold: float) -> List[dict]:
    search_lines = search_str.strip().split("\n")
    search_line_count = len(search_lines)
    content_lines = content.split("\n")
    similar = []
    for i in range(max(0, len(content_lines) - search_line_count + 1)):
        window = "\n".join(content_lines[i:i + search_line_count])
        similarity = SequenceMatcher(None, search_str.strip(), window.strip()).ratio()
        if similarity >= threshold:
            similar.append({
                "similarity": round(similarity, 2),
                "start_line": i + 1,
                "end_line": i + search_line_count,
                "content": window,
            })
    similar.sort(key=lambda x: x["similarity"], reverse=True)
    return similar[:5]


def _replace_content(content: str, new: str, match: Tuple[int, int], normalize: bool) -> str:
    start, end = match
    if normalize:
        original_match = content[start:end]
        leading_ws = ""
        for ch in original_match:
            if ch in " \t":
                leading_ws += ch
            else:
                break
        trailing_ws = ""
        for ch in reversed(original_match):
            if ch in " \t\n":
                trailing_ws = ch + trailing_ws
            else:
                break
        replacement = leading_ws + new.strip() + trailing_ws
    else:
        replacement = new
    return content[:start] + replacement + content[end:]


def _get_line_numbers(content: str, match: Tuple[int, int]) -> dict:
    start, end = match
    return {"start": content[:start].count("\n") + 1, "end": content[:end].count("\n") + 1}


def _parse_occurrence(occurrence: Any, total_matches: int) -> Optional[List[int]]:
    if occurrence is None:
        return None if total_matches > 1 else [1]
    if isinstance(occurrence, str):
        if occurrence.lower() == "all":
            return list(range(1, total_matches + 1))
        try:
            return [int(occurrence)]
        except ValueError:
            try:
                parsed = ast.literal_eval(occurrence)
                if isinstance(parsed, list):
                    return [int(v) for v in parsed]
            except (ValueError, SyntaxError):
                pass
            return None
    if isinstance(occurrence, int):
        return [occurrence]
    if isinstance(occurrence, list):
        try:
            return [int(v) for v in occurrence]
        except (ValueError, TypeError):
            return None
    return None


def _write_backup(root: str, file_path: Path, content: str, reason: str) -> dict:
    timestamp = str(int(datetime.now().timestamp() * 1000))
    backup_path = _backups_dir(root) / f"{file_path.name}.{timestamp}"
    backup_path.write_text(content, encoding="utf-8")
    return {"backup_path": str(backup_path), "backup_id": timestamp, "backup_reason": reason}


def _diff_line_style(line: str) -> str:
    # Code's bundled syntax highlighter doesn't know the "diff" language, so
    # unified-diff text comes out monochrome — color each line by hand instead.
    if line.startswith("+++") or line.startswith("---"):
        return "text-muted-foreground"
    if line.startswith("@@"):
        return "text-sky-500"
    if line.startswith("+"):
        return "text-green-500 bg-green-500/10"
    if line.startswith("-"):
        return "text-red-500 bg-red-500/10"
    return "text-muted-foreground"


# Zeroes out the renderer's default 1.5rem padding around the whole widget
# (".pf-app-root { padding: calc(var(--spacing) * 6) }").
_APP_CSS_CLASS = "p-0"


# Accordion was tried for collapsibility, but its bundled open/close
# animation leaves a large stale empty area below default-open content (a
# height-measurement bug in prefab_ui's renderer, not something fixable via
# css_class) -- toggle visibility by hand via state instead, which has no
# animation to get wrong. Both views share the same "expanded" state key and
# default to expanded (open by default, collapsible on click), per request.
_TOGGLE_CSS_CLASS = "p-0 h-auto justify-start font-mono font-bold text-sm"


def _toggle_button(title: str):
    with If("expanded"):
        Button(f"▾ {title}", variant="ghost", on_click=ToggleState("expanded"), css_class=_TOGGLE_CSS_CLASS)
    with Else():
        Button(f"▸ {title}", variant="ghost", on_click=ToggleState("expanded"), css_class=_TOGGLE_CSS_CLASS)


def _diff_view(title: str, diff_text: str) -> PrefabApp:
    with Column(gap=4) as view:
        _toggle_button(title)
        with If("expanded"):
            with Column(
                gap=0,
                css_class="font-mono text-xs leading-5 whitespace-pre overflow-x-auto "
                          "max-h-56 overflow-y-auto rounded-md border border-border p-2",
            ):
                for line in diff_text.splitlines():
                    Text(line or " ", css_class=_diff_line_style(line))
    return PrefabApp(view=view, css_class=_APP_CSS_CLASS, state={"expanded": True})


def _message_view(title: str, message: str) -> PrefabApp:
    with Column(gap=4) as view:
        _toggle_button(title)
        with If("expanded"):
            Text(message, css_class="text-sm text-muted-foreground")
    return PrefabApp(view=view, css_class=_APP_CSS_CLASS, state={"expanded": True})


def register(mcp):
    @mcp.tool(
        name="file_write",
        app=PrefabAppConfig(prefers_border=False),
        annotations={
            "title": "Write File",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def file_write(
        path: Annotated[str, Field(description="Path within the active project, relative or absolute.")],
        content: Annotated[str, Field(description="Complete file content to write.")],
        reason: Annotated[str, Field(description="Why this file is being created/overwritten.")],
        ctx: Context,
    ) -> ToolResult:
        """Create a new file or completely overwrite an existing one. For small
        edits to an existing file, prefer file_edit — it keeps a backup. On
        hosts that support it, the change is also shown as a rendered diff."""
        root = await session.get_active_project(ctx)
        file_path = _resolve_or_raise(root, path)

        was_overwrite = file_path.exists() and file_path.is_file()
        old_content: Optional[str] = None
        if was_overwrite:
            try:
                old_content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                pass  # binary/non-UTF-8 file being overwritten — no diff to show
        file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            raise ToolError("Content contains characters that cannot be encoded as UTF-8.")

        file_path.write_text(content, encoding="utf-8")
        operation = "overwrite" if was_overwrite else "create"
        audit.log(root, "file_write", f"{operation} {file_path} — {reason}")
        result = {
            "success": True,
            "path": str(file_path),
            "reason": reason,
            "operation": operation,
            "lines_written": len(content.split("\n")),
            "chars_written": len(content),
        }
        if old_content is None and was_overwrite:
            return ToolResult(content=result)
        diff = _generate_diff(old_content or "", content, file_path.name)
        result["diff"] = diff
        return ToolResult(content=result, structured_content=_diff_view(file_path.name, diff))

    @mcp.tool(
        name="file_edit",
        app=PrefabAppConfig(prefers_border=False),
        annotations={
            "title": "Edit File",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def file_edit(
        path: Annotated[str, Field(description="Path within the active project, relative or absolute.")],
        reason: Annotated[str, Field(description="Why this edit is being made.")],
        ctx: Context,
        old_content: Annotated[Optional[str], Field(description="Content to find (single-replacement mode). Include 2-5 lines of context for uniqueness.")] = None,
        new_content: Annotated[Optional[str], Field(description="Replacement content (single-replacement mode).")] = None,
        occurrence: Annotated[Optional[Any], Field(description="Which match to replace: a number, a list of numbers, or 'all'. Required if old_content matches more than once.")] = None,
        replacements: Annotated[Optional[List[dict]], Field(description="Multiple {old_content, new_content, occurrence} replacements in one call, as an alternative to old_content/new_content.")] = None,
        normalize_whitespace: Annotated[bool, Field(description="Ignore leading/trailing whitespace when matching.")] = True,
    ) -> ToolResult:
        """Search-and-replace edit of an existing file. A backup of the file's
        prior content is written under .claudeaibridge/backups/ before any
        change is applied, so an edit can always be undone by hand. On hosts
        that support it, the change is also shown as a rendered diff."""
        if replacements is None and (old_content is None or new_content is None):
            raise ToolError("Provide either old_content+new_content, or a replacements list.")
        if replacements is not None and (old_content is not None or new_content is not None):
            raise ToolError("Use either old_content/new_content or replacements, not both.")

        root = await session.get_active_project(ctx)
        file_path = _resolve_or_raise(root, path)
        if not file_path.exists() or not file_path.is_file():
            raise ToolError(f"File not found: {path}")

        try:
            current_content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"File is not UTF-8 text: {path}")

        if replacements is not None:
            result = _apply_multiple(file_path, current_content, replacements, normalize_whitespace)
        else:
            result = _apply_single(file_path, current_content, old_content, new_content, occurrence, normalize_whitespace)

        if result.get("success") and result.get("_new_content") is not None:
            new_content_written = result.pop("_new_content")
            file_path.write_text(new_content_written, encoding="utf-8")
            backup = _write_backup(root, file_path, current_content, reason)
            result["reason"] = reason
            result.update(backup)
            diff = _generate_diff(current_content, new_content_written, file_path.name)
            result["diff"] = diff
            audit.log(root, "file_edit", f"edit {file_path} — {reason}")
            return ToolResult(content=result, structured_content=_diff_view(file_path.name, diff))
        else:
            result.pop("_new_content", None)
            error = result.get("error", "unknown error")
            audit.log(root, "file_edit", f"FAILED {file_path} — {error}")
            return ToolResult(content=result, structured_content=_message_view(file_path.name, error))

    @mcp.tool(
        name="file_trash",
        annotations={
            "title": "Move Files to Trash",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def file_trash(
        paths: Annotated[List[str], Field(description="Paths within the active project to move to trash.")],
        reason: Annotated[str, Field(description="Why these are being removed.")],
        ctx: Context,
    ) -> dict:
        """Move files/directories to .claudeaibridge/trash/ inside the project
        instead of deleting them — a safer alternative to permanent deletion."""
        root = await session.get_active_project(ctx)
        trash_dir = _trash_dir(root)
        results = []
        for p in paths:
            try:
                src = _resolve_or_raise(root, p)
            except ToolError as e:
                results.append({"path": p, "status": "error", "message": str(e)})
                continue
            if not src.exists():
                results.append({"path": str(src), "status": "error", "message": "Path does not exist."})
                continue

            dest = trash_dir / src.name
            counter = 1
            while dest.exists():
                dest = trash_dir / f"{src.name}.{counter}"
                counter += 1
            try:
                shutil.move(str(src), str(dest))
                results.append({"path": str(src), "status": "success", "moved_to": str(dest)})
            except Exception as e:
                results.append({"path": str(src), "status": "error", "message": str(e)})

        success_count = sum(1 for r in results if r["status"] == "success")
        moved = [r["path"] for r in results if r["status"] == "success"]
        if moved:
            audit.log(root, "file_trash", f"{', '.join(moved)} — {reason}")
        return {
            "success": success_count == len(paths),
            "reason": reason,
            "successful_moves": success_count,
            "failed_moves": len(paths) - success_count,
            "results": results,
        }

    @mcp.tool(
        name="file_enum",
        annotations={
            "title": "List Directory",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def file_enum(
        ctx: Context,
        path: Annotated[str, Field(description="Directory within the active project. Defaults to the project root.")] = ".",
    ) -> dict:
        """List a directory's contents (like ls -la), with a line count for
        files and an entry count for subdirectories."""
        root = await session.get_active_project(ctx)
        dir_path = _resolve_or_raise(root, path)
        if not dir_path.exists() or not dir_path.is_dir():
            raise ToolError(f"Not a directory: {path}")

        items = []
        for item in sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            is_dir = item.is_dir()
            if is_dir:
                try:
                    n = sum(1 for _ in item.iterdir())
                    count = f"{n} entries"
                except OSError:
                    count = "access denied"
            else:
                try:
                    with open(item, "r", encoding="utf-8", errors="ignore") as f:
                        count = str(sum(1 for _ in f))
                except OSError:
                    count = "unreadable"
            items.append({"name": item.name, "is_dir": is_dir, "count": count})

        return {"path": str(dir_path), "item_count": len(items), "items": items}


def _apply_single(file_path: Path, current_content: str, old_content: str, new_content: str,
                   occurrence: Any, normalize: bool) -> dict:
    matches = _find_matches(current_content, old_content, normalize)
    if not matches:
        return {
            "success": False,
            "error": "No exact match found for old_content.",
            "similar_matches": _find_similar_matches(current_content, old_content, threshold=0.6),
        }

    occurrences = _parse_occurrence(occurrence, len(matches))
    if occurrences is None:
        return {
            "success": False,
            "error": f"Found {len(matches)} matches; specify which via 'occurrence'.",
            "matches": [
                {"occurrence": i + 1, "location": _get_line_numbers(current_content, m)}
                for i, m in enumerate(matches)
            ],
        }
    invalid = [o for o in occurrences if o < 1 or o > len(matches)]
    if invalid:
        return {"success": False, "error": f"Invalid occurrence(s) {invalid}; found {len(matches)} match(es)."}

    new_full = current_content
    for occ_num in sorted(occurrences, reverse=True):
        new_full = _replace_content(new_full, new_content, matches[occ_num - 1], normalize)

    return {
        "success": True,
        "path": str(file_path),
        "occurrences_replaced": occurrences,
        "total_occurrences": len(matches),
        "_new_content": new_full,
    }


def _apply_multiple(file_path: Path, current_content: str, replacements: List[dict], normalize: bool) -> dict:
    new_content = current_content
    changes = []
    issues = []
    for idx, r in enumerate(replacements):
        old, new = r.get("old_content"), r.get("new_content")
        if not old or new is None:
            issues.append({"replacement_index": idx + 1, "issue": "missing old_content/new_content"})
            continue
        matches = _find_matches(new_content, old, normalize)
        if not matches:
            issues.append({"replacement_index": idx + 1, "issue": "no match found", "old_content": old})
            continue
        occurrences = _parse_occurrence(r.get("occurrence"), len(matches))
        if occurrences is None:
            issues.append({"replacement_index": idx + 1, "issue": f"{len(matches)} matches, occurrence required"})
            continue
        invalid = [o for o in occurrences if o < 1 or o > len(matches)]
        if invalid:
            issues.append({"replacement_index": idx + 1, "issue": f"invalid occurrence(s) {invalid}"})
            continue
        for occ_num in sorted(occurrences, reverse=True):
            new_content = _replace_content(new_content, new, matches[occ_num - 1], normalize)
            changes.append({"replacement_index": idx + 1, "occurrence": occ_num})

    if not changes:
        return {"success": False, "error": "All replacements failed.", "issues": issues}

    return {
        "success": True,
        "path": str(file_path),
        "partial": bool(issues),
        "total_changes": len(changes),
        "changes": changes,
        "issues": issues,
        "_new_content": new_content,
    }
