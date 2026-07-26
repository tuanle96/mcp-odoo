import importlib

from odoo_mcp import agent_tools


class FakeRequest:
    def __init__(self, lifespan_context):
        self.lifespan_context = lifespan_context


class FakeLife:
    def __init__(self, odoo):
        self.odoo = odoo
        self.schema_cache = {}
        self.write_approvals = {}

    def get_client(self, instance=None):
        return "default", self.odoo


class FakeCtx:
    def __init__(self, odoo):
        self.request_context = FakeRequest(FakeLife(odoo))


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get_model_fields(self, model):
        return {
            "name": {"type": "char", "readonly": False},
            "email": {"type": "char", "readonly": False},
        }

    def execute_method(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        return [101, 102]


def test_preview_batch_create_builds_single_atomic_call():
    report = agent_tools.build_write_preview_report(
        model="res.partner",
        operation="create",
        values_list=[{"name": "Ada"}, {"name": "Grace", "email": "g@x.io"}],
    )
    assert report["success"] is True
    assert report["approval"]["values_list"] == [
        {"name": "Ada"},
        {"name": "Grace", "email": "g@x.io"},
    ]
    assert report["execute_method"]["method"] == "create"
    assert report["execute_method"]["args"] == [
        [{"name": "Ada"}, {"name": "Grace", "email": "g@x.io"}]
    ]


def test_preview_batch_rejects_misuse():
    for kwargs, code in [
        (
            dict(operation="write", record_ids=[1], values_list=[{"name": "x"}]),
            "values_list_unsupported_operation",
        ),
        (
            dict(operation="create", values={"name": "x"}, values_list=[{"name": "y"}]),
            "values_and_values_list",
        ),
        (dict(operation="create", values_list=[]), "empty_values_list"),
        (dict(operation="create", values_list=[{}]), "invalid_values_list_entry"),
        (
            dict(
                operation="create",
                values_list=[{"name": str(i)} for i in range(101)],
            ),
            "values_list_too_large",
        ),
    ]:
        report = agent_tools.build_write_preview_report(model="res.partner", **kwargs)
        assert report["success"] is False
        assert code in {issue["code"] for issue in report["issues"]}, code


def test_batch_token_differs_from_single_and_verifies():
    single = agent_tools.build_write_preview_report(
        model="res.partner", operation="create", values={"name": "Ada"}
    )
    batch = agent_tools.build_write_preview_report(
        model="res.partner", operation="create", values_list=[{"name": "Ada"}]
    )
    assert single["approval"]["token"] != batch["approval"]["token"]
    ok, _ = agent_tools.verify_write_approval(batch["approval"])
    assert ok is True
    tampered = dict(batch["approval"])
    tampered["values_list"] = [{"name": "Mallory"}]
    ok, _ = agent_tools.verify_write_approval(tampered)
    assert ok is False


def test_validate_batch_checks_every_entry_against_metadata():
    report = agent_tools.validate_write_report(
        model="res.partner",
        operation="create",
        values=None,
        record_ids=None,
        values_list=[{"name": "Ada"}, {"bogus_field": 1}],
        fields_metadata={"name": {"type": "char", "readonly": False}},
        metadata_source="server",
    )
    assert report["success"] is False
    messages = [issue["message"] for issue in report["issues"]]
    assert any(
        "values_list[1]" in message and "bogus_field" in message for message in messages
    )


def test_batch_create_executes_end_to_end(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    client = RecordingClient()
    ctx = FakeCtx(client)

    validation = server.validate_write(
        ctx,
        "res.partner",
        "create",
        values_list=[{"name": "Ada"}, {"name": "Grace"}],
    )
    assert validation["success"] is True
    assert validation["approval_status"]["stored"] is True

    result = server.execute_approved_write(ctx, validation["approval"], confirm=True)
    assert result["success"] is True
    assert result["result"] == [101, 102]
    # Exactly ONE Odoo call with the whole vals_list (atomic).
    assert client.calls == [
        ("res.partner", "create", ([{"name": "Ada"}, {"name": "Grace"}],), {})
    ]


# ----- _normalize_write_response (v1.3.4) -----------------------------------
# Regression: FastMCP's inferred outputSchema for execute_approved_write
# can promote success-path keys (like result: <bool>) to required. The
# success return already carries result; the error returns did not, so
# FastMCP rejected the error envelope with
# "Output validation error: 'result' is a required property". The fix
# wraps every return through _normalize_write_response, which injects
# result: None on envelopes that do not carry the key.


def test_normalize_write_response_injects_result_none_on_error_envelope():
    from odoo_mcp.tools_write import _normalize_write_response
    out = _normalize_write_response(
        {"success": False, "tool": "execute_approved_write", "error": "boom"}
    )
    assert out == {
        "success": False,
        "tool": "execute_approved_write",
        "error": "boom",
        "result": None,
    }


def test_normalize_write_response_passthrough_when_result_present():
    from odoo_mcp.tools_write import _normalize_write_response
    success = {
        "success": True,
        "tool": "execute_approved_write",
        "model": "res.partner",
        "operation": "unlink",
        "result": True,
        "instance": "default",
    }
    assert _normalize_write_response(success) is success


def test_normalize_write_response_handles_non_dict():
    from odoo_mcp.tools_write import _normalize_write_response
    # Defensive: non-dict input is returned unchanged (no crash).
    assert _normalize_write_response(None) is None
    assert _normalize_write_response("not a dict") == "not a dict"


def test_execute_approved_write_error_envelope_carries_result_none(monkeypatch):
    """End-to-end: a token-mismatch error envelope must include result=None.

    Triggers every gate-failure path (token mismatch) and checks that
    FastMCP's inferred outputSchema check would not reject the envelope
    for the missing required `result` field.
    """
    from odoo_mcp import tools_write
    from odoo_mcp.server_core import WRITE_APPROVAL_TTL_SECONDS

    # Build a minimal ctx with an empty AppContext (no validated record -> gate fails).
    class _FakeAppContext:
        write_approvals = {}

    class _FakeRequestContext:
        lifespan_context = _FakeAppContext()

    class _FakeCtx:
        request_context = _FakeRequestContext()

    ctx = _FakeCtx()
    bogus = {
        "model": "res.partner",
        "operation": "unlink",
        "record_ids": [3598],
        "values": {},
        "context": {},
        "instance": "default",
        "token": "odoo-write:bogus",
    }
    out = tools_write.execute_approved_write(ctx, bogus, confirm=True)
    assert out["success"] is False
    assert "result" in out
    assert out["result"] is None
    assert "error" in out

