import importlib


def test_parse_args_defaults_to_stdio(monkeypatch):
    cli = importlib.import_module("odoo_mcp.__main__")
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("MCP_HTTP_HOST", raising=False)
    monkeypatch.delenv("MCP_HTTP_PORT", raising=False)
    monkeypatch.delenv("MCP_HTTP_PATH", raising=False)

    args = cli.parse_args([])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.path == "/mcp"


def test_cli_applies_streamable_http_runtime_settings(monkeypatch):
    cli = importlib.import_module("odoo_mcp.__main__")
    calls = []

    def fake_run(*, transport):
        calls.append(transport)

    monkeypatch.setattr(cli.mcp, "run", fake_run)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "odoo-mcp",
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
            "--path",
            "/mcp-test",
            "--log-level",
            "WARNING",
        ],
    )

    assert cli.main() == 0
    assert calls == ["streamable-http"]
    assert cli.mcp.settings.host == "0.0.0.0"
    assert cli.mcp.settings.port == 9999
    assert cli.mcp.settings.streamable_http_path == "/mcp-test"
    assert cli.mcp.settings.log_level == "WARNING"
