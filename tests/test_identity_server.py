"""Surface tests for request identity mode (Algorithma fork).

Isolation tests 1-8 from the vNext specification, run against the real
``AppContext`` with a fake per-user Odoo client so the whole choke point
(``_resolve_odoo`` → identity → bounded cache → tool) is exercised without a
network. Configured mode is covered by the upstream suite and must stay
byte-identical; these tests only ever enable request mode via monkeypatch.
"""

import importlib
import json

import pytest

from odoo_mcp import audit, identity

server = importlib.import_module("odoo_mcp.server")

KEY_A = "a-key-0123456789abcdef0123456789abcdef01"
KEY_B = "b-key-0123456789abcdef0123456789abcdef02"
ANNA = {"x-user-email": "anna@example.ch", "x-odoo-api-key": KEY_A, "x-librechat-user-id": "lc-a"}
BOB = {"x-user-email": "bob@example.ch", "x-odoo-api-key": KEY_B, "x-librechat-user-id": "lc-b"}
INSTANCES = {
    "bauag2": {"url": "http://127.0.0.1:8071", "db": "bauag2"},
    "sanitaer": {"url": "http://127.0.0.1:8069", "db": "sanitaer"},
}


class FakeRequest:
    def __init__(self, lifespan_context):
        self.lifespan_context = lifespan_context


class Ctx:
    """Minimal MCP Context: lifespan state + the headers the transport attached."""

    def __init__(self, app_context, headers=None):
        self.request_context = FakeRequest(app_context)
        self.headers = headers


class FakeUserClient:
    """Odoo client stand-in: every answer is stamped with the acting user."""

    def __init__(self, identity_, instance):
        self.identity = identity_
        self.instance = instance
        self.calls = []

    def search_read(self, model_name, domain, fields=None, offset=None, limit=None, order=None):
        self.calls.append(("search_read", model_name))
        return [{"id": 1, "name": f"{model_name} visible to {self.identity.email}"}]

    def get_model_fields(self, model_name):
        return {
            "name": {"type": "char", "string": "Name", "required": False, "readonly": False},
        }

    def execute_method(self, model, method, *args, **kwargs):
        self.calls.append((method, model, args))
        return 42


@pytest.fixture
def request_mode(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "request")
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "off")
    monkeypatch.setattr(server, "load_instances_config", lambda: ("bauag2", dict(INSTANCES)))
    built = []

    def build(entry, identity_, *, name="default"):
        if identity_.api_key not in (KEY_A, KEY_B):
            raise identity.IdentityAuthenticationError(
                f"Odoo instance {name!r} rejected the credentials supplied for "
                f"{identity_.email!r}: invalid login or API key."
            )
        client = FakeUserClient(identity_, name)
        built.append(client)
        return client

    monkeypatch.setattr(server, "build_identity_client", build)
    app_context = server.AppContext()
    return app_context, built


# --- Test 1 / Test 2: A authenticates as A, B as B --------------------------


def test_request_a_runs_as_a_and_request_b_runs_as_b(request_mode):
    app_context, built = request_mode
    result_a = server.search_records(Ctx(app_context, ANNA), "res.partner", fields=["name"])
    result_b = server.search_records(Ctx(app_context, BOB), "res.partner", fields=["name"])
    assert result_a["success"] and result_b["success"]
    assert result_a["result"][0]["name"] == "res.partner visible to anna@example.ch"
    assert result_b["result"][0]["name"] == "res.partner visible to bob@example.ch"
    assert [c.identity.email for c in built] == ["anna@example.ch", "bob@example.ch"]
    assert all(c.instance == "bauag2" for c in built)


# --- Test 3: A's cached client is never returned for B ----------------------


def test_cached_client_is_per_identity_not_per_instance(request_mode):
    app_context, built = request_mode
    ctx_a, ctx_b = Ctx(app_context, ANNA), Ctx(app_context, BOB)
    server.search_records(ctx_a, "res.partner", fields=["name"])
    server.search_records(ctx_a, "res.partner", fields=["name"])
    server.search_records(ctx_b, "res.partner", fields=["name"])
    assert len(built) == 2  # A reused once, B built separately
    assert built[0].identity.email == "anna@example.ch"
    assert built[1].identity.email == "bob@example.ch"
    # A rotated key -> new entry, never the old session
    rotated = dict(ANNA, **{"x-odoo-api-key": KEY_B})
    server.search_records(Ctx(app_context, rotated), "res.partner", fields=["name"])
    assert len(built) == 3
    # Another instance -> another entry for the same user
    server.search_records(ctx_a, "res.partner", fields=["name"], instance="sanitaer")
    assert len(built) == 4 and built[3].instance == "sanitaer"
    assert len(app_context.identity_clients) == 4


# --- Test 4: missing headers fail closed, no fallback -----------------------


def test_missing_headers_fail_closed_without_shared_fallback(request_mode, monkeypatch):
    app_context, built = request_mode
    real_get_odoo_client = server.get_odoo_client

    def shared_client_used():
        raise AssertionError("shared client used")

    monkeypatch.setattr(server, "get_odoo_client", shared_client_used)
    for headers in (None, {}, {"x-user-email": "anna@example.ch"}):
        result = server.search_records(Ctx(app_context, headers), "res.partner")
        assert result["success"] is False
        assert "identity" in result["error"].lower() or "X-Odoo-Api-Key" in result["error"]
    assert built == []
    with pytest.raises(identity.MissingIdentityError):
        _ = app_context.odoo
    with pytest.raises(identity.MissingIdentityError):
        app_context.get_client("bauag2")
    # odoo:// resources have no request context at all: the real factory
    # refuses them in request mode before any configuration is read.
    monkeypatch.setattr(server, "get_odoo_client", real_get_odoo_client)
    with pytest.raises(identity.MissingIdentityError):
        server.get_models()


# --- Test 5: invalid key -> sanitized error ---------------------------------


def test_invalid_key_returns_sanitized_error(request_mode):
    app_context, _ = request_mode
    bad = dict(ANNA, **{"x-odoo-api-key": "wrong-key-0123456789abcdef0123456789"})
    result = server.search_records(Ctx(app_context, bad), "res.partner")
    assert result["success"] is False
    assert "rejected the credentials" in result["error"]
    assert "wrong-key" not in result["error"]
    assert len(app_context.identity_clients) == 0


# --- Test 6: API keys never appear in logs or audit -------------------------


def test_keys_never_reach_logs_or_audit(request_mode, monkeypatch, tmp_path, capsys):
    app_context, _ = request_mode
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(audit.AUDIT_LOG_ENV, str(log_path))
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    ctx = Ctx(app_context, ANNA)
    report = server.validate_write(ctx, "res.partner", "create", values={"name": "Neu"})
    assert report["success"], report
    executed = server.execute_approved_write(ctx, report["approval"], confirm=True)
    assert executed["success"], executed
    health = server.health_check()
    text = log_path.read_text() + json.dumps(health) + capsys.readouterr().err
    assert KEY_A not in text
    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert {e["event"] for e in entries} == {"validate", "execute"}
    assert all(e["principal"] == "anna@example.ch" for e in entries)
    assert all(e["client_user_id"] == "lc-a" for e in entries)


# --- Test 7: approval issued to A cannot be executed by B -------------------


def test_approval_is_bound_to_the_validating_user(request_mode, monkeypatch):
    app_context, built = request_mode
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    ctx_a, ctx_b = Ctx(app_context, ANNA), Ctx(app_context, BOB)
    approval = server.validate_write(ctx_a, "res.partner", "create", values={"name": "Neu"})["approval"]
    assert approval["principal"] == "anna@example.ch"

    as_bob = server.execute_approved_write(ctx_b, approval, confirm=True)
    assert as_bob["success"] is False
    assert "different user" in as_bob["error"]

    forged = dict(approval, principal="bob@example.ch")
    forged_result = server.execute_approved_write(ctx_b, forged, confirm=True)
    assert forged_result["success"] is False
    assert "token does not match" in forged_result["error"]

    # Nothing was written by either attempt.
    assert not any(call[0] == "create" for c in built for call in c.calls)

    as_anna = server.execute_approved_write(ctx_a, approval, confirm=True)
    assert as_anna["success"] is True
    assert as_anna["result"] == 42
    anna_client = next(c for c in built if c.identity.email == "anna@example.ch")
    assert ("create", "res.partner", ({"name": "Neu"},)) in anna_client.calls
    # Single use: the same approval cannot be spent twice.
    again = server.execute_approved_write(ctx_a, approval, confirm=True)
    assert again["success"] is False


def test_same_user_different_credential_cannot_spend_the_approval(request_mode, monkeypatch):
    app_context, _ = request_mode
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    approval = server.validate_write(Ctx(app_context, ANNA), "res.partner", "create", values={"name": "Neu"})["approval"]
    other_key = dict(ANNA, **{"x-odoo-api-key": KEY_B})
    result = server.execute_approved_write(Ctx(app_context, other_key), approval, confirm=True)
    assert result["success"] is False
    assert "different identity or instance" in result["error"]


# --- Test 8: approval for instance A cannot execute on instance B -----------


def test_approval_is_bound_to_the_validating_instance(request_mode, monkeypatch):
    app_context, built = request_mode
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    ctx = Ctx(app_context, ANNA)
    approval = server.validate_write(
        ctx, "res.partner", "create", values={"name": "Neu"}, instance="bauag2"
    )["approval"]
    assert approval["instance"] == "bauag2"

    moved = dict(approval, instance="sanitaer")
    result = server.execute_approved_write(ctx, moved, confirm=True)
    assert result["success"] is False
    assert "token does not match" in result["error"]

    # Even with a recomputed token the server-side binding refuses it.
    record = app_context.write_approvals[approval["token"]]
    who = identity.RequestIdentity("anna@example.ch", KEY_A)
    assert server.approval_identity_matches(record, who.approval_binding("bauag2"))
    assert not server.approval_identity_matches(record, who.approval_binding("sanitaer"))
    assert not any(c.instance == "sanitaer" for c in built)


# --- preview tokens are per user in request mode ----------------------------


def test_preview_tokens_differ_per_user(request_mode):
    app_context, _ = request_mode
    preview_a = server.preview_write("res.partner", "create", values={"name": "Neu"}, ctx=Ctx(app_context, ANNA))
    preview_b = server.preview_write("res.partner", "create", values={"name": "Neu"}, ctx=Ctx(app_context, BOB))
    assert preview_a["approval"]["token"] != preview_b["approval"]["token"]
    assert preview_a["approval"]["principal"] == "anna@example.ch"
    validated = server.validate_write(Ctx(app_context, ANNA), "res.partner", "create", values={"name": "Neu"})
    assert validated["approval"]["token"] == preview_a["approval"]["token"]


def test_chatter_token_is_bound_to_the_user(request_mode, monkeypatch):
    app_context, built = request_mode
    monkeypatch.delenv("MCP_CHATTER_DIRECT", raising=False)
    preview = server.chatter_post(Ctx(app_context, ANNA), "res.partner", 7, "hello")
    assert preview["mode"] == "preview"
    hijack = server.chatter_post(
        Ctx(app_context, BOB), "res.partner", 7, "hello", approval=preview["approval"], confirm=True
    )
    assert hijack["success"] is False
    assert "does not match" in hijack["error"]
    assert not any(call[0] == "message_post" for c in built for call in c.calls)


# --- health posture ---------------------------------------------------------


def test_health_check_exposes_identity_posture(request_mode):
    health = server.health_check()
    posture = health["identity"]
    assert posture["identity_mode"] == "request"
    assert posture["request_identity_required"] is True
    assert posture["configured_shared_credentials_used"] is False
    assert posture["configured_credentials_present"] is False
    assert health["runtime"]["identity"] == posture
    # Tests run without an HTTP runtime -> the stdio warning must be visible.
    assert any("stdio" in w for w in posture["warnings"])


def test_health_check_warns_about_shared_credentials_in_request_mode(request_mode, monkeypatch):
    monkeypatch.setattr(
        server,
        "load_instances_config",
        lambda: ("bauag2", {"bauag2": {**INSTANCES["bauag2"], "username": "bot", "password": "x"}}),
    )
    posture = server.health_check()["identity"]
    assert posture["configured_credentials_present"] is True
    assert any("ignored in request mode" in w for w in posture["warnings"])


def test_configured_mode_health_has_no_identity_warnings(monkeypatch):
    monkeypatch.delenv(identity.IDENTITY_MODE_ENV, raising=False)
    posture = server.health_check()["identity"]
    assert posture["identity_mode"] == "configured"
    assert posture["request_identity_required"] is False
    assert posture["warnings"] == []


# --- CLI guard --------------------------------------------------------------


def test_cli_refuses_request_mode_over_stdio(monkeypatch):
    from odoo_mcp import __main__ as cli

    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "request")
    args = cli.parse_args(["--transport", "stdio"])
    with pytest.raises(ValueError, match="streamable-http"):
        cli.configure_mcp_runtime(args)
    args = cli.parse_args(["--transport", "streamable-http", "--port", "18400"])
    options = cli.configure_mcp_runtime(args)
    assert options["host"] == "127.0.0.1"
    assert cli.health_payload(args)["identity"]["identity_mode"] == "request"


# --- plugins go through the same choke point --------------------------------


def test_plugin_api_resolves_identity_clients(request_mode):
    from odoo_mcp import plugin_api

    app_context, built = request_mode
    name, client = plugin_api.resolve_odoo(Ctx(app_context, BOB))
    assert name == "bauag2"
    assert client.identity.email == "bob@example.ch"
    with pytest.raises(identity.MissingIdentityError):
        plugin_api.resolve_odoo(Ctx(app_context, None))
