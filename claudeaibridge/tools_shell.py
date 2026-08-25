"""
Shell execution tool, scoped to the active project's folder.

Ported from a standalone shell-agent MCP server, adapted so the sandboxed
writable root is resolved per call (the active project) instead of fixed at
process startup — the active project can differ between calls and between
concurrent sessions.

Behavior preserved from the original:
  - One-shot: no state carries between commands.
  - Fail-fast on interactive prompts via /proc inspection (a process blocked
    reading stdin, which we never feed, is waiting for input nobody will
    give) rather than hanging until the timeout.
  - A command that outruns its wait window is backgrounded, not killed —
    control returns to the caller and the job can be checked/stopped later.
"""

import os
import time
import uuid
import signal
import struct
import atexit
import asyncio
import tempfile
import shutil
from dataclasses import dataclass
from typing import Annotated, Literal, Optional

from fastmcp import Context
from pydantic import Field

from . import audit
from . import sandbox
from . import session

MAX_TIMEOUT_SECONDS = 300
POLL_INTERVAL = 0.05
PROMPT_CONFIRM_CYCLES = 2
MAX_BACKGROUND_JOBS = 16
MAX_JOB_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB
SEND_SETTLE_SECONDS = 15
IDLE_TIMEOUT_SECONDS = 120

_PREFER_NONINTERACTIVE = (
    "This tool is built for one-shot commands; driving a program interactively "
    "is a best-effort fallback, not what it was designed for. First, prefer to "
    "re-run the command non-interactively if it has any way to avoid the prompt "
    "— pipe the input in (e.g. 'echo y | <command>' or 'yes | <command>'), use a "
    "non-interactive flag (e.g. '--yes'), set a config/env var, or redirect stdin "
    "from a file ('<command> < input.txt'). Only drive it interactively when the "
    "program genuinely has no non-interactive option."
)

JOB_TMP_DIR = tempfile.mkdtemp(prefix="claudeaibridge-jobs-")

_READ_SYSCALLS = {0, 17, 19, 295, 327}
_POLL_SYSCALLS = {7, 271}
_SELECT_SYSCALLS = {23, 270}
_EPOLL_SYSCALLS = {232, 281, 441}
_POLLFD_SIZE = 8
_POLLIN = 0x0001
_MAX_WAIT_FDS = 4096


def _read_syscall(pid: int):
    try:
        with open(f"/proc/{pid}/syscall") as f:
            content = f.read().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    if not content or content == "running":
        return None
    parts = content.split()
    try:
        num = int(parts[0])
    except (ValueError, IndexError):
        return None
    if num < 0:
        return None
    args: list = []
    for token in parts[1:7]:
        try:
            args.append(int(token, 16))
        except ValueError:
            args.append(None)
    return num, args


def _read_mem(pid: int, addr: int, size: int):
    if not addr or size <= 0:
        return None
    try:
        with open(f"/proc/{pid}/mem", "rb", buffering=0) as mem:
            mem.seek(addr)
            data = mem.read(size)
        return data if len(data) == size else None
    except (OSError, ValueError, OverflowError):
        return None


def _poll_watches_stdin(pid: int, args: list):
    addr = args[0] if len(args) > 0 else None
    nfds = args[1] if len(args) > 1 else None
    if not addr or nfds is None or nfds <= 0 or nfds > _MAX_WAIT_FDS:
        return None
    raw = _read_mem(pid, addr, nfds * _POLLFD_SIZE)
    if raw is None:
        return None
    for i in range(nfds):
        fd, events = struct.unpack_from("=ih", raw, i * _POLLFD_SIZE)
        if fd == 0 and (events & _POLLIN):
            return True
    return False


def _select_watches_stdin(pid: int, args: list):
    nfds = args[0] if len(args) > 0 else None
    readfds_addr = args[1] if len(args) > 1 else None
    if nfds is None or nfds <= 0:
        return None
    if not readfds_addr:
        return False
    raw = _read_mem(pid, readfds_addr, 1)
    if raw is None:
        return None
    return bool(raw[0] & 0x01)


def _epoll_watches_stdin(pid: int, args: list):
    epfd = args[0] if len(args) > 0 else None
    if epfd is None or epfd < 0:
        return None
    saw_targets = False
    try:
        with open(f"/proc/{pid}/fdinfo/{epfd}") as f:
            for line in f:
                if line.startswith("tfd:"):
                    saw_targets = True
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            if int(parts[1]) == 0:
                                return True
                        except ValueError:
                            pass
    except OSError:
        return None
    return False if saw_targets else None


def _fd_target(pid: int, fd: int):
    try:
        return os.readlink(f"/proc/{pid}/fd/{fd}")
    except OSError:
        return None


def _is_blocked_on_stdin(pid: int, stdin_id) -> bool:
    if stdin_id is None:
        return False
    info = _read_syscall(pid)
    if info is None:
        return False
    num, args = info
    if num in _READ_SYSCALLS:
        on_fd0 = bool(args) and args[0] == 0
    elif num in _POLL_SYSCALLS:
        on_fd0 = _poll_watches_stdin(pid, args) is True
    elif num in _SELECT_SYSCALLS:
        on_fd0 = _select_watches_stdin(pid, args) is True
    elif num in _EPOLL_SYSCALLS:
        on_fd0 = _epoll_watches_stdin(pid, args) is True
    else:
        return False
    return on_fd0 and _fd_target(pid, 0) == stdin_id


def _descendant_pids(root_pid: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            with open(f"/proc/{pid}/stat") as f:
                raw = f.read()
            close = raw.rindex(")")
            ppid = int(raw[close + 1:].split()[1])
        except (FileNotFoundError, ProcessLookupError, ValueError, IndexError, OSError):
            continue
        children.setdefault(ppid, []).append(pid)
    result: list[int] = []
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        p = stack.pop()
        for c in children.get(p, []):
            if c not in seen:
                seen.add(c)
                result.append(c)
                stack.append(c)
    return result


def _tree_blocked_on_stdin(root_pid: int, stdin_id) -> bool:
    if _is_blocked_on_stdin(root_pid, stdin_id):
        return True
    return any(_is_blocked_on_stdin(p, stdin_id) for p in _descendant_pids(root_pid))


def _signal_group(proc, sig: int) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def _open_capture_files():
    fd_out, path_out = tempfile.mkstemp(prefix="cmd-", suffix=".out", dir=JOB_TMP_DIR)
    fd_err, path_err = tempfile.mkstemp(prefix="cmd-", suffix=".err", dir=JOB_TMP_DIR)
    return path_out, path_err, os.fdopen(fd_out, "wb"), os.fdopen(fd_err, "wb")


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _cleanup_files(*paths: str) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


@dataclass
class Job:
    id: str
    command: str
    root: str
    sandboxed: bool
    process: object
    wait_task: object
    stdout_path: str
    stderr_path: str
    start_time: float
    stdin_id: Optional[str] = None
    status: str = "running"
    live_state: str = "running"
    awaiting_since: Optional[float] = None
    exit_code: Optional[int] = None
    end_time: Optional[float] = None
    monitor_task: object = None


JOBS: dict[str, Job] = {}


def _output_size(job: Job) -> int:
    total = 0
    for p in (job.stdout_path, job.stderr_path):
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


async def _monitor_job(job: Job) -> None:
    while not job.wait_task.done():
        done, _ = await asyncio.wait({job.wait_task}, timeout=0.5)
        if done or job.status != "running":
            continue
        if _output_size(job) > MAX_JOB_OUTPUT_BYTES:
            job.status = "killed_output_limit"
            _signal_group(job.process, signal.SIGKILL)
            continue
        if _tree_blocked_on_stdin(job.process.pid, job.stdin_id):
            job.live_state = "awaiting_input"
            now = time.monotonic()
            if job.awaiting_since is None:
                job.awaiting_since = now
            elif now - job.awaiting_since > IDLE_TIMEOUT_SECONDS:
                job.status = "killed_idle"
                _signal_group(job.process, signal.SIGKILL)
                continue
        else:
            job.live_state = "running"
            job.awaiting_since = None

    if job.status == "running":
        job.status = "exited"
    if job.exit_code is None:
        job.exit_code = job.process.returncode
    if job.end_time is None:
        job.end_time = time.monotonic()
    _close_stdin(job.process)


def _prune_jobs() -> None:
    if len(JOBS) <= MAX_BACKGROUND_JOBS:
        return
    finished = sorted(
        (j for j in JOBS.values() if j.status != "running"),
        key=lambda j: j.end_time or 0.0,
    )
    for job in finished:
        if len(JOBS) <= MAX_BACKGROUND_JOBS:
            break
        JOBS.pop(job.id, None)
        _cleanup_files(job.stdout_path, job.stderr_path)


def _register_job(command, root, sandboxed, proc, wait_task, path_out, path_err, start,
                   stdin_id=None, awaiting: bool = False) -> Job:
    job = Job(
        id=uuid.uuid4().hex[:8],
        command=command,
        root=root,
        sandboxed=sandboxed,
        process=proc,
        wait_task=wait_task,
        stdout_path=path_out,
        stderr_path=path_err,
        start_time=start,
        stdin_id=stdin_id,
    )
    if awaiting:
        job.live_state = "awaiting_input"
        job.awaiting_since = time.monotonic()
    JOBS[job.id] = job
    job.monitor_task = asyncio.ensure_future(_monitor_job(job))
    _prune_jobs()
    return job


def _awaiting_hint(job_id: str) -> str:
    return (
        _PREFER_NONINTERACTIVE
        + f" If it truly needs interaction, send input with "
        f"jobs(action='send', job_id='{job_id}', input='your reply\\n') — include "
        f"a trailing newline to submit a line — then read the new output; or stop "
        f"it with jobs(action='kill', job_id='{job_id}'). It is auto-stopped "
        f"after {IDLE_TIMEOUT_SECONDS}s with no input. Caveat: programs that need "
        f"a real terminal (password prompts read from /dev/tty, full-screen TUIs) "
        f"cannot be driven this way, and a program that buffers its prompt may "
        f"show no prompt text even though it is waiting."
    )


def _reported_status(job: Job) -> str:
    return job.live_state if job.status == "running" else job.status


def _job_summary(job: Job) -> dict:
    now = time.monotonic()
    cmd = job.command
    return {
        "job_id": job.id,
        "command": cmd[:80] + "..." if len(cmd) > 80 else cmd,
        "status": _reported_status(job),
        "running": job.status == "running",
        "age_seconds": round((job.end_time or now) - job.start_time, 3),
        "exit_code": job.exit_code,
    }


def _job_detail(job: Job) -> dict:
    now = time.monotonic()
    status = _reported_status(job)
    detail = {
        "job_id": job.id,
        "command": job.command,
        "root": job.root,
        "sandboxed": job.sandboxed,
        "status": status,
        "running": job.status == "running",
        "stdout": _read_text(job.stdout_path),
        "stderr": _read_text(job.stderr_path),
        "exit_code": job.exit_code,
        "execution_time_seconds": round((job.end_time or now) - job.start_time, 3),
    }
    if status == "awaiting_input":
        detail["hint"] = _awaiting_hint(job.id)
    elif status == "killed_idle":
        detail["message"] = (
            "This job was stopped automatically: it sat waiting for input for "
            f"over {IDLE_TIMEOUT_SECONDS}s with no input sent, so it was reaped."
        )
    elif status == "killed_output_limit":
        detail["message"] = (
            "This job was stopped automatically: it exceeded the background "
            f"output limit ({MAX_JOB_OUTPUT_BYTES // (1024 * 1024)} MB)."
        )
    return detail


def _shutdown_jobs() -> None:
    for job in list(JOBS.values()):
        _signal_group(job.process, signal.SIGKILL)
    JOBS.clear()
    shutil.rmtree(JOB_TMP_DIR, ignore_errors=True)


atexit.register(_shutdown_jobs)


async def _spawn(command: str, fout, ferr, root: str):
    common = dict(
        stdin=asyncio.subprocess.PIPE,
        stdout=fout,
        stderr=ferr,
        start_new_session=True,
    )
    if sandbox.sandbox_available():
        return await asyncio.create_subprocess_exec(
            *sandbox.build_sandboxed_command(command, root), cwd=root, **common,
        )
    return await asyncio.create_subprocess_shell(
        command, executable="/bin/bash", cwd=root, **common,
    )


async def _await_settle(proc, wait_task, timeout: float, stdin_id) -> str:
    start = time.monotonic()
    prompt_cycles = 0
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        if wait_task.done():
            return "exited"
        if _tree_blocked_on_stdin(proc.pid, stdin_id):
            prompt_cycles += 1
        else:
            prompt_cycles = 0
        if prompt_cycles >= PROMPT_CONFIRM_CYCLES:
            return "awaiting_input"
        if time.monotonic() - start >= timeout:
            return "timeout"


def _close_stdin(proc) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:
        pass


async def _run_command(command: str, timeout: int, root: str) -> dict:
    timeout = min(timeout, MAX_TIMEOUT_SECONDS)
    path_out, path_err, fout, ferr = _open_capture_files()
    sandboxed = sandbox.sandbox_available()

    try:
        proc = await _spawn(command, fout, ferr, root)
    except Exception as e:
        fout.close()
        ferr.close()
        _cleanup_files(path_out, path_err)
        return {
            "status": "error",
            "command": command,
            "error": f"Failed to execute command: {type(e).__name__}: {e}",
        }

    fout.close()
    ferr.close()

    stdin_id = _fd_target(proc.pid, 0)
    wait_task = asyncio.ensure_future(proc.wait())
    start = time.monotonic()

    settle = await _await_settle(proc, wait_task, timeout, stdin_id)
    elapsed = time.monotonic() - start

    if settle == "exited":
        stdout, stderr = _read_text(path_out), _read_text(path_err)
        _cleanup_files(path_out, path_err)
        _close_stdin(proc)
        return {
            "status": "completed",
            "command": command,
            "root": root,
            "sandboxed": sandboxed,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "execution_time_seconds": round(elapsed, 3),
            "timed_out": False,
        }

    if settle == "awaiting_input":
        job = _register_job(command, root, sandboxed, proc, wait_task, path_out, path_err,
                             start, stdin_id=stdin_id, awaiting=True)
        return {
            "status": "awaiting_input",
            "command": command,
            "root": root,
            "sandboxed": sandboxed,
            "job_id": job.id,
            "stdout": _read_text(path_out),
            "stderr": _read_text(path_err),
            "message": (
                f"The command is waiting for interactive input and has been "
                f"parked as job '{job.id}' (it is still alive, not killed)."
            ),
            "hint": _awaiting_hint(job.id),
        }

    job = _register_job(command, root, sandboxed, proc, wait_task, path_out, path_err, start,
                         stdin_id=stdin_id)
    return {
        "status": "backgrounded",
        "command": command,
        "root": root,
        "sandboxed": sandboxed,
        "job_id": job.id,
        "stdout_so_far": _read_text(path_out),
        "stderr_so_far": _read_text(path_err),
        "elapsed_seconds": round(elapsed, 3),
        "message": (
            f"Command exceeded the {timeout}s wait and is still running "
            f"in the background as job '{job.id}'. Use the 'jobs' tool "
            f"(action='check', job_id='{job.id}') to see its progress or "
            f"final result, or action='kill' to stop it."
        ),
    }


def register(mcp):
    @mcp.tool(
        name="shell_execute",
        annotations={
            "title": "Execute Shell Command",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def shell_execute(
        command: Annotated[
            str,
            Field(
                description="The shell command to execute, in the active project's folder.",
                min_length=1,
                max_length=4096,
            ),
        ],
        ctx: Context,
        timeout: Annotated[
            int,
            Field(
                description=(
                    "Seconds to wait for the command before backgrounding it "
                    "(default: 60, max: 300). On overrun the command is NOT "
                    "killed — it keeps running as a background job."
                ),
                ge=1,
                le=300,
            ),
        ] = 60,
    ) -> dict:
        """
        Execute a shell command in the active project's folder (call
        select_project first). One-shot: no state carries between commands.

        Result 'status' is one of:
          completed      — finished; see stdout/stderr/exit_code.
          awaiting_input — waiting for input; parked as a job, drive it via
                           the 'jobs' tool or kill it.
          backgrounded   — outran the wait window; still running as a job.
          error          — could not be launched.
        """
        root = await session.get_active_project(ctx)
        result = await _run_command(command, timeout, root)
        audit.log(root, "shell_execute", f"{command!r} -> {result.get('status')}")
        return result

    @mcp.tool(
        name="jobs",
        annotations={
            "title": "Manage Background Jobs",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def jobs(
        action: Annotated[
            Literal["list", "check", "send", "kill"],
            Field(description="'list' all jobs; 'check' (job_id); 'send' (job_id, input); 'kill' (job_id)."),
        ],
        job_id: Annotated[Optional[str], Field(description="Required for check/send/kill.")] = None,
        input: Annotated[Optional[str], Field(description="For 'send': text to write to stdin.")] = None,
    ) -> dict:
        """Inspect, drive, or stop background jobs from shell_execute."""
        if action == "list":
            return {"jobs": [_job_summary(j) for j in JOBS.values()]}

        if not job_id:
            return {"error": f"action '{action}' requires a job_id."}

        job = JOBS.get(job_id)
        if job is None:
            return {"error": f"No job with ID '{job_id}'. Use action='list' to see active jobs."}

        if action == "check":
            return _job_detail(job)

        if action == "send":
            if job.status != "running":
                return {"error": f"Job '{job_id}' has already finished (status '{job.status}')."}
            if input is None:
                return {"error": "action 'send' requires the 'input' argument."}
            try:
                job.process.stdin.write(input.encode())
                await job.process.stdin.drain()
            except Exception as e:
                return {"error": f"Could not write to job '{job_id}': {type(e).__name__}: {e}"}
            job.awaiting_since = time.monotonic()
            settle = await _await_settle(job.process, job.wait_task, SEND_SETTLE_SECONDS, job.stdin_id)
            if settle == "exited":
                if job.status == "running":
                    job.status = "exited"
                if job.exit_code is None:
                    job.exit_code = job.process.returncode
                if job.end_time is None:
                    job.end_time = time.monotonic()
                _close_stdin(job.process)
            elif settle == "awaiting_input":
                job.live_state = "awaiting_input"
                job.awaiting_since = time.monotonic()
            else:
                job.live_state = "running"
            return _job_detail(job)

        # action == "kill"
        if job.status == "running":
            job.status = "killed"
            _signal_group(job.process, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(job.wait_task), timeout=2.0)
            except asyncio.TimeoutError:
                _signal_group(job.process, signal.SIGKILL)
                try:
                    await asyncio.shield(job.wait_task)
                except Exception:
                    pass
            _close_stdin(job.process)
            if job.exit_code is None:
                job.exit_code = job.process.returncode
            if job.end_time is None:
                job.end_time = time.monotonic()

        detail = _job_detail(job)
        detail["message"] = f"Job {job.id} killed."
        return detail
