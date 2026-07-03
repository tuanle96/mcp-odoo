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

TYPED_WRITE_TOOLS = {
    "preview_write": "approval",
    "validate_write": "approval_status",
    "execute_approved_write": "result",
    "chatter_post": "mode",
    "execute_method": "result",
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


def test_write_tools_expose_typed_output_schemas():
    tools = _tools_by_name()
    for name, marker_field in TYPED_WRITE_TOOLS.items():
        schema = tools[name].outputSchema
        assert schema is not None, name
        props = schema.get("properties", {})
        # Typed = more than a generic object wrapper: envelope + payload field.
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
    monkeypatch.setenv(
        "ODOO_MCP_INSTRUCTIONS_FILE", str(tmp_path / "missing.txt")
    )
    with pytest.raises(ValueError, match="unreadable"):
        server_core.load_server_instructions()


def test_load_server_instructions_truncates(monkeypatch, tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("x" * (server_core.MAX_INSTRUCTIONS_CHARS + 500), encoding="utf-8")
    monkeypatch.setenv("ODOO_MCP_INSTRUCTIONS_FILE", str(path))
    text = server_core.load_server_instructions()
    assert len(text) <= server_core.MAX_INSTRUCTIONS_CHARS + len(
        server_core.DEFAULT_SERVER_INSTRUCTIONS
    ) + 2
