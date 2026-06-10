import importlib
import time

import pytest

from odoo_mcp import odoo_client as oc


class _FlakyModels:
    """execute_kw fails with connection errors N times, then succeeds."""

    def __init__(self, failures, exc=ConnectionError("boom")):
        self.failures = failures
        self.exc = exc
        self.calls = 0

    def execute_kw(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return ["ok"]


def _client_with(models, monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RETRY_BACKOFF", "0")
    client = oc.OdooClient.__new__(oc.OdooClient)
    client.transport = "xmlrpc"
    client.db = "db"
    client.uid = 1
    client.password = "pw"
    client.lang = None
    client._models = models
    return client


def test_read_only_call_retries_connection_errors(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RETRY_ATTEMPTS", "2")
    models = _FlakyModels(failures=2)
    client = _client_with(models, monkeypatch)

    assert client._execute("res.partner", "search_read", []) == ["ok"]
    assert models.calls == 3


def test_read_only_call_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RETRY_ATTEMPTS", "1")
    models = _FlakyModels(failures=5)
    client = _client_with(models, monkeypatch)

    with pytest.raises(ConnectionError):
        client._execute("res.partner", "search_read", [])
    assert models.calls == 2


def test_write_methods_are_never_retried(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RETRY_ATTEMPTS", "5")
    models = _FlakyModels(failures=1)
    client = _client_with(models, monkeypatch)

    with pytest.raises(ConnectionError):
        client._execute("res.partner", "write", [[7], {"name": "Ada"}])
    assert models.calls == 1


def test_app_errors_are_never_retried(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RETRY_ATTEMPTS", "5")

    class _FaultModels:
        calls = 0

        def execute_kw(self, *args, **kwargs):
            self.calls += 1
            raise ValueError("AccessError: not allowed")

    models = _FaultModels()
    client = _client_with(models, monkeypatch)
    with pytest.raises(ValueError):
        client._execute("res.partner", "search_read", [])
    assert models.calls == 1


def test_retry_settings_clamped(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RETRY_ATTEMPTS", "99")
    assert oc._retry_attempts() == 5
    monkeypatch.setenv("ODOO_MCP_RETRY_ATTEMPTS", "not-a-number")
    assert oc._retry_attempts() == 2
    monkeypatch.setenv("ODOO_MCP_RETRY_BACKOFF", "-3")
    assert oc._retry_backoff_seconds() == 0.0


def test_bounded_ttl_cache_evicts_lru_and_expires():
    server = importlib.import_module("odoo_mcp.server")
    cache = server.BoundedTTLCache(max_entries=2, ttl_seconds=60)

    cache["a"] = 1
    cache["b"] = 2
    assert "a" in cache
    # Touch "a" so "b" becomes least recently used, then overflow.
    cache["c"] = 3
    assert "b" not in cache
    assert cache.get("a") == 1
    assert len(cache) == 2

    expiring = server.BoundedTTLCache(max_entries=10, ttl_seconds=0.01)
    expiring["x"] = "y"
    time.sleep(0.02)
    assert "x" not in expiring
    assert expiring.get("x", "gone") == "gone"


def test_schema_cache_settings_from_env(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "7")
    monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "12.5")
    assert server._schema_cache_settings() == (7, 12.5)
    monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "junk")
    monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "junk")
    assert server._schema_cache_settings() == (
        server.DEFAULT_SCHEMA_CACHE_MAX_ENTRIES,
        server.DEFAULT_SCHEMA_CACHE_TTL_SECONDS,
    )


def test_n_plus_one_counter_flags_hot_models():
    server = importlib.import_module("odoo_mcp.server")
    server._single_read_events.clear()

    for _ in range(server.N_PLUS_ONE_WARN_THRESHOLD):
        server.note_single_record_read("default", "res.partner")
    server.note_single_record_read("default", "sale.order")

    report = server.n_plus_one_report()
    assert report["window_seconds"] == server.N_PLUS_ONE_WINDOW_SECONDS
    hot = {item["model"] for item in report["hot_models"]}
    assert hot == {"res.partner"}
    assert report["hot_models"][0]["reads_in_window"] >= 10
    assert "search_records" in report["hot_models"][0]["recommendation"]

    runtime = server.runtime_security_report()
    assert runtime["n_plus_one"]["hot_models"]
    server._single_read_events.clear()
