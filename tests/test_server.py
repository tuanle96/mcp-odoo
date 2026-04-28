import asyncio
import importlib
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


def call_tool_json(server, name, arguments):
    content = asyncio.run(server.mcp.call_tool(name, arguments))
    if isinstance(content, tuple):
        content, structured = content
        if isinstance(structured, dict):
            return structured.get("result", structured)
    if content and isinstance(content[0], list):
        content = content[0]
    assert isinstance(content[0], TextContent)
    return json.loads(content[0].text)


class FakeRequest:
    def __init__(self, lifespan_context):
        self.lifespan_context = lifespan_context


class FakeCtx:
    def __init__(self, odoo):
        self.request_context = FakeRequest(FakeLife(odoo))


class FakeLife:
    def __init__(self, odoo):
        self.odoo = odoo
        self.schema_cache = {}
        self.write_approvals = {}


def test_server_import_initializes_fastmcp_with_current_sdk_without_lifespan():
    server = importlib.import_module("odoo_mcp.server")

    assert isinstance(server.mcp, FastMCP)
    assert server.mcp.name == "Odoo MCP Server"
    assert server.mcp.instructions == "MCP Server for interacting with Odoo ERP systems"


def test_server_registers_expected_tools_and_resources_without_lifespan():
    server = importlib.import_module("odoo_mcp.server")

    tools = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    resources = {
        str(resource.uri) for resource in asyncio.run(server.mcp.list_resources())
    }
    templates = {
        str(template.uriTemplate)
        for template in asyncio.run(server.mcp.list_resource_templates())
    }

    expected_tools = {
        "execute_method",
        "list_models",
        "get_model_fields",
        "search_records",
        "read_record",
        "search_employee",
        "search_holidays",
        "diagnose_odoo_call",
        "inspect_model_relationships",
        "generate_json2_payload",
        "upgrade_risk_report",
        "fit_gap_report",
        "get_odoo_profile",
        "schema_catalog",
        "preview_write",
        "validate_write",
        "execute_approved_write",
        "scan_addons_source",
        "build_domain",
        "business_pack_report",
        "health_check",
    }
    assert expected_tools <= tools
    assert len(tools) == 21
    assert "odoo://models" in resources
    assert {
        "odoo://model/{model_name}",
        "odoo://record/{model_name}/{record_id}",
        "odoo://search/{model_name}/{domain}",
    } <= templates

    prompts = {prompt.name for prompt in asyncio.run(server.mcp.list_prompts())}
    assert {
        "diagnose_failed_odoo_call",
        "fit_gap_workshop",
        "json2_migration_plan",
        "safe_write_review",
        "custom_module_audit",
    } <= prompts


def test_tools_expose_safety_annotations_and_output_schemas():
    server = importlib.import_module("odoo_mcp.server")

    tools = {
        tool.name: tool.model_dump() for tool in asyncio.run(server.mcp.list_tools())
    }

    assert tools["list_models"]["annotations"]["readOnlyHint"] is True
    assert tools["list_models"]["annotations"]["destructiveHint"] is False
    assert tools["preview_write"]["annotations"]["destructiveHint"] is False
    assert tools["execute_approved_write"]["annotations"]["destructiveHint"] is True
    assert tools["execute_method"]["annotations"]["destructiveHint"] is True
    assert tools["get_odoo_profile"]["outputSchema"]["type"] == "object"


def test_resources_are_json_with_assistant_annotations():
    server = importlib.import_module("odoo_mcp.server")

    resources = {
        str(resource.uri): resource.model_dump()
        for resource in asyncio.run(server.mcp.list_resources())
    }
    templates = {
        str(template.uriTemplate): template.model_dump()
        for template in asyncio.run(server.mcp.list_resource_templates())
    }

    assert resources["odoo://models"]["mimeType"] == "application/json"
    assert resources["odoo://models"]["annotations"]["audience"] == ["assistant"]
    assert templates["odoo://model/{model_name}"]["mimeType"] == "application/json"


def test_domain_normalization_accepts_json_object_and_standard_domain_list():
    server = importlib.import_module("odoo_mcp.server")

    assert server.normalize_domain_input('[["name", "ilike", "ada"]]') == [
        ["name", "ilike", "ada"]
    ]
    assert server.normalize_domain_input(
        {"conditions": [{"field": "id", "operator": ">", "value": 0}]}
    ) == [["id", ">", 0]]
    assert server.normalize_domain_input(
        ["|", ["name", "=", "Ada"], ["id", ">", 0]]
    ) == [
        "|",
        ["name", "=", "Ada"],
        ["id", ">", 0],
    ]


def test_safety_helpers_reject_bad_model_names_and_bounds_limits():
    server = importlib.import_module("odoo_mcp.server")

    assert server.clamp_limit(999) == 100
    server.validate_model_name("res.partner")
    server.validate_method_name("search_read")

    try:
        server.validate_model_name("res.partner;DROP")
    except ValueError as exc:
        assert "Invalid model name" in str(exc)
    else:
        raise AssertionError("unsafe model name should be rejected")

    try:
        server.validate_method_name("search-read")
    except ValueError as exc:
        assert "Invalid method name" in str(exc)
    else:
        raise AssertionError("unsafe method name should be rejected")

    try:
        server.clamp_limit(0)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("zero limit should be rejected")


def test_lifespan_is_lazy_and_preview_tools_call_tool_succeed_when_client_raises(
    monkeypatch,
):
    server = importlib.import_module("odoo_mcp.server")

    def fail_client():
        raise AssertionError("get_odoo_client should not be called for preview tools")

    monkeypatch.setattr(server, "get_odoo_client", fail_client)

    async def enter_lifespan():
        async with server.app_lifespan(server.mcp) as lifespan_context:
            return lifespan_context

    context = asyncio.run(enter_lifespan())
    assert context._odoo is None

    payload = call_tool_json(
        server,
        "generate_json2_payload",
        {
            "model": "res.partner",
            "method": "search_read",
            "args": [[["id", ">", 0]]],
            "kwargs": {"fields": ["name"], "limit": 1},
            "database": "demo-db",
        },
    )
    assert payload["success"] is True
    assert payload["metadata_used"]["client_instantiated"] is False
    assert payload["headers"]["X-Odoo-Database"] == "demo-db"

    diagnosis = call_tool_json(
        server,
        "diagnose_odoo_call",
        {"model": "res.partner", "method": "write", "args": [[7], {"name": "Ada"}]},
    )
    assert diagnosis["classification"]["safety"] == "destructive"

    upgrade = call_tool_json(
        server,
        "upgrade_risk_report",
        {"target_version": "20.0", "methods": [{"model": "res.partner", "method": "write"}]},
    )
    assert upgrade["transport"]["xmlrpc_jsonrpc_deprecation"] == "Odoo 20 fall 2026"

    fit_gap = call_tool_json(
        server,
        "fit_gap_report",
        {"requirements": ["Track contacts", "Complex custom workflow"]},
    )
    assert fit_gap["success"] is True
    assert fit_gap["summary"]["fit"] >= 1


def test_report_tools_do_not_execute_candidate_methods():
    server = importlib.import_module("odoo_mcp.server")

    class ExplodingClient:
        def execute_method(self, *args, **kwargs):
            raise AssertionError("diagnostic tools must not execute candidate methods")

        def get_model_fields(self, model):
            return {"partner_id": {"type": "many2one", "relation": "res.partner"}}

    assert server.diagnose_odoo_call("res.partner", "write")["success"] is True
    assert server.generate_json2_payload("res.partner", "write")["success"] is True
    assert server.upgrade_risk_report(
        methods=[{"model": "res.partner", "method": "write"}]
    )["success"] is True
    assert server.fit_gap_report(["Track contacts"])["success"] is True
    relationships = server.inspect_model_relationships(
        FakeCtx(ExplodingClient()),
        "res.partner",
        use_live_metadata=True,
    )
    assert relationships["success"] is True
    assert relationships["relationships"]["many2one"][0]["relation"] == "res.partner"


def test_inspect_model_relationships_uses_only_get_model_fields_for_live_metadata():
    server = importlib.import_module("odoo_mcp.server")
    calls = []

    class FakeClient:
        def get_model_fields(self, model):
            calls.append(("get_model_fields", model))
            return {"partner_id": {"type": "many2one", "relation": "res.partner"}}

        def execute_method(self, *args, **kwargs):
            calls.append(("execute_method", args, kwargs))
            raise AssertionError("execute_method must not be used")

    report = server.inspect_model_relationships(FakeCtx(FakeClient()), "res.partner")

    assert report["success"] is True
    assert calls == [("get_model_fields", "res.partner")]


def test_new_tools_return_stable_top_level_response_keys():
    server = importlib.import_module("odoo_mcp.server")

    payload = call_tool_json(
        server,
        "generate_json2_payload",
        {"model": "res.partner", "method": "search_read", "args": [[]]},
    )
    assert {
        "success",
        "tool",
        "model",
        "method",
        "endpoint",
        "headers",
        "body",
        "warnings",
        "transaction",
        "classification",
        "metadata_used",
    } <= payload.keys()

    diagnosis = call_tool_json(
        server,
        "diagnose_odoo_call",
        {"model": "res.partner", "method": "search_read", "args": [[]]},
    )
    assert {"success", "tool", "classification", "issues", "suggested_payload"} <= diagnosis.keys()

    upgrade = call_tool_json(server, "upgrade_risk_report", {"target_version": "20.0"})
    assert {"success", "tool", "summary", "risks", "transport"} <= upgrade.keys()

    fit_gap = call_tool_json(
        server, "fit_gap_report", {"requirements": ["Track contacts"]}
    )
    assert {"success", "tool", "summary", "items", "metadata_used"} <= fit_gap.keys()


def test_safe_write_preview_validate_and_execute_gates(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")

    preview = call_tool_json(
        server,
        "preview_write",
        {
            "model": "res.partner",
            "operation": "write",
            "record_ids": [7],
            "values": {"name": "Ada"},
        },
    )

    assert preview["success"] is True
    assert preview["approval"]["token"].startswith("odoo-write:")

    class FakeClient:
        def get_model_fields(self, model):
            return {"name": {"type": "char", "readonly": False}}

        def execute_method(self, *args, **kwargs):
            raise AssertionError("writes must stay disabled without env gate")

    validation = server.validate_write(
        FakeCtx(FakeClient()),
        "res.partner",
        "write",
        values={"name": "Ada"},
        record_ids=[7],
    )
    assert validation["success"] is True
    assert validation["approval"]["token"] == preview["approval"]["token"]

    monkeypatch.delenv("ODOO_MCP_ENABLE_WRITES", raising=False)
    preview_only_blocked = server.execute_approved_write(
        FakeCtx(FakeClient()), preview["approval"], confirm=True
    )
    assert preview_only_blocked["success"] is False
    assert "validated" in preview_only_blocked["error"]

    ctx = FakeCtx(FakeClient())
    validation = server.validate_write(
        ctx,
        "res.partner",
        "write",
        values={"name": "Ada"},
        record_ids=[7],
    )
    assert validation["success"] is True
    assert validation["approval"]["token"] == preview["approval"]["token"]

    blocked = server.execute_approved_write(
        ctx, validation["approval"], confirm=True
    )
    assert blocked["success"] is False
    assert "disabled" in blocked["error"]

    bad = dict(preview["approval"])
    bad["values"] = {"name": "Grace"}
    rejected = server.execute_approved_write(ctx, bad, confirm=True)
    assert rejected["success"] is False
    assert "token" in rejected["error"]


def test_execute_method_validates_model_and_method_before_client_call():
    server = importlib.import_module("odoo_mcp.server")

    class FakeClient:
        def execute_method(self, *args, **kwargs):
            raise AssertionError("invalid input must fail before client call")

    result = server.execute_method(FakeCtx(FakeClient()), "res.partner", "bad-method")

    assert result["success"] is False
    assert "Invalid method name" in result["error"]


def test_execute_method_blocks_direct_writes_and_unknown_methods(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")

    class FakeClient:
        def execute_method(self, *args, **kwargs):
            raise AssertionError("blocked methods must fail before client call")

    monkeypatch.delenv("ODOO_MCP_ALLOW_UNKNOWN_METHODS", raising=False)

    blocked_write = server.execute_method(
        FakeCtx(FakeClient()),
        "res.partner",
        "write",
        args=[[7], {"name": "Ada"}],
    )
    assert blocked_write["success"] is False
    assert "preview_write" in blocked_write["error"]

    blocked_unknown = server.execute_method(
        FakeCtx(FakeClient()),
        "sale.order",
        "action_confirm",
        args=[[7]],
    )
    assert blocked_unknown["success"] is False
    assert "blocked by default" in blocked_unknown["error"]


def test_execute_method_can_opt_into_unknown_methods(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")
    calls = []

    class FakeClient:
        def execute_method(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"ok": True}

    monkeypatch.setenv("ODOO_MCP_ALLOW_UNKNOWN_METHODS", "1")

    result = server.execute_method(
        FakeCtx(FakeClient()),
        "sale.order",
        "action_confirm",
        args=[[7]],
    )

    assert result["success"] is True
    assert calls == [(("sale.order", "action_confirm", [7]), {})]


def test_validate_write_only_registers_live_metadata_approvals(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")

    class FakeClient:
        def execute_method(self, *args, **kwargs):
            raise AssertionError("non-registered approvals must fail before execution")

    ctx = FakeCtx(FakeClient())
    shape_only = server.validate_write(
        ctx,
        "res.partner",
        "write",
        values={"name": "Ada"},
        record_ids=[7],
        use_live_metadata=False,
    )

    assert shape_only["success"] is True
    assert shape_only["approval_status"]["stored"] is False
    assert ctx.request_context.lifespan_context.write_approvals == {}

    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    blocked = server.execute_approved_write(
        ctx, shape_only["approval"], confirm=True
    )
    assert blocked["success"] is False
    assert "validated" in blocked["error"]

    empty_metadata = server.validate_write(
        FakeCtx(FakeClient()),
        "res.partner",
        "write",
        values={"name": "Ada"},
        record_ids=[7],
        fields_metadata={},
        use_live_metadata=False,
    )
    assert empty_metadata["success"] is False
    assert empty_metadata["issues"][0]["code"] == "unknown_field"
    assert empty_metadata["approval"] is None


def test_validate_write_rejects_empty_live_metadata_for_unlink(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")

    class EmptyMetadataClient:
        def get_model_fields(self, model):
            return {}

        def execute_method(self, *args, **kwargs):
            raise AssertionError("empty metadata must fail before execution")

    ctx = FakeCtx(EmptyMetadataClient())
    validation = server.validate_write(
        ctx,
        "res.partner",
        "unlink",
        record_ids=[7],
    )

    assert validation["success"] is False
    assert "metadata was empty" in validation["error"]
    assert validation["approval_status"]["stored"] is False
    assert ctx.request_context.lifespan_context.write_approvals == {}

    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    preview = server.preview_write("res.partner", "unlink", record_ids=[7])
    blocked = server.execute_approved_write(ctx, preview["approval"], confirm=True)
    assert blocked["success"] is False
    assert "validated" in blocked["error"]


def test_execute_approved_write_runs_only_after_all_gates(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")

    class FakeClient:
        def get_model_fields(self, model):
            return {"name": {"type": "char", "readonly": False}}

        def execute_method(self, *args, **kwargs):
            calls.append((args, kwargs))
            return True

    calls = []
    ctx = FakeCtx(FakeClient())
    validation = server.validate_write(
        ctx,
        "res.partner",
        "write",
        values={"name": "Ada"},
        record_ids=[7],
        context={"lang": "en_US"},
    )

    assert validation["approval_status"]["stored"] is True
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    result = server.execute_approved_write(
        ctx, validation["approval"], confirm=True
    )

    assert result["success"] is True
    assert calls == [
        (
            ("res.partner", "write", [7], {"name": "Ada"}),
            {"context": {"lang": "en_US"}},
        )
    ]


def test_schema_catalog_caches_and_business_pack_uses_live_metadata():
    server = importlib.import_module("odoo_mcp.server")

    class FakeClient:
        def __init__(self):
            self.model_calls = 0

        def get_models(self):
            self.model_calls += 1
            return {
                "model_names": ["res.partner", "sale.order"],
                "models_details": {
                    "res.partner": {"name": "Contact"},
                    "sale.order": {"name": "Sales Order"},
                },
            }

        def get_model_fields(self, model):
            return {"name": {"type": "char"}}

        def get_installed_modules(self, limit=100):
            return [{"name": "sale"}, {"name": "crm"}]

    fake = FakeClient()
    ctx = FakeCtx(fake)
    first = server.schema_catalog(ctx, query="sale", include_fields=True)
    second = server.schema_catalog(ctx, query="sale", include_fields=True)
    pack = server.business_pack_report(ctx, "sales")

    assert first["success"] is True
    assert first["result"][0]["model"] == "sale.order"
    assert second["metadata_used"]["cache_hit"] is True
    assert fake.model_calls == 2
    assert pack["success"] is True
    assert "sale" in pack["installed_modules"]


def test_domain_builder_and_addon_scanner(tmp_path: Path, monkeypatch):
    server = importlib.import_module("odoo_mcp.server")

    domain = call_tool_json(
        server,
        "build_domain",
        {
            "conditions": [
                {"field": "name", "operator": "ilike", "value": "Ada"},
                {"field": "id", "operator": "in", "value": [1, 2]},
            ],
            "logical_operator": "or",
        },
    )
    assert domain["success"] is True
    assert domain["domain"][0] == "|"

    addon = tmp_path / "custom_sale"
    addon.mkdir()
    (addon / "__manifest__.py").write_text(
        "{'name': 'Custom Sale', 'depends': ['sale'], 'installable': True}",
        encoding="utf-8",
    )
    (addon / "models.py").write_text(
        "from odoo import models\n"
        "class X(models.Model):\n"
        "    _name = 'x.demo'\n"
        "    def write(self, vals):\n"
        "        return super().write(vals)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ODOO_ADDONS_PATHS", str(tmp_path))
    scan = call_tool_json(
        server,
        "scan_addons_source",
        {"addons_paths": [str(tmp_path)], "max_files": 20},
    )

    assert scan["success"] is True
    assert scan["summary"]["modules"] == 1
    assert any(item["code"] == "custom_method" for item in scan["source_findings"])

    blocked_scan = call_tool_json(
        server,
        "scan_addons_source",
        {"addons_paths": [str(tmp_path.parent)], "max_files": 20},
    )
    assert blocked_scan["success"] is False
    assert "outside configured ODOO_ADDONS_PATHS" in blocked_scan["error"]


def test_profile_health_and_prompts_are_available():
    server = importlib.import_module("odoo_mcp.server")

    class FakeClient:
        url = "https://odoo.example.test"
        hostname = "odoo.example.test"
        db = "demo-db"
        username = "demo"
        transport = "xmlrpc"
        timeout = 3
        verify_ssl = True
        json2_database_header = True

        def get_server_version(self):
            return {"server_version": "19.0"}

        def get_user_context(self):
            return {"lang": "en_US"}

    profile = server.get_odoo_profile(
        FakeCtx(FakeClient()), include_modules=False, module_limit=10
    )
    assert profile["success"] is True
    assert profile["profile"]["server_version"]["server_version"] == "19.0"

    health = call_tool_json(server, "health_check", {})
    assert health["success"] is True
    assert health["server"]["tool_count"] == 21

    prompt = asyncio.run(
        server.mcp.get_prompt(
            "safe_write_review",
            {"model": "res.partner", "operation": "write"},
        )
    )
    assert "execute_approved_write" in prompt.messages[0].content.text
