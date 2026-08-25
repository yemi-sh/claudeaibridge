"""
ngrok tunnel — gives the local server a public HTTPS URL claude.ai can reach.

Every ngrok account (free tier included) is permanently assigned one static
domain at signup, and the agent binds to it automatically once
authenticated with just the authtoken — no reservation step, no separate
API key, no domain configuration needed.
"""

import os
from pathlib import Path
from typing import Optional

from . import registry

_AUTHTOKEN_FILE = "ngrok_authtoken"


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


def status() -> dict:
    return {"authtoken_configured": get_authtoken() is not None}


def start(local_port: int, authtoken: Optional[str] = None):
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
            "claudeaibridge ngrok set-authtoken <token>"
        )

    listener = ngrok.forward(local_port, "http", authtoken=token)
    return listener
