"""OAuth 2.1 resource-server support for the Streamable HTTP transport.

Opt-in: set the ODOO_MCP_AUTH_* environment variables and start an HTTP
transport. The server then advertises RFC 9728 protected-resource metadata
and rejects requests without a valid bearer token. Tokens are validated
against the authorization server's RFC 7662 introspection endpoint —
works with Keycloak, Auth0, Authentik, and any AS that supports
introspection. stdio transport is unaffected.

Env vars:
- ODOO_MCP_AUTH_ISSUER_URL          authorization server issuer (required)
- ODOO_MCP_AUTH_INTROSPECTION_URL   RFC 7662 endpoint (required)
- ODOO_MCP_AUTH_RESOURCE_URL        canonical URL of this MCP server (required)
- ODOO_MCP_AUTH_REQUIRED_SCOPES     comma-separated scopes (optional)
- ODOO_MCP_AUTH_CLIENT_ID/SECRET    client credentials for the
                                    introspection call (optional; many AS
                                    require them)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

logger = logging.getLogger(__name__)

AUTH_ENV_PREFIX = "ODOO_MCP_AUTH_"


def _env(name: str) -> str | None:
    value = os.environ.get(f"{AUTH_ENV_PREFIX}{name}", "").strip()
    return value or None


def auth_required_scopes() -> list[str]:
    raw = _env("REQUIRED_SCOPES") or ""
    return [scope.strip() for scope in raw.split(",") if scope.strip()]


def auth_configured() -> bool:
    """True when the three mandatory auth env vars are all present."""
    return all(
        _env(name) for name in ("ISSUER_URL", "INTROSPECTION_URL", "RESOURCE_URL")
    )


def auth_posture() -> dict[str, Any]:
    """Non-secret auth posture for health_check / runtime_security_report."""
    return {
        "enabled": auth_configured(),
        "issuer_url": _env("ISSUER_URL"),
        "resource_url": _env("RESOURCE_URL"),
        "required_scopes": auth_required_scopes(),
        "introspection_configured": _env("INTROSPECTION_URL") is not None,
    }


class IntrospectionTokenVerifier(TokenVerifier):
    """Validate bearer tokens via RFC 7662 token introspection."""

    def __init__(
        self,
        introspection_url: str,
        *,
        resource_url: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.introspection_url = introspection_url
        self.resource_url = resource_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout_seconds = timeout_seconds
        self._transport = transport  # test seam (httpx.MockTransport)

    async def verify_token(self, token: str) -> AccessToken | None:
        auth: httpx.BasicAuth | None = None
        if self.client_id is not None:
            auth = httpx.BasicAuth(self.client_id, self.client_secret or "")
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self._transport, auth=auth
            ) as client:
                response = await client.post(
                    self.introspection_url,
                    data={"token": token},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning("token introspection failed: %s", exc)
            return None
        if response.status_code != 200:
            logger.warning("token introspection returned HTTP %s", response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            logger.warning("token introspection returned invalid JSON")
            return None
        if not isinstance(payload, dict) or not payload.get("active"):
            return None

        expires_at = payload.get("exp")
        if isinstance(expires_at, (int, float)) and expires_at <= time.time():
            return None

        # RFC 8707 audience check: when the AS binds tokens to a resource,
        # require it to match this server's canonical URL.
        audience = payload.get("aud")
        if audience is not None:
            audiences = audience if isinstance(audience, list) else [audience]
            if self.resource_url not in [str(entry) for entry in audiences]:
                logger.warning("token audience %s does not match resource", audiences)
                return None

        scope_raw = payload.get("scope", "")
        scopes = scope_raw.split() if isinstance(scope_raw, str) else []
        return AccessToken(
            token=token,
            client_id=str(payload.get("client_id") or payload.get("sub") or "unknown"),
            scopes=scopes,
            expires_at=(
                int(expires_at) if isinstance(expires_at, (int, float)) else None
            ),
            resource=self.resource_url,
        )


def build_auth() -> tuple[AuthSettings, IntrospectionTokenVerifier] | None:
    """Build (AuthSettings, verifier) from env, or None when not configured."""
    if not auth_configured():
        partial = [
            name
            for name in ("ISSUER_URL", "INTROSPECTION_URL", "RESOURCE_URL")
            if _env(name)
        ]
        if partial:
            raise ValueError(
                "Incomplete OAuth configuration: set ODOO_MCP_AUTH_ISSUER_URL, "
                "ODOO_MCP_AUTH_INTROSPECTION_URL, and ODOO_MCP_AUTH_RESOURCE_URL "
                f"together (found only {', '.join(partial)})."
            )
        return None
    issuer = _env("ISSUER_URL")
    introspection = _env("INTROSPECTION_URL")
    resource = _env("RESOURCE_URL")
    assert issuer and introspection and resource
    settings = AuthSettings(
        issuer_url=AnyHttpUrl(issuer),
        resource_server_url=AnyHttpUrl(resource),
        required_scopes=auth_required_scopes() or None,
    )
    verifier = IntrospectionTokenVerifier(
        introspection,
        resource_url=resource,
        client_id=_env("CLIENT_ID"),
        client_secret=_env("CLIENT_SECRET"),
    )
    return settings, verifier
