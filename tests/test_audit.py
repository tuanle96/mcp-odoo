import json

from odoo_mcp import audit


def test_record_write_event_disabled_without_env(monkeypatch):
    monkeypatch.delenv(audit.AUDIT_LOG_ENV, raising=False)
    assert audit.record_write_event("execute", outcome="success") is False
    assert audit.audit_posture() == {
        "enabled": False,
        "path": None,
        "env": "ODOO_MCP_AUDIT_LOG",
    }


def test_record_write_event_appends_jsonl_lines(monkeypatch, tmp_path):
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(audit.AUDIT_LOG_ENV, str(log_path))

    assert audit.record_write_event(
        "validate",
        outcome="approved",
        model="res.partner",
        operation="write",
        record_ids=[7],
        instance="client_a",
        token="odoo-write:abc123",
    )
    assert audit.record_write_event(
        "execute",
        outcome="denied",
        model="res.partner",
        operation="write",
        detail="confirm=true is required for destructive execution",
    )

    lines = [json.loads(line) for line in log_path.read_text().strip().splitlines()]
    assert len(lines) == 2
    assert lines[0]["event"] == "validate"
    assert lines[0]["instance"] == "client_a"
    assert lines[0]["record_ids"] == [7]
    # tokens are stored as digests, never plain text
    assert lines[0]["token_sha256"] is not None
    assert "odoo-write" not in log_path.read_text().split("token_sha256")[1][:40]
    assert lines[1]["outcome"] == "denied"
    assert "confirm=true" in lines[1]["detail"]
    assert audit.audit_posture()["enabled"] is True


def test_record_write_event_fails_open_on_oserror(monkeypatch, tmp_path):
    monkeypatch.setenv(audit.AUDIT_LOG_ENV, str(tmp_path / "no-dir" / "audit.jsonl"))
    assert (
        audit.record_write_event("execute", outcome="success", model="res.partner")
        is False
    )


def test_side_effect_policy_file_merges_with_env(monkeypatch, tmp_path):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "allowed_side_effect_methods": [
                    {"method": "sale.order.action_confirm", "reviewed_by": "qa"},
                    "stock.picking.button_validate",
                    {"no_method_key": True},
                ]
            }
        )
    )
    monkeypatch.setenv("ODOO_MCP_POLICY_FILE", str(policy))
    monkeypatch.setenv(
        "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
        "sale.order.action_confirm,account.move.action_post",
    )

    merged = server.allowed_side_effect_methods()
    assert merged == [
        "sale.order.action_confirm",
        "account.move.action_post",
        "stock.picking.button_validate",
    ]
    assert server.side_effect_method_allowed("stock.picking", "button_validate")

    posture = server.runtime_security_report()["side_effect_policy"]
    assert posture["file"] == str(policy)
    assert posture["file_method_count"] == 2
    assert posture["env_method_count"] == 2
    assert posture["error"] is None


def test_side_effect_policy_file_fails_closed_on_broken_json(monkeypatch, tmp_path):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    policy = tmp_path / "policy.json"
    policy.write_text("{not json")
    monkeypatch.setenv("ODOO_MCP_POLICY_FILE", str(policy))
    monkeypatch.delenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", raising=False)

    assert server.allowed_side_effect_methods() == []
    posture = server.runtime_security_report()["side_effect_policy"]
    assert posture["error"] is not None
    assert posture["file_method_count"] == 0


def test_side_effect_policy_absent_keeps_env_behaviour(monkeypatch):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.delenv("ODOO_MCP_POLICY_FILE", raising=False)
    monkeypatch.chdir("/")  # no ./odoo_mcp_policy.json here
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "a.b.c")
    assert server.allowed_side_effect_methods() == ["a.b.c"]


class _ElicitResult:
    def __init__(self, action, approve=None):
        self.action = action
        self.data = None
        if approve is not None:
            self.data = type("Data", (), {"approve": approve})()


class _ElicitCtx:
    """Minimal ctx exposing only async elicit; gates fail before ctx use otherwise."""

    def __init__(self, action="accept", approve=True, raise_exc=None):
        self._action = action
        self._approve = approve
        self._raise = raise_exc
        self.elicited_messages = []

    async def elicit(self, message, schema):
        if self._raise is not None:
            raise self._raise
        self.elicited_messages.append(message)
        return _ElicitResult(self._action, self._approve)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_elicitation_gate_off_by_default(monkeypatch):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.delenv(server.ELICIT_WRITES_ENV, raising=False)

    ctx = _ElicitCtx()
    result = _run(
        server.execute_approved_write_tool(
            ctx, {"model": "res.partner", "operation": "write", "token": "bogus"}
        )
    )
    # Falls through to the normal gates (bad token) without ever eliciting.
    assert result["success"] is False
    assert "token" in result["error"]
    assert ctx.elicited_messages == []


def test_elicitation_decline_blocks_write_and_audits(monkeypatch, tmp_path):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(server.ELICIT_WRITES_ENV, "1")
    monkeypatch.setenv(audit.AUDIT_LOG_ENV, str(log_path))

    ctx = _ElicitCtx(action="decline", approve=None)
    result = _run(
        server.execute_approved_write_tool(
            ctx,
            {
                "model": "res.partner",
                "operation": "write",
                "record_ids": [7],
                "values": {"name": "Ada"},
                "token": "whatever",
            },
            confirm=True,
        )
    )

    assert result["success"] is False
    assert "declined" in result["error"]
    entry = json.loads(log_path.read_text().strip())
    assert entry["event"] == "elicit"
    assert entry["outcome"] == "declined"
    # The human saw a readable diff summary.
    assert "write on res.partner" in ctx.elicited_messages[0]
    assert "Ada" in ctx.elicited_messages[0]


def test_elicitation_accept_proceeds_to_gates(monkeypatch):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv(server.ELICIT_WRITES_ENV, "1")

    ctx = _ElicitCtx(action="accept", approve=True)
    result = _run(
        server.execute_approved_write_tool(
            ctx, {"model": "res.partner", "operation": "write", "token": "bogus"}
        )
    )
    # Human approved, then the normal token gate still rejects the bad token.
    assert result["success"] is False
    assert "token" in result["error"]
    assert len(ctx.elicited_messages) == 1


def test_elicitation_unsupported_client_falls_back(monkeypatch):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv(server.ELICIT_WRITES_ENV, "1")

    ctx = _ElicitCtx(raise_exc=RuntimeError("client has no elicitation capability"))
    result = _run(
        server.execute_approved_write_tool(
            ctx, {"model": "res.partner", "operation": "write", "token": "bogus"}
        )
    )
    # Fallback: behaves exactly like the token flow.
    assert result["success"] is False
    assert "token" in result["error"]


def test_execute_approved_write_denial_is_audited(monkeypatch, tmp_path):
    import importlib

    server = importlib.import_module("odoo_mcp.server")
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(audit.AUDIT_LOG_ENV, str(log_path))

    result = server.execute_approved_write(
        None,
        approval={"model": "res.partner", "operation": "write", "token": "bogus"},
        confirm=True,
    )

    assert result["success"] is False
    entry = json.loads(log_path.read_text().strip())
    assert entry["event"] == "execute"
    assert entry["outcome"] == "denied"
    assert entry["model"] == "res.partner"
