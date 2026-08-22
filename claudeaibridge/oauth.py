"""
Self-hosted OAuth 2.1 authorization server for the connector.

This is the actual security boundary between "a token holder can read/edit/
commit in your registered projects" and "no one can." It deliberately does
NOT auto-approve: fastmcp's own InMemoryOAuthProvider (which this subclasses
for all the protocol plumbing — client registration, PKCE, token exchange,
refresh, revocation) is explicitly documented as a testing stub that skips
human consent entirely. Here, authorize() redirects the browser to a local
consent page instead of instantly minting a code, and only a human clicking
"Approve" on this machine causes a code to be issued.

Everything else (DCR, the /token endpoint, PKCE validation, discovery
metadata) is handled by the base classes — reimplementing that protocol
plumbing by hand would be the highest-risk way to introduce an auth bug.

State is persisted to the config directory so a server restart doesn't force
re-approval in claude.ai on every launch — approval is a one-time action per
install, not a per-boot one.
"""

import json
import secrets
import time
from pathlib import Path
from typing import Optional

from mcp.server.auth.provider import (
    AuthorizationParams,
    AuthorizeError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import (
    AccessToken,
    AuthorizationCode,
    InMemoryOAuthProvider,
    RefreshToken,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from . import registry

_PENDING_TTL_SECONDS = 10 * 60


class ConsentOAuthProvider(InMemoryOAuthProvider):
    def __init__(self, base_url: str, state_dir: Path):
        # Dynamic Client Registration must be on: claude.ai has no pre-shared
        # client_id and registers itself the first time it connects.
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self._state_path = state_dir / "oauth_state.json"
        self._pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams, float]] = {}
        self._load()

    # -- persistence ---------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for cid, c in data.get("clients", {}).items():
            self.clients[cid] = OAuthClientInformationFull(**c)
        for tok, t in data.get("access_tokens", {}).items():
            self.access_tokens[tok] = AccessToken(**t)
        for tok, t in data.get("refresh_tokens", {}).items():
            self.refresh_tokens[tok] = RefreshToken(**t)
        self._access_to_refresh_map = data.get("access_to_refresh_map", {})
        self._refresh_to_access_map = data.get("refresh_to_access_map", {})

    def _save(self) -> None:
        data = {
            "clients": {k: v.model_dump(mode="json") for k, v in self.clients.items()},
            "access_tokens": {k: v.model_dump(mode="json") for k, v in self.access_tokens.items()},
            "refresh_tokens": {k: v.model_dump(mode="json") for k, v in self.refresh_tokens.items()},
            "access_to_refresh_map": self._access_to_refresh_map,
            "refresh_to_access_map": self._refresh_to_access_map,
        }
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._state_path)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await super().register_client(client_info)
        self._save()

    async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
        result = await super().exchange_authorization_code(client, authorization_code)
        self._save()
        return result

    async def exchange_refresh_token(self, client, refresh_token, scopes) -> OAuthToken:
        result = await super().exchange_refresh_token(client, refresh_token, scopes)
        self._save()
        return result

    async def revoke_token(self, token) -> None:
        await super().revoke_token(token)
        self._save()

    # -- consent gate ----------------------------------------------------

    def _prune_pending(self) -> None:
        now = time.time()
        expired = [k for k, (_, _, created) in self._pending.items() if now - created > _PENDING_TTL_SECONDS]
        for k in expired:
            del self._pending[k]

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Instead of minting a code, park the request and send the browser
        to our own consent page — a human must approve it there."""
        if client.client_id not in self.clients:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description=f"Client '{client.client_id}' not registered.",
            )
        self._prune_pending()
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = (client, params, time.time())
        return f"{str(self.base_url).rstrip('/')}/consent?request_id={request_id}"

    async def _issue_code(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """The code-minting logic InMemoryOAuthProvider.authorize() would
        normally do immediately — run only after the consent page approves."""
        auth_code_value = f"auth_code_{secrets.token_hex(16)}"
        expires_at = time.time() + 5 * 60
        scopes_list = params.scopes or []
        if client.scope:
            allowed = set(client.scope.split())
            scopes_list = [s for s in scopes_list if s in allowed]
        self.auth_codes[auth_code_value] = AuthorizationCode(
            code=auth_code_value,
            client_id=client.client_id,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=scopes_list,
            expires_at=expires_at,
            code_challenge=params.code_challenge,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=auth_code_value, state=params.state)

    async def _handle_consent_page(self, request: Request) -> HTMLResponse:
        request_id = request.query_params.get("request_id", "")
        self._prune_pending()
        pending = self._pending.get(request_id)
        if pending is None:
            return HTMLResponse(_render_expired(), status_code=400)
        client, _params, _created = pending
        projects = registry.list_projects()
        return HTMLResponse(_render_consent(request_id, client, projects))

    async def _handle_consent_confirm(self, request: Request):
        form = await request.form()
        request_id = str(form.get("request_id", ""))
        decision = form.get("decision")
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return HTMLResponse(_render_expired(), status_code=400)
        client, params, _created = pending

        if decision != "approve":
            denial_uri = construct_redirect_uri(
                str(params.redirect_uri), error="access_denied", state=params.state
            )
            return RedirectResponse(url=denial_uri, status_code=302)

        redirect_uri = await self._issue_code(client, params)
        return RedirectResponse(url=redirect_uri, status_code=302)

    def get_routes(self, mcp_path: Optional[str] = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        routes.append(Route("/consent", endpoint=self._handle_consent_page, methods=["GET"]))
        routes.append(Route("/consent/confirm", endpoint=self._handle_consent_confirm, methods=["POST"]))
        return routes


def _render_consent(request_id: str, client: OAuthClientInformationFull, projects: dict) -> str:
    client_name = client.client_name or client.client_id
    project_list = "".join(f"<li><code>{name}</code> — {info['path']}</li>" for name, info in sorted(projects.items()))
    if not project_list:
        project_list = "<li><em>No projects registered yet — register one with `claudeaibridge add-project` before using the connector.</em></li>"
    return f"""<!doctype html>
<html><head><title>claudeaibridge — Authorize</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font-family: system-ui, sans-serif; max-width: 560px; margin: 4rem auto; padding: 0 1.5rem; color: #1a1a1a; }}
h1 {{ font-size: 1.25rem; }}
.card {{ border: 1px solid #ddd; border-radius: 8px; padding: 1.5rem; margin: 1.5rem 0; }}
ul {{ padding-left: 1.2rem; }}
button {{ font-size: 1rem; padding: 0.6rem 1.2rem; border-radius: 6px; border: none; cursor: pointer; margin-right: 0.5rem; }}
.approve {{ background: #1a7f37; color: white; }}
.deny {{ background: #eee; color: #333; }}
</style></head>
<body>
<h1>Allow claude.ai to access this machine?</h1>
<p><strong>{client_name}</strong> is requesting access to run file and shell tools, scoped to the project folders you've registered on this machine:</p>
<div class="card"><ul>{project_list}</ul></div>
<form method="post" action="/consent/confirm">
  <input type="hidden" name="request_id" value="{request_id}">
  <button class="approve" type="submit" name="decision" value="approve">Approve</button>
  <button class="deny" type="submit" name="decision" value="deny">Deny</button>
</form>
</body></html>"""


def _render_expired() -> str:
    return """<!doctype html>
<html><head><title>claudeaibridge — Link expired</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 480px; margin: 4rem auto; padding: 0 1.5rem;">
<h1>This authorization link has expired or was already used.</h1>
<p>Go back to claude.ai and try connecting again.</p>
</body></html>"""
