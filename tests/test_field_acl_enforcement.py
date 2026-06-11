"""End-to-end field ACL redaction across the read tools and resources."""

import importlib
import json

import pytest

from tests.test_batch_write import FakeCtx

from odoo_mcp.field_policy import reset_field_policy

server = importlib.import_module("odoo_mcp.server")


class PolicyClient:
    """Minimal Odoo client returning records with a sensitive field."""

    def search_read(self, *args, **kwargs):
        return [
            {"id": 1, "name": "Azure", "credit_limit": 5000, "email": "a@x.io"},
            {"id": 2, "name": "Deco", "credit_limit": 9000, "email": "d@x.io"},
        ]

    def read_records(self, model, ids, fields=None):
        return [{"id": ids[0], "name": "Azure", "credit_limit": 5000}]

    def get_model_fields(self, model):
        return {
            "name": {"type": "char", "string": "Name"},
            "credit_limit": {"type": "monetary", "string": "Credit Limit"},
            "email": {"type": "char", "string": "Email"},
        }

    def get_models(self):
        return {"model_names": ["res.partner"]}


@pytest.fixture
def deny_credit_limit(monkeypatch, tmp_path):
    pf = tmp_path / "fp.json"
    pf.write_text(
        json.dumps(
            {"field_acl": {"default": {"res.partner": {"deny": ["credit_limit"]}}}}
        )
    )
    monkeypatch.setenv("ODOO_MCP_FIELD_POLICY_FILE", str(pf))
    monkeypatch.setattr(server, "resolve_instance_name", lambda name: name or "default")
    monkeypatch.setattr(server, "resolve_default_instance_name", lambda: "default")
    reset_field_policy()
    yield
    reset_field_policy()


def test_search_records_redacts_denied_field(deny_credit_limit):
    out = server.search_records(FakeCtx(PolicyClient()), model="res.partner")
    assert out["success"] is True
    assert all("credit_limit" not in row for row in out["result"])
    assert out["redacted_fields"] == ["credit_limit"]
    assert all("name" in row for row in out["result"])


def test_read_record_redacts_denied_field(deny_credit_limit):
    out = server.read_record(FakeCtx(PolicyClient()), model="res.partner", record_id=1)
    assert out["success"] is True
    assert "credit_limit" not in out["result"]
    assert out["redacted_fields"] == ["credit_limit"]


def test_aggregate_rejects_denied_field(deny_credit_limit):
    out = server.aggregate_records(
        FakeCtx(PolicyClient()),
        model="res.partner",
        group_by=["country_id"],
        measures=["credit_limit:sum"],
    )
    assert out["success"] is False
    assert "credit_limit" in out["error"]


def test_aggregate_rejects_denied_groupby(deny_credit_limit):
    out = server.aggregate_records(
        FakeCtx(PolicyClient()),
        model="res.partner",
        group_by=["credit_limit"],
        measures=["__count:count"] if False else None,
    )
    assert out["success"] is False
    assert "credit_limit" in out["error"]


def test_get_model_fields_marks_restricted(deny_credit_limit):
    out = server.get_model_fields(FakeCtx(PolicyClient()), model="res.partner")
    assert out["success"] is True
    assert out["result"]["credit_limit"]["access"] == "restricted"
    assert "access" not in out["result"]["name"]
    assert out["restricted_fields"] == ["credit_limit"]


def test_resource_get_record_redacts(deny_credit_limit, monkeypatch):
    monkeypatch.setattr(server, "get_odoo_client", lambda: PolicyClient())
    payload = json.loads(server.get_record("res.partner", "1"))
    assert "credit_limit" not in payload
    assert payload["_redacted_fields"] == ["credit_limit"]


def test_no_policy_is_byte_identical():
    reset_field_policy()
    import os

    os.environ.pop("ODOO_MCP_FIELD_POLICY_FILE", None)
    os.environ.pop("ODOO_MCP_POLICY_FILE", None)
    reset_field_policy()
    out = server.search_records(FakeCtx(PolicyClient()), model="res.partner")
    assert "redacted_fields" not in out
    assert all("credit_limit" in row for row in out["result"])


class _EchoLife:
    """Lifespan double whose get_client echoes the requested instance name."""

    def __init__(self, odoo):
        self.odoo = odoo
        self.schema_cache = {}
        self.write_approvals = {}

    def get_client(self, instance=None):
        return (instance or "default"), self.odoo


class _EchoCtx:
    def __init__(self, odoo):
        self.request_context = type(
            "R", (), {"lifespan_context": _EchoLife(odoo)}
        )()


def test_instance_isolation(monkeypatch, tmp_path):
    pf = tmp_path / "fp.json"
    # Policy only for instance 'alpha'; 'default' is unaffected.
    pf.write_text(
        json.dumps(
            {"field_acl": {"alpha": {"res.partner": {"deny": ["credit_limit"]}}}}
        )
    )
    monkeypatch.setenv("ODOO_MCP_FIELD_POLICY_FILE", str(pf))
    monkeypatch.setattr(server, "resolve_instance_name", lambda name: name or "default")
    monkeypatch.setattr(server, "resolve_default_instance_name", lambda: "default")
    reset_field_policy()
    try:
        default_out = server.search_records(
            _EchoCtx(PolicyClient()), model="res.partner"
        )
        assert "redacted_fields" not in default_out
        alpha_out = server.search_records(
            _EchoCtx(PolicyClient()), model="res.partner", instance="alpha"
        )
        assert alpha_out["redacted_fields"] == ["credit_limit"]
    finally:
        reset_field_policy()


def test_health_check_reports_field_acl(deny_credit_limit):
    health = server.health_check()
    assert health["runtime"]["field_acl"]["active"] is True
    assert health["runtime"]["field_acl"]["instances_with_rules"] == 1
