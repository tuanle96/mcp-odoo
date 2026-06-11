import asyncio
import json
import time

import httpx
import pytest

from odoo_mcp import auth

RESOURCE = "https://mcp.example.com/mcp"
INTROSPECTION = "https://as.example.com/introspect"


def _verifier(handler, **kwargs):
    return auth.IntrospectionTokenVerifier(
        INTROSPECTION,
        resource_url=RESOURCE,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _json_response(payload, status_code=200):
    return httpx.Response(status_code, json=payload)


def test_verify_token_accepts_active_token_with_matching_audience():
    def handler(request):
        assert request.url == INTROSPECTION
        body = dict(pair.split("=") for pair in request.content.decode().split("&"))
        assert body["token"] == "tok-1"
        return _json_response(
            {
                "active": True,
                "aud": RESOURCE,
                "scope": "odoo.read odoo.write",
                "client_id": "agent-1",
                "exp": int(time.time()) + 600,
            }
        )

    token = asyncio.run(_verifier(handler).verify_token("tok-1"))
    assert token is not None
    assert token.client_id == "agent-1"
    assert token.scopes == ["odoo.read", "odoo.write"]
    assert token.resource == RESOURCE


def test_verify_token_rejects_inactive_expired_and_wrong_audience():
    cases = [
        {"active": False},
        {"active": True, "exp": int(time.time()) - 10},
        {"active": True, "aud": "https://other.example.com"},
    ]
    for payload in cases:
        token = asyncio.run(
            _verifier(lambda req, p=payload: _json_response(p)).verify_token("tok")
        )
        assert token is None, payload


def test_verify_token_rejects_http_errors_and_bad_json():
    assert (
        asyncio.run(
            _verifier(lambda req: httpx.Response(500, text="boom")).verify_token("t")
        )
        is None
    )
    assert (
        asyncio.run(
            _verifier(lambda req: httpx.Response(200, text="not-json")).verify_token(
                "t"
            )
        )
        is None
    )

    def raise_error(request):
        raise httpx.ConnectError("refused")

    assert asyncio.run(_verifier(raise_error).verify_token("t")) is None


def test_verify_token_sends_client_credentials_when_configured():
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization", "")
        return _json_response({"active": True, "sub": "user-7"})

    token = asyncio.run(
        _verifier(handler, client_id="mcp", client_secret="s3cret").verify_token("t")
    )
    assert token is not None
    assert seen["authorization"].startswith("Basic ")
    # No audience in the response → audience check is skipped (AS not RFC 8707-bound).
    assert token.client_id == "user-7"


def test_build_auth_returns_none_when_unconfigured(monkeypatch):
    for key in list(auth.os.environ):
        if key.startswith(auth.AUTH_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    assert auth.build_auth() is None
    assert auth.auth_posture()["enabled"] is False


def test_build_auth_rejects_partial_configuration(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_AUTH_ISSUER_URL", "https://as.example.com")
    monkeypatch.delenv("ODOO_MCP_AUTH_INTROSPECTION_URL", raising=False)
    monkeypatch.delenv("ODOO_MCP_AUTH_RESOURCE_URL", raising=False)
    with pytest.raises(ValueError, match="Incomplete OAuth configuration"):
        auth.build_auth()


def test_build_auth_builds_settings_and_verifier(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_AUTH_ISSUER_URL", "https://as.example.com")
    monkeypatch.setenv("ODOO_MCP_AUTH_INTROSPECTION_URL", INTROSPECTION)
    monkeypatch.setenv("ODOO_MCP_AUTH_RESOURCE_URL", RESOURCE)
    monkeypatch.setenv("ODOO_MCP_AUTH_REQUIRED_SCOPES", "odoo.read, odoo.write")
    monkeypatch.setenv("ODOO_MCP_AUTH_CLIENT_ID", "mcp")

    built = auth.build_auth()
    assert built is not None
    settings, verifier = built
    assert str(settings.issuer_url) == "https://as.example.com/"
    assert str(settings.resource_server_url) == RESOURCE
    assert settings.required_scopes == ["odoo.read", "odoo.write"]
    assert verifier.introspection_url == INTROSPECTION
    assert verifier.client_id == "mcp"

    posture = auth.auth_posture()
    assert posture["enabled"] is True
    assert posture["required_scopes"] == ["odoo.read", "odoo.write"]
    # posture is JSON-serializable and never contains the client secret
    assert "secret" not in json.dumps(posture).lower()


def test_runtime_security_report_exposes_oauth_posture(monkeypatch):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    for key in list(auth.os.environ):
        if key.startswith(auth.AUTH_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    assert server.runtime_security_report()["oauth"]["enabled"] is False


def test_configure_oauth_wires_fastmcp_only_for_http(monkeypatch, capsys):
    import argparse
    import importlib

    main_mod = importlib.import_module("odoo_mcp.__main__")
    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv("ODOO_MCP_AUTH_ISSUER_URL", "https://as.example.com")
    monkeypatch.setenv("ODOO_MCP_AUTH_INTROSPECTION_URL", INTROSPECTION)
    monkeypatch.setenv("ODOO_MCP_AUTH_RESOURCE_URL", RESOURCE)
    monkeypatch.setattr(server.mcp.settings, "auth", None, raising=False)
    monkeypatch.setattr(server.mcp, "_token_verifier", None, raising=False)

    # stdio → ignored with a warning
    main_mod.configure_oauth(argparse.Namespace(transport="stdio"))
    assert server.mcp.settings.auth is None
    assert "ignored" in capsys.readouterr().err

    # HTTP → wired
    main_mod.configure_oauth(argparse.Namespace(transport="streamable-http"))
    assert server.mcp.settings.auth is not None
    assert isinstance(server.mcp._token_verifier, auth.IntrospectionTokenVerifier)
