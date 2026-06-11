"""Tests for cross-instance fan-out: pure selection/merge logic + tool surface."""

import importlib

from odoo_mcp.cross_instance import (
    combine_aggregate_rows,
    combine_bucket_reports,
    envelope,
    parse_instances_meta,
    select_instances,
    tag_and_merge,
)
from odoo_mcp.field_policy import reset_field_policy

server = importlib.import_module("odoo_mcp.server")

SUMMARY = {
    "acme": {"tags": ["eu", "vip"], "cross_instance": True},
    "globex": {"tags": ["us"], "cross_instance": True},
    "secret": {"tags": ["eu"], "cross_instance": False},
}


# --- pure logic ------------------------------------------------------------


def test_parse_meta_defaults():
    metas = parse_instances_meta({"x": {}})
    assert metas["x"].cross_instance is True
    assert metas["x"].tags == frozenset()


def test_select_all_excludes_opt_out():
    metas = parse_instances_meta(SUMMARY)
    sel = select_instances("all", metas)
    assert sel.selected == ["acme", "globex"]
    assert "secret" not in sel.selected


def test_select_none_is_all():
    metas = parse_instances_meta(SUMMARY)
    assert select_instances(None, metas).selected == ["acme", "globex"]


def test_select_by_tag():
    metas = parse_instances_meta(SUMMARY)
    sel = select_instances({"tags": ["eu"]}, metas)
    # 'secret' is eu but opted out, so excluded.
    assert sel.selected == ["acme"]


def test_select_explicit_list_reports_opt_out_and_unknown():
    metas = parse_instances_meta(SUMMARY)
    sel = select_instances(["acme", "secret", "ghost"], metas)
    assert sel.selected == ["acme"]
    assert sel.skipped_opt_out == ["secret"]
    assert sel.unknown == ["ghost"]


def test_tag_and_merge_attributes_rows():
    merged = tag_and_merge(
        {"acme": [{"id": 1}], "globex": [{"id": 2}, {"id": 3}]}
    )
    assert {r["_instance"] for r in merged} == {"acme", "globex"}
    assert len(merged) == 3


def test_combine_aggregate_rows_sums_additive():
    rows = {
        "acme": [{"amount": 100.0, "__count": 2}],
        "globex": [{"amount": 50.0, "__count": 1}, {"amount": 25.0, "__count": 1}],
    }
    combined = combine_aggregate_rows(rows, ["amount"])
    assert combined["combined_measures"]["amount"] == 175.0
    assert combined["combined_count"] == 4


def test_combine_bucket_reports_sums_buckets():
    reports = {
        "acme": {"buckets": {"90+": 100.0, "1-30": 10.0}, "total_outstanding": 110.0},
        "globex": {"buckets": {"90+": 50.0}, "total_outstanding": 50.0},
    }
    combined = combine_bucket_reports(reports)
    assert combined["combined_buckets"]["90+"] == 150.0
    assert combined["combined_total_outstanding"] == 160.0


def test_envelope_reports_skips_and_unknowns():
    from odoo_mcp.cross_instance import Selection

    sel = Selection(["a"], ["b"], ["c"])
    env = envelope({"a": {"count": 1}}, {}, sel)
    assert env["skipped_opt_out"] == ["b"]
    assert env["unknown_instances"] == ["c"]


# --- tool surface (fan-out with fake multi-instance app context) -----------


class FanoutClient:
    def __init__(self, name, down=False):
        self.name = name
        self.down = down

    def search_read(self, model, domain, fields=None, limit=None):
        if self.down:
            raise ConnectionError("instance unreachable")
        return [{"id": 1, "name": f"{self.name}-partner", "credit_limit": 999}]

    def execute_method(self, model, method, *args):
        return [{"partner_id": (1, "P"), "amount_residual": 200.0, "__count": 3}]


class FanoutAppContext:
    """App context whose get_client returns a per-instance fake client."""

    def __init__(self, clients):
        self._clients = clients

    def get_client(self, instance=None):
        name = instance or "acme"
        return name, self._clients[name]


class FanoutCtx:
    def __init__(self, app_context):
        self.request_context = type(
            "R", (), {"lifespan_context": app_context}
        )()


def _ctx(down=None):
    down = down or set()
    clients = {
        name: FanoutClient(name, down=name in down) for name in ("acme", "globex")
    }
    return FanoutCtx(FanoutAppContext(clients))


def setup_function(_fn):
    reset_field_policy()


def teardown_function(_fn):
    reset_field_policy()


def _patch_instances(monkeypatch, summary=None):
    summary = summary or {
        "acme": {"tags": ["eu"], "cross_instance": True},
        "globex": {"tags": ["us"], "cross_instance": True},
    }
    monkeypatch.setattr(server, "list_configured_instances", lambda: summary)


def test_search_across_instances_merges(monkeypatch):
    _patch_instances(monkeypatch)
    out = server.search_across_instances(_ctx(), model="res.partner")
    assert out["success"] is True
    assert out["merged_count"] == 2
    assert {r["_instance"] for r in out["merged"]} == {"acme", "globex"}
    assert out["errors"] == {}


def test_search_partial_failure(monkeypatch):
    _patch_instances(monkeypatch)
    out = server.search_across_instances(_ctx(down={"globex"}), model="res.partner")
    assert out["success"] is True
    assert "acme" in out["results"]
    assert "globex" in out["errors"]
    assert "unreachable" in out["errors"]["globex"]
    assert out["merged_count"] == 1


def test_search_applies_field_acl_per_instance(monkeypatch, tmp_path):
    import json

    pf = tmp_path / "fp.json"
    pf.write_text(
        json.dumps({"field_acl": {"acme": {"res.partner": {"deny": ["credit_limit"]}}}})
    )
    monkeypatch.setenv("ODOO_MCP_FIELD_POLICY_FILE", str(pf))
    reset_field_policy()
    _patch_instances(monkeypatch)
    out = server.search_across_instances(_ctx(), model="res.partner")
    by_instance = {r["_instance"]: r for r in out["merged"]}
    assert "credit_limit" not in by_instance["acme"]  # denied for acme
    assert "credit_limit" in by_instance["globex"]  # globex unaffected


def test_aggregate_across_instances_combines(monkeypatch):
    _patch_instances(monkeypatch)
    out = server.aggregate_across_instances(
        _ctx(),
        model="account.move.line",
        group_by=["partner_id"],
        measures=["amount_residual:sum"],
    )
    assert out["success"] is True
    assert out["combined_measures"]["amount_residual"] == 400.0  # 200 x 2 instances
    assert out["combined_count"] == 6


def test_opt_out_instance_excluded(monkeypatch):
    _patch_instances(
        monkeypatch,
        {
            "acme": {"tags": [], "cross_instance": True},
            "globex": {"tags": [], "cross_instance": False},
        },
    )
    out = server.search_across_instances(_ctx(), model="res.partner")
    assert list(out["results"]) == ["acme"]
    assert "globex" not in out["results"] and "globex" not in out["errors"]
