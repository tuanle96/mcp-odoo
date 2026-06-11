"""Tests for the interactive `odoo-mcp --setup` wizard (network-free)."""

from __future__ import annotations

import json
import os
import stat
import sys

from odoo_mcp import setup_wizard


def make_input(answers):
    """Return an input() stand-in yielding canned answers in order."""
    iterator = iter(answers)

    def fake_input(prompt=""):
        return next(iterator)

    return fake_input


def test_prompt_value_returns_default_on_blank():
    reader = make_input(["", "value"])
    assert setup_wizard.prompt_value("x", default="dflt", input_func=reader) == "dflt"
    assert setup_wizard.prompt_value("x", input_func=reader) == "value"


def test_prompt_value_reprompts_until_non_empty(capsys):
    reader = make_input(["", "", "ok"])
    assert setup_wizard.prompt_value("x", input_func=reader) == "ok"


def test_collect_connection_details_xmlrpc_default():
    reader = make_input(
        ["https://demo.odoo.com", "demo-db", "admin@example.com", "secret", ""]
    )
    details = setup_wizard.collect_connection_details(input_func=reader)
    assert details == {
        "url": "https://demo.odoo.com",
        "db": "demo-db",
        "username": "admin@example.com",
        "password": "secret",
    }


def test_collect_connection_details_json2_sets_api_key():
    reader = make_input(
        ["https://demo.odoo.com", "demo-db", "admin@example.com", "key-1", "json2"]
    )
    details = setup_wizard.collect_connection_details(input_func=reader)
    assert details["transport"] == "json2"
    assert details["api_key"] == "key-1"


def test_test_connection_reports_failure(monkeypatch):
    class ExplodingClient:
        def __init__(self, **kwargs):
            raise ConnectionError("no route to host")

    monkeypatch.setattr(
        "odoo_mcp.odoo_client.OdooClient", ExplodingClient
    )
    ok, message = setup_wizard.test_connection(
        {"url": "u", "db": "d", "username": "n", "password": "p"}
    )
    assert ok is False
    assert "no route to host" in message


def test_test_connection_reports_version(monkeypatch):
    class HappyClient:
        def __init__(self, **kwargs):
            pass

        def get_server_version(self):
            return {"server_version": "19.0"}

    monkeypatch.setattr("odoo_mcp.odoo_client.OdooClient", HappyClient)
    ok, message = setup_wizard.test_connection(
        {"url": "u", "db": "d", "username": "n", "password": "p"}
    )
    assert ok is True
    assert "19.0" in message


def test_write_config_creates_file_with_owner_only_permissions(tmp_path):
    target = tmp_path / "nested" / "config.json"
    written = setup_wizard.write_config(
        {"url": "u", "db": "d", "username": "n", "password": "p"}, target
    )
    assert written == target
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["url"] == "u"
    if sys.platform != "win32":
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o600


def test_client_snippets_mention_config_path_and_write_gate(tmp_path):
    snippet = setup_wizard.client_snippets(tmp_path / "config.json")
    assert "ODOO_CONFIG_FILE" in snippet
    assert "claude mcp add odoo" in snippet
    assert "ODOO_MCP_ENABLE_WRITES" in snippet


def test_run_setup_happy_path_writes_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        setup_wizard,
        "test_connection",
        lambda details: (True, "Connected (Odoo 19.0)."),
    )
    target = tmp_path / "config.json"
    reader = make_input(
        [
            "https://demo.odoo.com",
            "demo-db",
            "admin@example.com",
            "secret",
            "",  # transport default
            str(target),
        ]
    )
    assert setup_wizard.run_setup(input_func=reader) == 0
    assert target.exists()
    out = capsys.readouterr().out
    assert "claude mcp add odoo" in out


def test_run_setup_aborts_when_connection_fails_and_user_declines(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        setup_wizard, "test_connection", lambda details: (False, "boom")
    )
    reader = make_input(
        [
            "https://demo.odoo.com",
            "demo-db",
            "admin@example.com",
            "secret",
            "",  # transport default
            "n",  # do not save anyway
        ]
    )
    assert setup_wizard.run_setup(input_func=reader) == 1
    assert not (tmp_path / "config.json").exists()


def test_run_setup_cancelled_by_eof_returns_1():
    def eof_input(prompt=""):
        raise EOFError

    assert setup_wizard.run_setup(input_func=eof_input) == 1


def test_cli_setup_flag_invokes_wizard(monkeypatch):
    from odoo_mcp import __main__ as cli

    called = {}

    def fake_run_setup():
        called["yes"] = True
        return 0

    monkeypatch.setattr("odoo_mcp.setup_wizard.run_setup", fake_run_setup)
    monkeypatch.setattr(sys, "argv", ["odoo-mcp", "--setup"])
    assert cli.main() == 0
    assert called == {"yes": True}
