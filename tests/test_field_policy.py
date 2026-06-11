"""Unit tests for the pure field-level ACL policy module."""

import json

import pytest

from odoo_mcp import field_policy
from odoo_mcp.field_policy import (
    FieldPolicy,
    FieldPolicyError,
    _parse_field_policy,
    load_field_policy,
    reset_field_policy,
)


def make(acl):
    return _parse_field_policy({"field_acl": acl})


def test_empty_policy_is_passthrough():
    policy = FieldPolicy({})
    assert policy.active() is False
    kept, redacted = policy.filter_fields("default", "res.partner", ["a", "b"])
    assert kept == ["a", "b"] and redacted == []


def test_deny_removes_listed_fields():
    policy = make({"default": {"res.partner": {"deny": ["credit_limit", "comment"]}}})
    kept, redacted = policy.filter_fields(
        "default", "res.partner", ["name", "credit_limit", "comment", "email"]
    )
    assert kept == ["name", "email"]
    assert sorted(redacted) == ["comment", "credit_limit"]


def test_allow_is_exclusive_whitelist():
    policy = make({"default": {"hr.employee": {"allow": ["name", "work_email"]}}})
    kept, redacted = policy.filter_fields(
        "default", "hr.employee", ["name", "work_email", "ssn", "salary"]
    )
    assert kept == ["name", "work_email"]
    assert sorted(redacted) == ["salary", "ssn"]


def test_id_is_never_redacted():
    policy = make({"default": {"res.partner": {"allow": ["name"]}}})
    kept, redacted = policy.filter_fields("default", "res.partner", ["id", "x"])
    assert "id" in kept
    assert redacted == ["x"]


def test_wildcard_merges_with_specific_deny():
    policy = make(
        {
            "default": {
                "res.partner": {"deny": ["credit_limit"]},
                "*": {"deny": ["message_ids"]},
            }
        }
    )
    kept, redacted = policy.filter_fields(
        "default", "res.partner", ["name", "credit_limit", "message_ids"]
    )
    assert kept == ["name"]
    assert sorted(redacted) == ["credit_limit", "message_ids"]
    # Wildcard also applies to a model with no specific rule.
    kept2, redacted2 = policy.filter_fields(
        "default", "sale.order", ["name", "message_ids"]
    )
    assert kept2 == ["name"] and redacted2 == ["message_ids"]


def test_instances_are_isolated():
    policy = make({"a": {"res.partner": {"deny": ["x"]}}})
    # Instance 'b' has no rules -> pass-through.
    kept, redacted = policy.filter_fields("b", "res.partner", ["x", "y"])
    assert kept == ["x", "y"] and redacted == []
    kept_a, redacted_a = policy.filter_fields("a", "res.partner", ["x", "y"])
    assert kept_a == ["y"] and redacted_a == ["x"]


def test_redact_record_drops_keys():
    policy = make({"default": {"res.partner": {"deny": ["credit_limit"]}}})
    record = {"id": 1, "name": "Acme", "credit_limit": 5000}
    filtered, redacted = policy.redact_record("default", "res.partner", record)
    assert filtered == {"id": 1, "name": "Acme"}
    assert redacted == ["credit_limit"]


def test_redact_records_aggregates_names():
    policy = make({"default": {"res.partner": {"deny": ["credit_limit"]}}})
    records = [
        {"id": 1, "credit_limit": 1},
        {"id": 2, "credit_limit": 2, "name": "x"},
    ]
    out, redacted = policy.redact_records("default", "res.partner", records)
    assert all("credit_limit" not in r for r in out)
    assert redacted == ["credit_limit"]


def test_check_aggregate_blocks_denied_field():
    policy = make({"default": {"account.move.line": {"deny": ["balance"]}}})
    err = policy.check_aggregate("default", "account.move.line", ["partner_id", "balance"])
    assert err is not None and "balance" in err
    assert policy.check_aggregate("default", "account.move.line", ["partner_id"]) is None


def test_restricted_fields_for_metadata_marking():
    policy = make({"default": {"res.partner": {"deny": ["credit_limit"]}}})
    restricted = policy.restricted_fields(
        "default", "res.partner", ["name", "credit_limit"]
    )
    assert restricted == ["credit_limit"]


@pytest.mark.parametrize(
    "acl",
    [
        {"default": {"res.partner": {"deny": ["a"], "allow": ["b"]}}},  # both
        {"default": {"res.partner": {}}},  # neither
        {"default": {"res.partner": {"deny": "notalist"}}},
        {"default": {"res.partner": {"deny": [1, 2]}}},
        {"default": {"res.partner": "notdict"}},
        {"default": "notdict"},
    ],
)
def test_malformed_policies_fail_closed(acl):
    with pytest.raises(FieldPolicyError):
        make(acl)


def test_field_acl_not_object_fails():
    with pytest.raises(FieldPolicyError):
        _parse_field_policy({"field_acl": ["nope"]})


def test_load_no_file_is_inactive(monkeypatch, tmp_path):
    monkeypatch.delenv("ODOO_MCP_FIELD_POLICY_FILE", raising=False)
    monkeypatch.delenv("ODOO_MCP_POLICY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    reset_field_policy()
    policy = load_field_policy()
    assert policy.active() is False


def test_load_from_dedicated_file(monkeypatch, tmp_path):
    pf = tmp_path / "field_policy.json"
    pf.write_text(json.dumps({"field_acl": {"default": {"res.partner": {"deny": ["x"]}}}}))
    monkeypatch.setenv("ODOO_MCP_FIELD_POLICY_FILE", str(pf))
    reset_field_policy()
    try:
        policy = load_field_policy()
        assert policy.active() is True
        _, redacted = policy.filter_fields("default", "res.partner", ["x", "y"])
        assert redacted == ["x"]
    finally:
        reset_field_policy()


def test_load_malformed_file_raises(monkeypatch, tmp_path):
    pf = tmp_path / "bad.json"
    pf.write_text("{ not valid json")
    monkeypatch.setenv("ODOO_MCP_FIELD_POLICY_FILE", str(pf))
    reset_field_policy()
    try:
        with pytest.raises(FieldPolicyError):
            load_field_policy()
    finally:
        reset_field_policy()


def test_posture_reports_active(monkeypatch, tmp_path):
    pf = tmp_path / "fp.json"
    pf.write_text(json.dumps({"field_acl": {"default": {"*": {"deny": ["x"]}}}}))
    monkeypatch.setenv("ODOO_MCP_FIELD_POLICY_FILE", str(pf))
    reset_field_policy()
    try:
        posture = field_policy.field_policy_posture()
        assert posture["active"] is True
        assert posture["instances_with_rules"] == 1
    finally:
        reset_field_policy()
