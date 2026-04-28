import importlib
import json


def test_parse_args_defaults_to_stdio(monkeypatch):
    cli = importlib.import_module("odoo_mcp.__main__")
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("MCP_HTTP_HOST", raising=False)
    monkeypatch.delenv("MCP_HTTP_PORT", raising=False)
    monkeypatch.delenv("MCP_HTTP_PATH", raising=False)
    monkeypatch.delenv("MCP_ALLOW_REMOTE_HTTP", raising=False)

    args = cli.parse_args([])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.path == "/mcp"
    assert args.allow_remote_http is False


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
            "--allow-remote-http",
            "--allowed-hosts",
            "odoo.example.test,127.0.0.1:*",
            "--allowed-origins",
            "https://agent.example.test",
        ],
    )

    assert cli.main() == 0
    assert calls == ["streamable-http"]
    assert cli.mcp.settings.host == "0.0.0.0"
    assert cli.mcp.settings.port == 9999
    assert cli.mcp.settings.streamable_http_path == "/mcp-test"
    assert cli.mcp.settings.log_level == "WARNING"
    assert cli.mcp.settings.transport_security.allowed_hosts == [
        "odoo.example.test",
        "127.0.0.1:*",
    ]
    assert cli.mcp.settings.transport_security.allowed_origins == [
        "https://agent.example.test"
    ]


def test_cli_rejects_remote_http_bind_without_explicit_opt_in():
    cli = importlib.import_module("odoo_mcp.__main__")
    args = cli.parse_args(
        ["--transport", "streamable-http", "--host", "0.0.0.0"]
    )

    try:
        cli.configure_mcp_runtime(args)
    except ValueError as exc:
        assert "local hosts only" in str(exc)
    else:
        raise AssertionError("remote HTTP bind should require explicit opt-in")


def test_cli_health_prints_non_secret_runtime_json(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    monkeypatch.setenv("ODOO_PASSWORD", "secret")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "odoo-mcp",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--health",
        ],
    )

    assert cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["transport"] == "streamable-http"
    assert "secret" not in json.dumps(payload)
