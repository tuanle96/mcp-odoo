"""Tool-surface tests for the async task, knowledge, and accounting tools."""

import importlib
import time

from tests.test_batch_write import FakeCtx

from odoo_mcp.knowledge_index import reset_knowledge_store
from odoo_mcp.task_queue import reset_task_manager

server = importlib.import_module("odoo_mcp.server")


class KnowledgeClient:
    def __init__(self):
        self.search_read_calls = []

    def get_model_fields(self, model):
        return {
            "name": {"type": "char", "string": "Name", "store": True},
            "email": {"type": "char", "string": "Email", "store": True},
        }

    def search_read(self, *args, **kwargs):
        model = kwargs.get("model_name") or (args[0] if args else None)
        self.search_read_calls.append((model, kwargs))
        return [
            {"id": 1, "name": "Azure Interior", "email": "azure@example.com"},
            {"id": 2, "name": "Deco Addict", "email": "deco@example.com"},
        ]


class AccountingClient:
    def search_read(self, model, domain, fields=None, limit=None, **kwargs):
        assert model == "account.move.line"
        return [
            {
                "amount_residual": 150.0,
                "date_maturity": "2026-05-01",
                "date": "2026-05-01",
                "partner_id": [7, "Azure Interior"],
            }
        ]

    def execute_method(self, model, method, domain):
        assert method == "search_count"
        return 4


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def setup_function(_fn):
    reset_knowledge_store()
    reset_task_manager()


def test_index_then_search_knowledge(monkeypatch):
    monkeypatch.setattr(server, "resolve_instance_name", lambda name: "default")
    client = KnowledgeClient()
    outcome = server.index_knowledge(FakeCtx(client), model="res.partner")
    assert outcome["success"] is True
    assert outcome["indexed"] == 2

    found = server.search_knowledge(query="azure", model="res.partner")
    assert found["success"] is True
    assert found["results"][0]["record_id"] == 1

    stats = server.knowledge_stats()
    assert stats["total_documents"] == 2


def test_search_knowledge_without_index_is_helpful(monkeypatch):
    monkeypatch.setattr(server, "resolve_instance_name", lambda name: "default")
    result = server.search_knowledge(query="azure", model="res.partner")
    assert result["success"] is False
    assert "index_knowledge" in result["error"]


def test_index_knowledge_rejects_bad_model():
    outcome = server.index_knowledge(
        FakeCtx(KnowledgeClient()), model="res.partner; DROP TABLE"
    )
    assert outcome["success"] is False


def test_receivable_payable_aging_tool():
    report = server.receivable_payable_aging(
        FakeCtx(AccountingClient()), direction="receivable", as_of="2026-06-11"
    )
    assert report["success"] is True
    assert report["buckets"]["31-60"] == 150.0
    assert report["partners"][0]["partner"] == "Azure Interior"


def test_receivable_payable_aging_rejects_bad_direction():
    report = server.receivable_payable_aging(
        FakeCtx(AccountingClient()), direction="sideways"
    )
    assert report["success"] is False
    assert "direction" in report["error"]


def test_accounting_health_summary_tool():
    summary = server.accounting_health_summary(FakeCtx(AccountingClient()))
    assert summary["success"] is True
    assert summary["open_receivable_items"] == 4
    assert summary["draft_invoices"] == 4


def test_submit_async_index_knowledge_roundtrip(monkeypatch):
    monkeypatch.setattr(server, "resolve_instance_name", lambda name: "default")
    client = KnowledgeClient()
    submitted = server.submit_async_task(
        FakeCtx(client),
        operation="index_knowledge",
        params={"model": "res.partner"},
    )
    assert submitted["success"] is True
    task_id = submitted["task_id"]

    assert wait_for(
        lambda: server.get_async_task(task_id)["status"] == "succeeded"
    )
    status = server.get_async_task(task_id)
    assert status["result"]["indexed"] == 2

    found = server.search_knowledge(query="deco", model="res.partner")
    assert found["results"][0]["record_id"] == 2


def test_submit_async_aging(monkeypatch):
    submitted = server.submit_async_task(
        FakeCtx(AccountingClient()),
        operation="receivable_payable_aging",
        params={"direction": "payable", "as_of": "2026-06-11"},
    )
    assert submitted["success"] is True
    task_id = submitted["task_id"]
    assert wait_for(
        lambda: server.get_async_task(task_id)["status"] == "succeeded"
    )
    result = server.get_async_task(task_id)["result"]
    assert result["direction"] == "payable"


def test_submit_async_unknown_operation():
    outcome = server.submit_async_task(
        FakeCtx(KnowledgeClient()), operation="drop_database"
    )
    assert outcome["success"] is False
    assert "Allowed:" in outcome["error"]


def test_cancel_and_list_async_tasks(monkeypatch):
    monkeypatch.setattr(server, "resolve_instance_name", lambda name: "default")

    class SlowClient(KnowledgeClient):
        def search_read(self, *args, **kwargs):  # noqa: D102
            time.sleep(0.2)
            return KnowledgeClient.search_read(self, *args, **kwargs)

    first = server.submit_async_task(
        FakeCtx(SlowClient()),
        operation="index_knowledge",
        params={"model": "res.partner"},
    )
    listed = server.list_async_tasks()
    assert listed["success"] is True
    assert any(item["task_id"] == first["task_id"] for item in listed["tasks"])

    cancel_unknown = server.cancel_async_task("nonexistent")
    assert cancel_unknown["success"] is False
    assert wait_for(
        lambda: server.get_async_task(first["task_id"])["status"]
        in ("succeeded", "cancelled")
    )


def test_async_tools_have_safe_annotations():
    import asyncio

    tools = {
        tool.name: tool.model_dump()
        for tool in asyncio.run(server.mcp.list_tools())
    }
    for name in (
        "submit_async_task",
        "get_async_task",
        "cancel_async_task",
        "list_async_tasks",
        "search_knowledge",
        "knowledge_stats",
    ):
        assert tools[name]["annotations"]["destructiveHint"] is False
    for name in ("index_knowledge", "receivable_payable_aging",
                 "accounting_health_summary"):
        assert tools[name]["annotations"]["readOnlyHint"] is True


def test_rate_limit_block_mode_on_search_records(monkeypatch):
    from odoo_mcp.rate_limit import reset_rate_tracker

    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "block")
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MAX_CALLS", "1")
    reset_rate_tracker()
    try:
        client = KnowledgeClient()
        first = server.search_records(FakeCtx(client), model="res.partner")
        assert first["success"] is True
        second = server.search_records(FakeCtx(client), model="res.partner")
        assert second["success"] is False
        assert second.get("rate_limited") is True
    finally:
        reset_rate_tracker()


def test_health_check_includes_rate_report(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "off")
    health = server.health_check()
    assert health["rate_limits"] == {"mode": "off"}
