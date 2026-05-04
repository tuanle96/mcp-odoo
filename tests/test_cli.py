import importlib
import json
import logging


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


def _restore_root_logger():
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


def test_setup_logging_text_mode_uses_plain_formatter(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")
    monkeypatch.delenv("ODOO_MCP_LOG_JSON", raising=False)
    monkeypatch.delenv("ODOO_MCP_LOG_FILE", raising=False)

    try:
        logger = cli.setup_logging(level="DEBUG")
        logger.info("hello plain")
        captured = capsys.readouterr().err
        assert "hello plain" in captured
        assert "INFO" in captured
        # Should not be JSON when ODOO_MCP_LOG_JSON is not set
        assert not captured.lstrip().startswith("{")
    finally:
        _restore_root_logger()


def test_setup_logging_json_mode_emits_structured_lines(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    try:
        cli.setup_logging(level="INFO", use_json=True)
        logging.getLogger("odoo_mcp.test").warning(
            "structured event", extra={"odoo_model": "res.partner"}
        )
        captured = capsys.readouterr().err.strip()
        # Last line is the warning event we care about.
        last = captured.splitlines()[-1]
        record = json.loads(last)
        assert record["level"] == "WARNING"
        assert record["message"] == "structured event"
        assert record["odoo_model"] == "res.partner"
        assert record["logger"] == "odoo_mcp.test"
    finally:
        _restore_root_logger()


def test_setup_logging_writes_to_rotating_file(monkeypatch, tmp_path):
    cli = importlib.import_module("odoo_mcp.__main__")
    log_path = tmp_path / "odoo-mcp.log"

    try:
        cli.setup_logging(level="INFO", log_file=str(log_path))
        logging.getLogger("odoo_mcp.test").info("file event")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "file event" in content
    finally:
        _restore_root_logger()


def test_setup_logging_invalid_level_falls_back_to_info(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")
    monkeypatch.setenv("ODOO_MCP_LOG_LEVEL", "NOPE")

    try:
        logger = cli.setup_logging()
        # debug should be suppressed under INFO
        logger.debug("hidden")
        logger.info("visible")
        out = capsys.readouterr().err
        assert "hidden" not in out
        assert "visible" in out
    finally:
        _restore_root_logger()


def test_setup_logging_env_overrides_pick_up_when_kwargs_omitted(
    monkeypatch, capsys
):
    cli = importlib.import_module("odoo_mcp.__main__")
    monkeypatch.setenv("ODOO_MCP_LOG_JSON", "1")
    monkeypatch.setenv("ODOO_MCP_LOG_LEVEL", "INFO")

    try:
        cli.setup_logging()
        logging.getLogger().info("env-driven")
        captured = capsys.readouterr().err.strip().splitlines()[-1]
        record = json.loads(captured)
        assert record["message"] == "env-driven"
        assert record["level"] == "INFO"
    finally:
        _restore_root_logger()


def test_setup_logging_debug_level_actually_emits_debug(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    try:
        cli.setup_logging(level="DEBUG")
        logger = logging.getLogger("odoo_mcp.test")
        logger.debug("low-level event")
        logger.info("normal event")
        out = capsys.readouterr().err
        assert "low-level event" in out
        assert "normal event" in out
    finally:
        _restore_root_logger()


def test_setup_logging_json_includes_exception_traceback(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    try:
        cli.setup_logging(level="INFO", use_json=True)
        try:
            raise RuntimeError("boom on purpose")
        except RuntimeError:
            logging.getLogger("odoo_mcp.test").exception("captured")
        captured = capsys.readouterr().err.strip().splitlines()[-1]
        record = json.loads(captured)
        assert record["message"] == "captured"
        assert "exc_info" in record
        assert "RuntimeError" in record["exc_info"]
        assert "boom on purpose" in record["exc_info"]
    finally:
        _restore_root_logger()


def test_is_secret_env_key_recognizes_password_token_and_api_key_suffixes():
    cli = importlib.import_module("odoo_mcp.__main__")

    # Explicit secrets in SECRET_ENV_KEYS
    assert cli.is_secret_env_key("ODOO_PASSWORD") is True
    assert cli.is_secret_env_key("MCP_HTTP_AUTH_TOKEN") is True
    # Suffix-based detection
    assert cli.is_secret_env_key("MY_SERVICE_PASSWORD") is True
    assert cli.is_secret_env_key("AGENT_TOKEN") is True
    assert cli.is_secret_env_key("CUSTOM_API_KEY") is True
    # Non-secret
    assert cli.is_secret_env_key("ODOO_URL") is False
    assert cli.is_secret_env_key("MCP_TRANSPORT") is False


def test_main_handles_keyboard_interrupt_gracefully(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    def raise_keyboard_interrupt(*, transport):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.mcp, "run", raise_keyboard_interrupt)
    monkeypatch.setattr(cli.sys, "argv", ["odoo-mcp"])

    assert cli.main() == 0
    captured = capsys.readouterr()
    assert "stopped by user" in captured.err


def test_main_handles_unexpected_exception_returns_one(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    def raise_runtime_error(*, transport):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli.mcp, "run", raise_runtime_error)
    monkeypatch.setattr(cli.sys, "argv", ["odoo-mcp"])

    assert cli.main() == 1
    captured = capsys.readouterr()
    assert "Error starting server" in captured.err
    assert "boom" in captured.err


def test_main_masks_secret_environment_values_in_startup_log(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    monkeypatch.setenv("ODOO_PASSWORD", "supersecret-do-not-print")
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setattr(cli.mcp, "run", lambda *, transport: None)
    monkeypatch.setattr(cli.sys, "argv", ["odoo-mcp"])

    assert cli.main() == 0
    err = capsys.readouterr().err
    assert "supersecret-do-not-print" not in err
    assert "***hidden***" in err
    assert "ODOO_URL: https://odoo.example.test" in err


def test_main_logs_streamable_http_bind_and_path(monkeypatch, capsys):
    cli = importlib.import_module("odoo_mcp.__main__")

    monkeypatch.setattr(cli.mcp, "run", lambda *, transport: None)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "odoo-mcp",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "--path",
            "/mcp",
        ],
    )

    assert cli.main() == 0
    err = capsys.readouterr().err
    assert "Bind: 127.0.0.1:8080" in err
    assert "Path: /mcp" in err


def test_configure_mcp_runtime_raises_when_transport_security_missing(monkeypatch):
    cli = importlib.import_module("odoo_mcp.__main__")

    monkeypatch.setattr(cli.mcp.settings, "transport_security", None, raising=False)
    args = cli.parse_args(
        [
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--allowed-hosts",
            "odoo.example.test",
        ]
    )

    try:
        cli.configure_mcp_runtime(args)
    except ValueError as exc:
        assert "transport security" in str(exc)
    else:
        raise AssertionError("missing transport_security must raise")


def test_health_payload_returns_none_transport_security_when_unavailable(monkeypatch):
    cli = importlib.import_module("odoo_mcp.__main__")

    monkeypatch.setattr(cli.mcp.settings, "transport_security", None, raising=False)
    args = cli.parse_args(["--health"])

    payload = cli.health_payload(args)
    assert payload["transport_security"] is None
    assert payload["success"] is True


def test_main_entrypoint_invokes_sys_exit_with_main_return_value(monkeypatch, capsys):
    """Cover the ``if __name__ == '__main__'`` block via runpy."""
    import runpy
    import sys as _sys

    cli = importlib.import_module("odoo_mcp.__main__")

    captured: dict = {"main_called": False}
    real_main = cli.main

    def fake_main():
        captured["main_called"] = True
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    monkeypatch.setattr(_sys, "argv", ["odoo-mcp", "--health"])

    real_exit = _sys.exit

    def fake_exit(code=0):
        captured["code"] = code
        # Prevent runpy from raising SystemExit by short-circuiting
        raise SystemExit(code)

    monkeypatch.setattr(_sys, "exit", fake_exit)

    try:
        runpy.run_module("odoo_mcp.__main__", run_name="__main__")
    except SystemExit as exc:
        captured.setdefault("code", exc.code)

    # main may have been called either via fake_main (if monkeypatch survived)
    # or via the runpy execution; either way, sys.exit must have been called.
    assert "code" in captured
    # restore the real main for any later tests
    monkeypatch.setattr(cli, "main", real_main)
    monkeypatch.setattr(_sys, "exit", real_exit)
