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
3. Starting the server and printing the connector URL.

Then, in claude.ai: **Settings → Connectors → Add custom connector**, paste the
printed URL. claude.ai opens a browser tab to authorize — approve it on the consent
page — and you're connected. Ask Claude to list available projects, select one, and
start working.

## Command reference

### `claudeaibridge init`

The interactive setup wizard described above. Safe to re-run — every step shows
what's already configured and lets you keep or change it.

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
remember.

### `claudeaibridge serve`

Runs the MCP server directly (`init` calls into this at the end; use this to restart
without going through the wizard again).

| Flag | Meaning |
|---|---|
| `--ngrok` | Expose the server via ngrok. Requires `claudeaibridge ngrok set-authtoken` first. |
| `--base-url <url>` | The public URL claude.ai should use — for your own domain/tunnel instead of ngrok. Overridden automatically when `--ngrok` is used. |
| `--no-auth` | Disable OAuth. **Local testing only** — never combine with a public tunnel. |
| `--host` / `--port` | Where the local server listens (default `127.0.0.1:8420`). |
| `--transport stdio` | Talk over stdin/stdout instead of HTTP, for a local MCP client (e.g. Claude Desktop) on this same machine — no network, no tunnel, no OAuth. |

Examples:

```bash
claudeaibridge serve --ngrok                                    # the normal path
claudeaibridge serve --base-url https://your-domain.example     # bring your own tunnel
claudeaibridge serve --no-auth                                  # local-only testing
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

- Windows isn't supported.
- No background-service mode yet (systemd/launchd) — `serve` runs in the foreground.
- No interactive claude.ai-side widgets yet (a project picker, diff previews) —
  tool calls render as claude.ai's standard tool-call cards for now.

## License

[GPL-3.0-or-later](LICENSE).
