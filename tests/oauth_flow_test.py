"""
Manual smoke test for the OAuth 2.1 flow: dynamic client registration,
the consent gate (approve and deny paths), PKCE code exchange, and that
tool calls are rejected without a valid token and accepted with one.

Spins up `claudeaibridge serve` as a subprocess against a scratch config
dir, so it doesn't touch the user's real project registry or OAuth state.

Run with: .venv/bin/python tests/oauth_flow_test.py
"""

import base64
import hashlib
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

PORT = 8422
BASE = f"http://127.0.0.1:{PORT}"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def register_client(client: httpx.Client, name: str) -> str:
    resp = client.post(f"{BASE}/register", json={
        "redirect_uris": ["https://claude.ai/api/mcp/callback"],
        "client_name": name,
        "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "none",
    })
    resp.raise_for_status()
    return resp.json()["client_id"]


def start_authorization(client: httpx.Client, client_id: str, state: str, challenge: str) -> str:
    authz = client.get(f"{BASE}/authorize", params={
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "https://claude.ai/api/mcp/callback",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    assert authz.status_code in (302, 307), (authz.status_code, authz.text)
    location = authz.headers["location"]
    assert location.startswith(f"{BASE}/consent?request_id=")
    return location.split("request_id=")[1]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_config, tempfile.TemporaryDirectory() as tmp_project:
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = tmp_config

        # A registered project so the consent page's project-listing code path
        # actually runs -- an empty registry masks bugs there entirely (which
        # is exactly how a real one shipped previously: the old test always
        # ran against zero projects).
        add = subprocess.run(
            [sys.executable, "-m", "claudeaibridge.cli", "add-project", tmp_project],
            env=env, capture_output=True, text=True,
        )
        assert add.returncode == 0, add.stderr
        registered_path = str(Path(tmp_project).resolve())

        proc = subprocess.Popen(
            [sys.executable, "-m", "claudeaibridge.cli", "serve", "--foreground", "--port", str(PORT)],
            env=env,
        )
        try:
            for _ in range(50):
                try:
                    httpx.get(f"{BASE}/.well-known/oauth-authorization-server", timeout=0.5)
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("server did not start in time")

            client = httpx.Client(follow_redirects=False)

            # No DCR endpoint advertised without registration enabled would be a bug.
            meta = client.get(f"{BASE}/.well-known/oauth-authorization-server").json()
            assert "registration_endpoint" in meta

            # -- Deny path -------------------------------------------------
            cid = register_client(client, "deny-test")
            verifier = b64url(secrets.token_bytes(32))
            challenge = b64url(hashlib.sha256(verifier.encode()).digest())
            request_id = start_authorization(client, cid, "state1", challenge)

            deny = client.post(f"{BASE}/consent/confirm", data={"request_id": request_id, "decision": "deny"})
            assert "error=access_denied" in deny.headers["location"]

            reuse = client.post(f"{BASE}/consent/confirm", data={"request_id": request_id, "decision": "approve"})
            assert reuse.status_code == 400

            garbage = client.get(f"{BASE}/consent?request_id=not-a-real-id")
            assert garbage.status_code == 400

            # -- Approve path + token exchange + authenticated call -------
            cid = register_client(client, "approve-test")
            verifier = b64url(secrets.token_bytes(32))
            challenge = b64url(hashlib.sha256(verifier.encode()).digest())
            state = secrets.token_urlsafe(8)
            request_id = start_authorization(client, cid, state, challenge)

            page = client.get(f"{BASE}/consent?request_id={request_id}")
            assert page.status_code == 200 and "Approve" in page.text
            assert registered_path in page.text, "consent page should list the registered project's path"

            unauth = client.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                  headers={"Accept": "application/json, text/event-stream"})
            assert unauth.status_code == 401

            confirm = client.post(f"{BASE}/consent/confirm", data={"request_id": request_id, "decision": "approve"})
            location = confirm.headers["location"]
            assert location.startswith("https://claude.ai/api/mcp/callback")
            assert f"state={state}" in location
            code = location.split("code=")[1].split("&")[0]

            token_resp = client.post(f"{BASE}/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/api/mcp/callback",
                "client_id": cid,
                "code_verifier": verifier,
            })
            assert token_resp.status_code == 200, token_resp.text
            access_token = token_resp.json()["access_token"]

            headers = {"Authorization": f"Bearer {access_token}",
                       "Accept": "application/json, text/event-stream",
                       "Content-Type": "application/json"}
            init = client.post(f"{BASE}/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}},
            })
            assert init.status_code == 200, init.text
            headers["mcp-session-id"] = init.headers["mcp-session-id"]

            authed = client.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                  headers=headers)
            assert authed.status_code == 200
            assert "list_projects" in authed.text and "shell_execute" in authed.text

            # Restart-persistence: a second server on the same config dir
            # should still recognize the token minted above.
        finally:
            proc.terminate()
            proc.wait(timeout=5)

        proc2 = subprocess.Popen(
            [sys.executable, "-m", "claudeaibridge.cli", "serve", "--foreground", "--port", str(PORT)],
            env=env,
        )
        try:
            for _ in range(50):
                try:
                    httpx.get(f"{BASE}/.well-known/oauth-authorization-server", timeout=0.5)
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            headers.pop("mcp-session-id")
            init2 = client.post(f"{BASE}/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}},
            })
            assert init2.status_code == 200, "access token did not survive a server restart"
        finally:
            proc2.terminate()
            proc2.wait(timeout=5)

    print("SUCCESS -- register/authorize/consent(approve+deny)/token/authenticated-call/restart-persistence all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
