import asyncio
import importlib
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


def call_tool_json(server, name, arguments):
    content = asyncio.run(server.mcp.call_tool(name, arguments))
    if content and isinstance(content[0], list):
        content = content[0]
    assert isinstance(content[0], TextContent)
    return json.loads(content[0].text)


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
    }
    assert expected_tools <= tools
    assert "odoo://models" in resources
    assert {
        "odoo://model/{model_name}",
        "odoo://record/{model_name}/{record_id}",
        "odoo://search/{model_name}/{domain}",
    } <= templates


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

    try:
        server.validate_model_name("res.partner;DROP")
    except ValueError as exc:
        assert "Invalid model name" in str(exc)
    else:
        raise AssertionError("unsafe model name should be rejected")

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

    class FakeLife:
        odoo = ExplodingClient()

    class FakeRequest:
        lifespan_context = FakeLife()

    class FakeCtx:
        request_context = FakeRequest()

    assert server.diagnose_odoo_call("res.partner", "write")["success"] is True
    assert server.generate_json2_payload("res.partner", "write")["success"] is True
    assert server.upgrade_risk_report(
        methods=[{"model": "res.partner", "method": "write"}]
    )["success"] is True
    assert server.fit_gap_report(["Track contacts"])["success"] is True
    relationships = server.inspect_model_relationships(
        FakeCtx(),
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

    class FakeLife:
        odoo = FakeClient()

    class FakeRequest:
        lifespan_context = FakeLife()

    class FakeCtx:
        request_context = FakeRequest()

    report = server.inspect_model_relationships(FakeCtx(), "res.partner")

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
