"""
Live ngrok tunnel test — requires a real ngrok account.

I (the assistant) could not run this myself: it needs a real ngrok authtoken,
which means creating or using an ngrok account, and I don't have one in this
environment. The tunnel.py code was verified against the real ngrok service
for the failure path (invalid token correctly rejected over the network),
but the success path — actually getting a public URL and confirming
claude.ai-shaped requests reach the local server through it — needs to be
run by hand with a real token.

Usage:
    NGROK_AUTHTOKEN=xxxx .venv/bin/python tests/tunnel_test.py

Skips (exit 0) if NGROK_AUTHTOKEN is not set, rather than failing, so it's
safe to leave in the test suite without blocking anyone who hasn't set up
ngrok yet.
"""

import os
import subprocess
import sys
import tempfile
import time

import httpx


def main() -> int:
    token = os.environ.get("NGROK_AUTHTOKEN")
    if not token:
        print("SKIPPED: set NGROK_AUTHTOKEN to run this test.")
        return 0

    with tempfile.TemporaryDirectory() as tmp_config:
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = tmp_config

        set_token = subprocess.run(
            [sys.executable, "-m", "claudeaibridge.cli", "ngrok", "set-authtoken", token],
            env=env, capture_output=True, text=True,
        )
        assert set_token.returncode == 0, set_token.stderr

        proc = subprocess.Popen(
            [sys.executable, "-m", "claudeaibridge.cli", "serve", "--ngrok", "--port", "8424"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            public_url = None
            deadline = time.time() + 30
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                print(line, end="")
                if line.startswith("ngrok tunnel up: "):
                    public_url = line.split("ngrok tunnel up: ", 1)[1].strip()
                    break
            assert public_url, "never saw a tunnel URL printed"

            resp = httpx.get(f"{public_url}/.well-known/oauth-authorization-server", timeout=15)
            assert resp.status_code == 200, resp.text
            meta = resp.json()
            print("discovery metadata via public URL:", meta)
            assert meta["issuer"].rstrip("/") == public_url.rstrip("/") + "/" or public_url in meta["issuer"]

            print(f"SUCCESS -- reached the local server through {public_url}")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
