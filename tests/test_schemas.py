"""Typed per-tool output schemas + server instructions loading."""

import asyncio

import pytest

from odoo_mcp import schemas, server, server_core

TYPED_READ_TOOLS = {
    "get_odoo_profile": "profile",
    "schema_catalog": "result",
    "health_check": "server",
    "list_instances": "instances",
    "list_models": "result",
    "get_model_fields": "result",
    "search_records": "result",
    "read_record": "result",
    "read_attachment": "attachment",
    "aggregate_records": "rows",
}

DESCRIBED_INPUT_TOOLS = {
    "accounting_health_across_instances": {
        "direction",
        "as_of",
        "top_partners",
        "instances",
    },
    "execute_method": {"model", "method", "args", "kwargs", "instance"},
    "get_odoo_profile": {"include_modules", "module_limit", "instance"},
    "diagnose_access": {
        "model",
        "operation",
        "domain",
        "record_ids",
        "expected_count",
        "include_rules",
        "observed_error",
        "limit",
        "instance",
    },
    "schema_catalog": {
        "query",
        "models",
        "include_fields",
        "refresh",
        "limit",
        "instance",
    },
    "search_holidays": {"start_date", "end_date", "employee_id", "instance"},
    "business_pack_report": {"pack", "use_live_metadata", "instance"},
    "diagnose_odoo_call": {
        "model",
        "method",
        "args",
        "kwargs",
        "transport",
        "target_version",
        "observed_error",
        "include_debug",
        "metadata",
        "use_live_metadata",
    },
    "receivable_payable_aging": {
        "direction",
        "as_of",
        "top_partners",
        "limit",
        "instance",
    },
    "upgrade_risk_report": {
        "source_version",
        "target_version",
        "modules",
        "methods",
        "source_findings",
        "observed_errors",
        "use_live_metadata",
        "include_debug",
    },
}

TYPED_DOMAIN_TOOLS = {
    "build_domain": "domain",
    "index_knowledge": "indexed",
    "search_knowledge": "results",
    "knowledge_stats": "indexes",
    "receivable_payable_aging": "buckets",
    "accounting_health_summary": "open_receivable_items",
    "submit_async_task": "task_id",
    "get_async_task": "task_id",
    "cancel_async_task": "task_id",
    "list_async_tasks": "tasks",
    "search_across_instances": "merged",
    "aggregate_across_instances": "combined_measures",
    "accounting_health_across_instances": "combined_buckets",
}


def _tools_by_name():
    tools = asyncio.run(server.mcp.list_tools())
    return {tool.name: tool for tool in tools}


def test_read_tools_expose_typed_output_schemas():
    tools = _tools_by_name()
    for name, marker_field in TYPED_READ_TOOLS.items():
        schema = tools[name].outputSchema
        assert schema is not None, name
        props = schema.get("properties", {})
        # Typed = more than a generic object wrapper: envelope + payload field.
        assert "success" in props, name
        assert "error" in props, name
        assert marker_field in props, (name, sorted(props))


def test_target_tools_describe_every_input_parameter_and_hide_context():
    tools = _tools_by_name()
    for name, expected_parameters in DESCRIBED_INPUT_TOOLS.items():
        properties = tools[name].inputSchema.get("properties", {})
        assert set(properties) == expected_parameters, name
        assert "ctx" not in properties, name
        for parameter, schema in properties.items():
            assert schema.get("description", "").strip(), (name, parameter)


def test_domain_tools_expose_typed_output_schemas():
    tools = _tools_by_name()
    for name, marker_field in TYPED_DOMAIN_TOOLS.items():
        schema = tools[name].outputSchema
        assert schema is not None, name
        props = schema.get("properties", {})
        assert "success" in props, name
        assert "error" in props, name
        assert marker_field in props, (name, sorted(props))


def test_envelope_models_accept_success_and_error_shapes():
    ok = schemas.SearchRecordsResponse.model_validate(
        {
            "success": True,
            "count": 1,
            "result": [{"id": 1, "name": "Azure"}],
            "smart_fields_applied": True,
            "fields_used": ["id", "name"],
            "redacted_fields": ["email"],
        }
    )
    assert ok.count == 1
    err = schemas.SearchRecordsResponse.model_validate(
        {"success": False, "error": "boom"}
    )
    assert err.error == "boom"
    # Rate-limit refusals and future fields must keep validating.
    extra = schemas.SearchRecordsResponse.model_validate(
        {"success": False, "error": "rate limited", "rate_limit": {"tool": "x"}}
    )
    assert extra.success is False


def test_build_domain_schema_accepts_runtime_payload():
    response = schemas.BuildDomainResponse.model_validate(
        {
            "success": True,
            "tool": "build_domain",
            "domain": [["name", "ilike", "azure"]],
            "conditions": [["name", "ilike", "azure"]],
            "issues": [],
            "metadata_used": {"fields_get": False},
        }
    )
    assert response.domain == [["name", "ilike", "azure"]]


def test_accounting_health_schema_accepts_runtime_payload():
    response = schemas.AccountingHealthSummaryResponse.model_validate(
        {
            "success": True,
            "open_receivable_items": 4,
            "open_payable_items": 3,
            "draft_invoices": 2,
        }
    )
    assert response.open_receivable_items == 4
    assert response.open_payable_items == 3
    assert response.draft_invoices == 2


def test_aging_schema_advertises_runtime_payload_fields():
    properties = schemas.ReceivablePayableAgingResponse.model_json_schema()[
        "properties"
    ]
    assert {"partners", "line_count", "skipped_lines"} <= properties.keys()
    assert "top_partners" not in properties
    assert "currency" not in properties


def test_load_server_instructions_default(monkeypatch):
    monkeypatch.delenv("ODOO_MCP_INSTRUCTIONS_FILE", raising=False)
    assert (
        server_core.load_server_instructions()
        == server_core.DEFAULT_SERVER_INSTRUCTIONS
    )


def test_load_server_instructions_appends_file(tmp_path, monkeypatch):
    path = tmp_path / "instructions.txt"
    path.write_text("Fiscal year starts in July.\n", encoding="utf-8")
    monkeypatch.setenv("ODOO_MCP_INSTRUCTIONS_FILE", str(path))
    text = server_core.load_server_instructions()
    assert text.startswith(server_core.DEFAULT_SERVER_INSTRUCTIONS)
    assert "Fiscal year starts in July." in text


def test_load_server_instructions_unreadable_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("ODOO_MCP_INSTRUCTIONS_FILE", str(tmp_path / "missing.txt"))
    with pytest.raises(ValueError, match="unreadable"):
        server_core.load_server_instructions()


def test_load_server_instructions_truncates(monkeypatch, tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("x" * (server_core.MAX_INSTRUCTIONS_CHARS + 500), encoding="utf-8")
    monkeypatch.setenv("ODOO_MCP_INSTRUCTIONS_FILE", str(path))
    text = server_core.load_server_instructions()
    assert (
        len(text)
        <= server_core.MAX_INSTRUCTIONS_CHARS
        + len(server_core.DEFAULT_SERVER_INSTRUCTIONS)
        + 2
    )
