# claudeaibridge

Give [claude.ai](https://claude.ai) — including the free plan — a real coding-agent
experience on your own machine: file editing, shell access, git, all in a project
folder you explicitly choose, driven entirely from the web. No IDE, no Claude Code
subscription, no manually copy-pasting code into the browser.

It works by running a small local server that speaks [MCP](https://modelcontextprotocol.io)
(the same protocol Claude Code and Claude Desktop use for tools), exposing it to
claude.ai as a **custom connector** over a public URL (via [ngrok](https://ngrok.com),
or your own domain/tunnel), and gating access behind a real OAuth consent screen that
only you can approve, on this machine.

## Why

claude.ai connectors already let Claude read/write data in *other* services (Google
Drive, GitHub, etc.) through remote MCP servers. This project is that same mechanism
pointed at your own filesystem — the thing Claude Code does locally, made reachable
from a browser tab instead.

## How it's kept safe

This gives a web page the ability to edit files and run shell commands on your
computer. The design leans hard on containment rather than trust:

- **You choose the folders.** The connector can only ever see project folders you
  explicitly registered on this machine (via the CLI) — it has no way to add a new
  one itself, so a leaked connector URL doesn't mean "access to your whole disk."
- **Filesystem containment.** Every file tool resolves and checks paths against the
  active project's root before touching disk; `../` traversal and symlink escapes are
  rejected. Shell commands run inside a sandbox (`bwrap` on Linux, `sandbox-exec` on
  macOS) that makes the rest of the filesystem read-only.
- **Real OAuth consent.** Connecting isn't a rubber stamp — claude.ai has to complete
  an OAuth flow that opens a page on *this machine*, listing exactly which projects
  are registered, and a human has to click Approve. Nothing is granted silently.
- **Undo-safety.** File edits keep a backup before changing anything; deletions move
  files to a local trash folder instead of removing them, both inside the project's
  own `.claudeaibridge/` folder.
- **An audit trail.** Every file/shell action is appended to
  `<project>/.claudeaibridge/audit.log` in plain text — always answerable: "what did
  Claude actually do here?"

## Interactive widgets

Ask Claude to "show my projects" or "let me pick a project" and, on hosts that
support it, `browse_projects` renders as an actual clickable list in the chat
instead of plain text — clicking an entry calls `select_project` for you. On a
host without widget support it degrades automatically to the same plain project
list `list_projects` returns, so it's always safe to call either way.

`file_write` and `file_edit` do the same automatically — on hosts that support
it, every successful edit (and a failed one, with the reason why) is shown as a
rendered diff right there, no extra step required. Claude still gets the exact
same result data either way (what changed, backup path, error details) — the
widget is a rendering of that same data, not a replacement for it.

## Platform support

Linux and macOS. Windows is not supported yet (the sandboxing approach doesn't have a
Windows equivalent implemented).

## Install

**Option 1 — prebuilt binary.** Build one yourself with PyInstaller (see
[Building from source](#building-from-source)) — there's no separate download step
today, just a build step.

**Option 2 — from source, with Python 3.10+:**

```bash
git clone <this repo's URL>
cd claudeaibridge
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Quick start

```bash
claudeaibridge init
```

This walks you through everything:

1. An ngrok authtoken (free — [get one here](https://dashboard.ngrok.com/get-started/your-authtoken)),
   *or* your own domain/tunnel if you already have one, *or* local-only for testing.
2. Picking the project folder(s) claude.ai should be able to work in, via an
   interactive browser (arrow keys to navigate, Space to select, type to search).
3. Installing itself as a background service (systemd on Linux, launchd on macOS)
   and printing the connector URL — you can close the terminal, it keeps running.

Then, in claude.ai: **Settings → Connectors → Add custom connector**, paste the
printed URL. claude.ai opens a browser tab to authorize — approve it on the consent
page — and you're connected. Ask Claude to list available projects, select one, and
start working.

Check on it anytime with `claudeaibridge status`. If no service manager is available
(Windows, or a Linux setup without a systemd user session), `init` falls back to
running in the foreground instead.

## Command reference

### `claudeaibridge init`

The interactive setup wizard described above. Safe to re-run — every step shows
what's already configured and lets you keep or change it. Re-running it re-installs
the background service with whatever you choose this time, replacing the old one.

### `claudeaibridge status`

Shows whether the background service is currently running, and the last connector
URL it printed.

### `claudeaibridge stop`

Stops the background service and uninstalls it (removes the systemd/launchd unit
entirely, not just stops it) — the connector goes offline until you run `init` or
`serve` again.

### Projects

```bash
claudeaibridge add-project ~/code/my-repo   # register a specific folder
claudeaibridge add-project                  # or pick one (or several) interactively
claudeaibridge edit-project ~/code/my-repo  # unregister that folder directly
claudeaibridge edit-project                 # or open the picker, pre-checked with
                                             # everything registered — check/uncheck
                                             # to add or remove
claudeaibridge list-projects                # print all registered paths
```

A project's identity is its resolved filesystem path — there's no separate name to
remember. In chat, Claude can also show these as a clickable widget instead of a
plain list — see [Interactive widgets](#interactive-widgets).

### `claudeaibridge serve`

Installs/starts the MCP server as the background service with whatever flags you
give it (`init` calls into this same path at the end of the wizard) and returns —
it doesn't block your terminal. If the background service is already running,
`serve` just reports its connector URL instead of reinstalling it.

| Flag | Meaning |
|---|---|
| `--ngrok` | Expose the server via ngrok. Requires `claudeaibridge ngrok set-authtoken` first. |
| `--base-url <url>` | The public URL claude.ai should use — for your own domain/tunnel instead of ngrok. Overridden automatically when `--ngrok` is used. |
| `--no-auth` | Disable OAuth. **Local testing only** — never combine with a public tunnel. |
| `--host` / `--port` | Where the local server listens (default `127.0.0.1:8420`). |
| `--transport stdio` | Talk over stdin/stdout instead of HTTP, for a local MCP client (e.g. Claude Desktop) on this same machine — no network, no tunnel, no OAuth, no background service either. |
| `--foreground` | Run right here in this terminal instead of installing/using the background service — useful for watching logs live or quick debugging. If the background service is currently running, it's stopped first so the port is free. |

Examples:

```bash
claudeaibridge serve --ngrok                                    # install+start as a service
claudeaibridge serve --base-url https://your-domain.example     # bring your own tunnel
claudeaibridge serve --no-auth --foreground                     # local-only testing, blocking
claudeaibridge serve --transport stdio                          # local MCP client
```

### `claudeaibridge ngrok`

```bash
claudeaibridge ngrok set-authtoken <token>   # one-time setup
claudeaibridge ngrok status                  # check whether a token is configured
```

Every ngrok account (free tier included) is permanently assigned one static domain,
and the agent binds to it automatically with just the authtoken — nothing else to
configure. If you're on a paid plan and want a custom domain instead, run your own
`ngrok http --domain=...` process and point `claudeaibridge serve` at it with
`--base-url` (no `--ngrok`).

## Configuration

Everything lives under `~/.config/claudeaibridge/` (or `$XDG_CONFIG_HOME/claudeaibridge`):

- `projects.json` — the registered project allowlist
- `ngrok_authtoken` — your ngrok token (`chmod 600`)
- `oauth_state.json` — registered OAuth clients and tokens, so approving the
  connector in claude.ai is a one-time action, not something you redo on every
  restart
- `connector_url` — the last connector URL the server printed, so `claudeaibridge
  status` has something to show even when the server is running as a background
  service (and its own stdout isn't something you're watching)

## Building from source

```bash
pip install -e '.[build]'
./packaging/build.sh
```

Produces `dist/claudeaibridge-<os>-<arch>`. PyInstaller doesn't cross-compile — this
only builds for whatever platform you run it on.

## Running the tests

```bash
pip install -e '.[test]'
python tests/smoke_test.py        # core MCP tools, in-memory
python tests/oauth_flow_test.py   # full OAuth flow against a real subprocess
python tests/tunnel_test.py       # live ngrok test — needs NGROK_AUTHTOKEN set
```

## Known limitations

- Windows isn't supported, and `init`'s background-service step needs systemd
  (Linux) or launchd (macOS) — where neither is available it falls back to running
  in the foreground.
- The launchd (macOS) path for the background service is implemented but
  unverified — it was built and tested only on Linux (systemd), since that's the
  only platform available during development.
- Widgets so far: `browse_projects` (clickable project picker), and
  `file_write`/`file_edit` rendering their own change as a diff.

## License

[GPL-3.0-or-later](LICENSE).
