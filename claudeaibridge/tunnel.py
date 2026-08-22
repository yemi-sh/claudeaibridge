"""
ngrok tunnel — gives the local server a public HTTPS URL claude.ai can reach.

Deliberately does NOT auto-provision a reserved domain via ngrok's
management API (that needs a separate API key beyond the connect
authtoken, and reserving infrastructure on the user's behalf without them
seeing it first is the kind of action this project avoids). Instead: the
user reserves one free static domain in the ngrok dashboard themselves
(a couple of clicks), then registers it once via `claudeaibridge tunnel
set-domain`. Without a domain configured, ngrok still works but hands out
a fresh random URL each run — fine for a first try, not persistent.
"""

import os
from pathlib import Path
from typing import Optional

from . import registry

_AUTHTOKEN_FILE = "ngrok_authtoken"
_DOMAIN_FILE = "ngrok_domain"


def _read(name: str) -> Optional[str]:
    path = registry.config_dir() / name
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _write_secret(name: str, value: str) -> None:
    path = registry.config_dir() / name
    path.write_text(value.strip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def set_authtoken(token: str) -> None:
    _write_secret(_AUTHTOKEN_FILE, token)


def get_authtoken() -> Optional[str]:
    return _read(_AUTHTOKEN_FILE) or os.environ.get("NGROK_AUTHTOKEN")


def set_domain(domain: str) -> None:
    _write_secret(_DOMAIN_FILE, domain)


def get_domain() -> Optional[str]:
    return _read(_DOMAIN_FILE)


def status() -> dict:
    token = get_authtoken()
    return {
        "authtoken_configured": token is not None,
        "domain": get_domain(),
    }


def start(local_port: int, authtoken: Optional[str] = None, domain: Optional[str] = None):
    """
    Open an ngrok tunnel forwarding to 127.0.0.1:local_port and return its
    public HTTPS URL (str). The returned Listener object must be kept alive
    (held by the caller) for the tunnel to stay up — dropping it closes the
    tunnel.
    """
    import ngrok

    token = authtoken or get_authtoken()
    if not token:
        raise RuntimeError(
            "No ngrok authtoken configured. Get a free one at "
            "https://dashboard.ngrok.com/get-started/your-authtoken and run: "
            "claudeaibridge tunnel set-authtoken <token>"
        )

    options = {"authtoken": token}
    resolved_domain = domain or get_domain()
    if resolved_domain:
        options["domain"] = resolved_domain

    listener = ngrok.forward(local_port, "http", **options)
    return listener
