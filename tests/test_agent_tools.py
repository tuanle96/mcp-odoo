"""Unit tests for pure agent_tools helpers (network-free)."""

from __future__ import annotations

from pathlib import Path

from odoo_mcp import agent_tools


def _meta(field_type: str = "char", **extra) -> dict:
    return {"type": field_type, **extra}


def test_select_smart_fields_drops_audit_and_messaging_columns():
    fields = {
        "id": _meta("integer"),
        "name": _meta(),
        "create_uid": _meta("many2one"),
        "create_date": _meta("datetime"),
        "write_uid": _meta("many2one"),
        "write_date": _meta("datetime"),
        "__last_update": _meta("datetime"),
        "message_ids": _meta("one2many"),
        "message_follower_ids": _meta("one2many"),
        "activity_ids": _meta("one2many"),
        "website_message_ids": _meta("one2many"),
    }
    selected = agent_tools.select_smart_fields(fields)
    assert "id" in selected
    assert "name" in selected
    for noisy in (
        "create_uid",
        "create_date",
        "write_uid",
        "write_date",
        "__last_update",
        "message_ids",
        "message_follower_ids",
        "activity_ids",
        "website_message_ids",
    ):
        assert noisy not in selected


def test_select_smart_fields_drops_unstored_compute_and_binary():
    fields = {
        "id": _meta("integer"),
        "name": _meta(),
        "avatar": _meta("binary"),
        "kanban_image": _meta("binary"),
        "computed_summary": _meta("char", compute="_compute_summary", store=False),
        "stored_compute": _meta("char", compute="_compute_x", store=True),
    }
    selected = agent_tools.select_smart_fields(fields)
    assert "avatar" not in selected
    assert "kanban_image" not in selected
    assert "computed_summary" not in selected
    assert "stored_compute" in selected


def test_select_smart_fields_caps_to_max_fields_with_priority_order():
    fields = {
        "id": _meta("integer"),
        "name": _meta(),
        "code": _meta(),
        "state": _meta("selection"),
        "partner_id": _meta("many2one"),
        "filler_a": _meta(),
        "filler_b": _meta(),
        "filler_c": _meta(),
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=5)
    assert len(selected) == 5
    assert selected[0] == "id"
    # high-priority business identifiers must beat generic fillers
    for name in ("name", "code", "state", "partner_id"):
        assert name in selected
    for noise in ("filler_a", "filler_b", "filler_c"):
        assert noise not in selected


def test_select_smart_fields_zero_max_returns_empty():
    assert agent_tools.select_smart_fields({"name": _meta()}, max_fields=0) == []


def test_select_smart_fields_always_include_extends_forced_set():
    fields = {
        "id": _meta("integer"),
        "name": _meta(),
        "internal_note": _meta("text"),
    }
    selected = agent_tools.select_smart_fields(
        fields, max_fields=2, always_include=["internal_note"]
    )
    assert selected[:2] == ["id", "internal_note"]


def test_select_smart_fields_tracking_metadata_boosts_score():
    fields = {
        "id": _meta("integer"),
        "tracked_field": _meta("char", tracking=True),
        "untracked_field": _meta("char"),
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=2)
    assert selected[:2] == ["id", "tracked_field"]


def test_select_smart_fields_empty_metadata_returns_id_only():
    selected = agent_tools.select_smart_fields({})
    assert selected == ["id"]


def test_select_smart_fields_with_only_technical_fields_returns_id_only():
    fields = {
        "id": _meta("integer"),
        "create_uid": _meta("many2one"),
        "write_uid": _meta("many2one"),
        "message_ids": _meta("one2many"),
        "__last_update": _meta("datetime"),
    }
    selected = agent_tools.select_smart_fields(fields)
    assert selected == ["id"]


def test_select_smart_fields_skips_non_dict_metadata_entries():
    fields = {
        "id": _meta("integer"),
        "name": _meta(),
        "broken": "not-a-dict",  # corrupt entry
        "also_broken": None,
        "stable": _meta("char"),
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=10)
    assert "broken" not in selected
    assert "also_broken" not in selected
    assert "stable" in selected
    assert "name" in selected


def test_select_smart_fields_always_include_silently_drops_unknown_fields():
    fields = {"id": _meta("integer"), "name": _meta()}
    selected = agent_tools.select_smart_fields(
        fields, max_fields=5, always_include=["name", "ghost_field"]
    )
    assert "ghost_field" not in selected
    assert selected[:2] == ["id", "name"]


def test_select_smart_fields_max_one_returns_id_only():
    fields = {"id": _meta("integer"), "name": _meta(), "code": _meta()}
    assert agent_tools.select_smart_fields(fields, max_fields=1) == ["id"]


def test_select_smart_fields_drops_automatic_flagged_fields():
    fields = {
        "id": _meta("integer"),
        "name": _meta(),
        "audit_field": _meta("char", automatic=True),
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=5)
    assert "audit_field" not in selected


# ----- build_write_preview_report error branches ----------------------------


def test_build_write_preview_rejects_unknown_operation():
    report = agent_tools.build_write_preview_report(
        model="res.partner", operation="DESTROY"
    )
    assert report["success"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert "unsupported_write_operation" in codes


def test_build_write_preview_rejects_create_without_values():
    report = agent_tools.build_write_preview_report(
        model="res.partner", operation="create"
    )
    assert report["success"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert "missing_create_values" in codes


def test_build_write_preview_rejects_write_without_record_ids_or_values():
    report = agent_tools.build_write_preview_report(
        model="res.partner", operation="write"
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "missing_record_ids" in codes
    assert "missing_write_values" in codes


def test_build_write_preview_rejects_unlink_without_record_ids():
    report = agent_tools.build_write_preview_report(
        model="res.partner", operation="unlink"
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "missing_record_ids" in codes


def test_write_execute_method_args_for_unlink_returns_record_ids_only():
    report = agent_tools.build_write_preview_report(
        model="res.partner", operation="unlink", record_ids=[7]
    )
    assert report["execute_method"]["method"] == "unlink"
    assert report["execute_method"]["args"] == [[7]]


def test_write_execute_method_args_for_create_returns_values_only():
    report = agent_tools.build_write_preview_report(
        model="res.partner", operation="create", values={"name": "Ada"}
    )
    assert report["execute_method"]["args"] == [{"name": "Ada"}]


def test_write_execute_method_args_unknown_operation_returns_empty_args():
    # Trigger the else branch in _write_execute_method_args
    args_dict = agent_tools._write_execute_method_args(
        {"model": "res.partner", "operation": "rename", "context": {}}
    )
    assert args_dict["args"] == []


# ----- verify_write_approval ----------------------------------------------


def test_verify_write_approval_returns_false_for_token_mismatch():
    payload = {
        "model": "res.partner",
        "operation": "write",
        "record_ids": [7],
        "values": {"name": "Ada"},
        "context": {},
        "token": "odoo-write:bogus",
    }
    is_valid, expected = agent_tools.verify_write_approval(payload)
    assert is_valid is False
    assert expected.startswith("odoo-write:")


def test_verify_write_approval_returns_true_for_matching_token():
    canonical = {
        "model": "res.partner",
        "operation": "write",
        "record_ids": [7],
        "values": {"name": "Ada"},
        "context": {},
    }
    token = agent_tools.build_approval_token(canonical)
    is_valid, _ = agent_tools.verify_write_approval({**canonical, "token": token})
    assert is_valid is True


# ----- validate_write_report metadata branches ------------------------------


def test_validate_write_flags_unknown_field_with_metadata():
    report = agent_tools.validate_write_report(
        model="res.partner",
        operation="write",
        values={"ghost": "boo"},
        record_ids=[7],
        fields_metadata={"name": {"type": "char", "readonly": False}},
        metadata_source="server",
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "unknown_field" in codes
    assert report["success"] is False


def test_validate_write_flags_readonly_field():
    report = agent_tools.validate_write_report(
        model="res.partner",
        operation="write",
        values={"ref": "X"},
        record_ids=[7],
        fields_metadata={"ref": {"type": "char", "readonly": True}},
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "readonly_field" in codes


def test_validate_write_emits_many2one_and_relational_hints():
    report = agent_tools.validate_write_report(
        model="res.partner",
        operation="write",
        values={"company_id": 1, "tag_ids": [(6, 0, [1])]},
        record_ids=[7],
        fields_metadata={
            "company_id": {"type": "many2one", "readonly": False},
            "tag_ids": {"type": "many2many", "readonly": False},
        },
    )
    fields = {hint["field"] for hint in report["field_hints"]}
    assert {"company_id", "tag_ids"} <= fields


def test_validate_write_create_emits_required_field_hints():
    report = agent_tools.validate_write_report(
        model="res.partner",
        operation="create",
        values={"name": "Ada"},
        record_ids=None,
        fields_metadata={
            "name": {"type": "char", "readonly": False, "required": True},
            "email": {"type": "char", "readonly": False, "required": True},
            "computed": {"type": "char", "required": True, "compute": "_x"},
            "ro": {"type": "char", "required": True, "readonly": True},
        },
    )
    hints = {hint["field"] for hint in report["field_hints"]}
    # email is required and absent → hinted; name is provided → not hinted;
    # computed and readonly are skipped per logic
    assert "email" in hints
    assert "name" not in hints
    assert "computed" not in hints
    assert "ro" not in hints


def test_validate_write_skips_non_dict_metadata_entries_when_iterating_required():
    # When fields_metadata has corrupt entries, the create-iteration must skip them
    report = agent_tools.validate_write_report(
        model="res.partner",
        operation="create",
        values={"name": "Ada"},
        record_ids=None,
        fields_metadata={"name": {"type": "char", "readonly": False, "required": True}, "broken": "not-a-dict"},
    )
    assert report["success"] is True


# ----- build_domain_report error branches ----------------------------------


def test_build_domain_rejects_missing_field():
    report = agent_tools.build_domain_report(
        conditions=[{"field": "", "operator": "=", "value": 1}]
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "missing_field" in codes
    assert report["success"] is False


def test_build_domain_rejects_invalid_operator():
    report = agent_tools.build_domain_report(
        conditions=[{"field": "name", "operator": "??", "value": 1}]
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "invalid_operator" in codes


def test_build_domain_rejects_unknown_field_when_metadata_supplied():
    report = agent_tools.build_domain_report(
        conditions=[{"field": "ghost", "operator": "=", "value": 1}],
        fields_metadata={"name": {"type": "char"}},
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "unknown_field" in codes


def test_build_domain_rejects_in_operator_with_non_list_value():
    report = agent_tools.build_domain_report(
        conditions=[{"field": "id", "operator": "in", "value": 1}]
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "operator_requires_list" in codes


def test_build_domain_rejects_invalid_logical_operator():
    report = agent_tools.build_domain_report(
        conditions=[{"field": "name", "operator": "=", "value": "Ada"}],
        logical_operator="xor",
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "invalid_logical_operator" in codes


def test_build_domain_or_with_single_condition_keeps_implicit_and():
    # OR with only 1 condition does not prepend "|" (n-1 = 0 prefixes)
    report = agent_tools.build_domain_report(
        conditions=[{"field": "name", "operator": "=", "value": "Ada"}],
        logical_operator="or",
    )
    assert report["domain"] == [["name", "=", "Ada"]]


# ----- token_age_seconds ---------------------------------------------------


def test_token_age_seconds_returns_none_for_missing_timestamp():
    assert agent_tools.token_age_seconds(None) is None


def test_token_age_seconds_returns_non_negative_value():
    import time as _time

    assert agent_tools.token_age_seconds(_time.time() - 1) >= 1.0


def test_token_age_seconds_clamps_future_timestamps_to_zero():
    import time as _time

    assert agent_tools.token_age_seconds(_time.time() + 60) == 0.0


# ----- _normalize_scan_paths ----------------------------------------------


def test_normalize_scan_paths_uses_explicit_argument_first():
    paths = agent_tools._normalize_scan_paths(["/tmp/a", "", "/tmp/b"])
    assert paths == ["/tmp/a", "/tmp/b"]


def test_normalize_scan_paths_falls_back_to_env(monkeypatch):
    import os

    monkeypatch.setenv("ODOO_ADDONS_PATHS", f"/tmp/x{os.pathsep}/tmp/y")
    assert agent_tools._normalize_scan_paths(None) == ["/tmp/x", "/tmp/y"]


def test_normalize_scan_paths_returns_empty_when_no_env(monkeypatch):
    monkeypatch.delenv("ODOO_ADDONS_PATHS", raising=False)
    assert agent_tools._normalize_scan_paths(None) == []


# ----- _read_manifest ------------------------------------------------------


def test_read_manifest_returns_none_for_invalid_python(tmp_path):
    manifest = tmp_path / "module" / "__manifest__.py"
    manifest.parent.mkdir()
    manifest.write_text("{not: valid", encoding="utf-8")
    assert agent_tools._read_manifest(manifest) is None


def test_read_manifest_returns_none_for_non_dict_literal(tmp_path):
    manifest = tmp_path / "module" / "__manifest__.py"
    manifest.parent.mkdir()
    manifest.write_text("['a', 'b', 'c']", encoding="utf-8")
    assert agent_tools._read_manifest(manifest) is None


def test_read_manifest_normalizes_depends_to_list_when_invalid(tmp_path):
    manifest = tmp_path / "weird" / "__manifest__.py"
    manifest.parent.mkdir()
    manifest.write_text(
        "{'name': 'Weird', 'depends': 'not-a-list', 'installable': False}",
        encoding="utf-8",
    )
    parsed = agent_tools._read_manifest(manifest)
    assert parsed is not None
    assert parsed["depends"] == []
    assert parsed["installable"] is False
    assert parsed["custom"] is True


# ----- _scan_python_file syntax errors ----------------------------------


def test_scan_python_file_returns_warning_on_syntax_error(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def x(:\n", encoding="utf-8")
    findings = agent_tools._scan_python_file(bad)
    codes = {f["code"] for f in findings}
    assert "python_parse_error" in codes


def test_scan_python_file_detects_sudo_usage(tmp_path):
    file_path = tmp_path / "models.py"
    file_path.write_text(
        "from odoo import models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    def thing(self):\n"
        "        return self.sudo().read()\n",
        encoding="utf-8",
    )
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "sudo_usage" in codes


def test_scan_python_file_flags_action_function(tmp_path):
    # Standalone def action_X (not in a model class)
    src = "def action_confirm():\n    pass\n"
    path = tmp_path / "module.py"
    path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(path)
    codes = {f["code"] for f in findings}
    assert "custom_method" in codes


# ----- _scan_model_class compute / depends / super branches --------------


def test_scan_model_class_dynamic_compute_handles_non_string_compute_value(tmp_path):
    src = (
        "from odoo import api, fields, models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    total = fields.Float(compute=_compute)\n"  # dynamic compute (Name)
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "computed_method_missing" in codes


def test_scan_model_class_compute_with_depends_and_extra_field_reads(tmp_path):
    src = (
        "from odoo import api, fields, models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    total = fields.Float(compute='_compute_total')\n"
        "    @api.depends('qty')\n"
        "    def _compute_total(self):\n"
        "        for record in self:\n"
        "            record.total = record.qty * record.price\n"  # 'price' missing
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "computed_depends_missing_fields" in codes


def test_scan_model_class_super_call_assigned_then_returned(tmp_path):
    src = (
        "from odoo import models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    def write(self, vals):\n"
        "        result = super().write(vals)\n"
        "        return result\n"
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "crud_override_super_not_returned" not in codes


def test_scan_model_class_super_call_via_annotated_assign(tmp_path):
    src = (
        "from odoo import models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    def write(self, vals):\n"
        "        result: bool = super().write(vals)\n"
        "        return result\n"
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "crud_override_super_not_returned" not in codes


# ----- _scan_xml_file ------------------------------------------------------


def test_scan_xml_file_detects_automated_action_and_view(tmp_path):
    xml = tmp_path / "data.xml"
    xml.write_text(
        '<?xml version="1.0"?>\n'
        '<odoo>\n'
        '  <record id="cron1" model="ir.cron"/>\n'
        '  <record id="view1" model="ir.ui.view"/>\n'
        '</odoo>\n',
        encoding="utf-8",
    )
    findings = agent_tools._scan_xml_file(xml)
    codes = {f["code"] for f in findings}
    assert "automated_action" in codes
    assert "custom_view" in codes


def test_scan_xml_file_returns_warning_on_parse_error(tmp_path):
    xml = tmp_path / "broken.xml"
    xml.write_text("<not closed", encoding="utf-8")
    findings = agent_tools._scan_xml_file(xml)
    codes = {f["code"] for f in findings}
    assert "xml_parse_error" in codes


# ----- scan_addons_source_report -----------------------------------------


def test_scan_addons_source_warns_when_addons_path_missing():
    report = agent_tools.scan_addons_source_report(
        addons_paths=["/this/does/not/exist"]
    )
    codes = {f["code"] for f in report["source_findings"]}
    assert "addons_path_missing" in codes


def test_scan_addons_source_records_modules_findings_security_files(tmp_path):
    addon = tmp_path / "x_pack"
    (addon / "security").mkdir(parents=True)
    (addon / "security" / "ir.model.access.csv").write_text(
        "id,name\n1,X\n", encoding="utf-8"
    )
    (addon / "__manifest__.py").write_text(
        "{'name': 'X', 'installable': False}", encoding="utf-8"
    )
    (addon / "models.py").write_text(
        "from odoo import models\n"
        "class M(models.Model):\n"
        "    _name = 'x.m'\n"
        "    def create(self, vals):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (addon / "view.xml").write_text(
        '<odoo><record id="v1" model="ir.ui.view"/></odoo>',
        encoding="utf-8",
    )

    report = agent_tools.scan_addons_source_report(
        addons_paths=[str(tmp_path)],
        max_files=50,
    )
    codes = {f["code"] for f in report["source_findings"]}
    assert "non_installable_module" in codes
    assert "security_rule_file" in codes
    assert "custom_view" in codes
    assert report["summary"]["modules"] == 1


def test_scan_addons_source_skips_oversized_files(tmp_path):
    addon = tmp_path / "huge"
    addon.mkdir()
    big = addon / "huge.py"
    big.write_text("x = 1\n" * 10000, encoding="utf-8")
    report = agent_tools.scan_addons_source_report(
        addons_paths=[str(tmp_path)],
        max_files=50,
        max_file_bytes=10,
    )
    assert report["summary"]["skipped_files"] >= 1


def test_scan_addons_source_caps_at_max_files(tmp_path):
    addon = tmp_path / "many"
    addon.mkdir()
    for i in range(5):
        (addon / f"m{i}.py").write_text("x=1\n", encoding="utf-8")
    report = agent_tools.scan_addons_source_report(
        addons_paths=[str(tmp_path)],
        max_files=2,
    )
    assert report["summary"]["max_files_reached"] is True
    assert report["summary"]["scanned_files"] == 2


def test_scan_addons_source_skips_symlinks(tmp_path):
    target = tmp_path / "real.py"
    target.write_text("x=1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return  # symlink unsupported on this platform
    report = agent_tools.scan_addons_source_report(addons_paths=[str(tmp_path)])
    # The symlink shouldn't be counted; only the real file
    assert report["summary"]["scanned_files"] == 1


# ----- business_pack_report ------------------------------------------------


def test_business_pack_report_rejects_unknown_pack():
    report = agent_tools.business_pack_report(pack="ghosts")
    assert report["success"] is False
    assert "ghosts" in report["error"]


def test_business_pack_report_static_when_no_evidence():
    report = agent_tools.business_pack_report(pack="sales")
    assert report["success"] is True
    assert report["metadata_used"]["source"] == "static_pack"


# ----- select_smart_fields edge cases for line 928, 940-951 ---------------


def test_select_smart_fields_uses_compute_unstored_one2many_branch():
    fields = {
        "id": _meta("integer"),
        "computed_o2m": _meta("one2many", compute="_x", store=False),
        "stored_o2m": _meta("one2many", compute="_x", store=True),
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=10)
    assert "computed_o2m" not in selected
    assert "stored_o2m" in selected


def test_select_smart_fields_picks_text_html_and_numeric_types():
    fields = {
        "id": _meta("integer"),
        "description": _meta("text"),
        "html_body": _meta("html"),
        "amount": _meta("monetary"),
        "count": _meta("integer"),
        "ratio": _meta("float"),
        "is_active": _meta("boolean"),
        "kind": _meta("selection"),
        "child_ids": _meta("one2many"),  # low priority
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=10)
    assert "description" in selected
    assert "html_body" in selected
    assert "amount" in selected
    assert "is_active" in selected
    assert "kind" in selected
    # Lower-priority field should still appear if room remains
    assert "child_ids" in selected


def test_select_smart_fields_promotes_many2one_id_suffix():
    fields = {
        "id": _meta("integer"),
        "country_id": _meta("many2one"),  # no priority entry, but _id+m2o → 45
        "filler": _meta("char"),
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=2)
    assert selected[:2] == ["id", "country_id"]


def test_select_smart_fields_assigns_score_for_unknown_date_type():
    fields = {
        "id": _meta("integer"),
        "expiry_date": _meta("date"),
        "filler": _meta("char"),
    }
    selected = agent_tools.select_smart_fields(fields, max_fields=2)
    assert selected[:2] == ["id", "expiry_date"]


def test_select_smart_fields_does_not_duplicate_when_already_selected():
    fields = {"id": _meta("integer"), "name": _meta()}
    selected = agent_tools.select_smart_fields(
        fields, max_fields=10, always_include=["name"]
    )
    # Both id and name appear exactly once
    assert selected.count("id") == 1
    assert selected.count("name") == 1


def test_smart_field_score_falls_through_to_default_for_unknown_types():
    # Field type not matching any priority branch returns 10
    score = agent_tools._smart_field_score("custom_thing", {"type": "reference"})
    assert score == 10


def test_smart_field_score_assigns_low_score_to_one2many_many2many_relations():
    assert (
        agent_tools._smart_field_score("members", {"type": "one2many"}) == 5
    )
    assert (
        agent_tools._smart_field_score("tags", {"type": "many2many"}) == 5
    )


# ----- _scan_python_file path / scan_addons_source path coverage --------


def test_scan_addons_source_skips_unmonitored_extensions(tmp_path):
    addon = tmp_path / "irrelevant"
    addon.mkdir()
    (addon / "readme.txt").write_text("hi", encoding="utf-8")
    (addon / "main.py").write_text("x=1\n", encoding="utf-8")
    report = agent_tools.scan_addons_source_report(addons_paths=[str(tmp_path)])
    assert report["summary"]["scanned_files"] == 1


def test_scan_addons_source_handles_stat_oserror(monkeypatch, tmp_path):
    """Verify the OSError except path on stat() lines 402-404 is exercised."""
    addon = tmp_path / "good"
    addon.mkdir()
    target = addon / "x.py"
    target.write_text("x=1\n", encoding="utf-8")

    real_stat = Path.stat
    raised = {"done": False}

    def stat_with_oserror(self, *args, **kwargs):
        # Only raise the OSError exactly once, when the helper performs the
        # size check on our target .py file. is_file/is_symlink may have
        # already issued their own stat calls — by raising on the next stat
        # call against this target after those, we guarantee the try-block
        # in scan_addons_source_report is the receiver.
        if self == target and not raised["done"]:
            # Allow the first 2 calls (is_file + is_symlink) to succeed;
            # raise on the third call which is the explicit size check.
            stat_with_oserror.calls = (
                getattr(stat_with_oserror, "calls", 0) + 1
            )
            if stat_with_oserror.calls >= 3:
                raised["done"] = True
                raise OSError("simulated stat failure")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_oserror)
    report = agent_tools.scan_addons_source_report(addons_paths=[str(tmp_path)])
    assert report["summary"]["skipped_files"] >= 1


def test_scan_addons_source_breaks_when_max_files_zero(tmp_path):
    addon = tmp_path / "x"
    addon.mkdir()
    (addon / "y.py").write_text("a=1\n", encoding="utf-8")
    # Pre-existing path. With max_files=0 the outer-loop "break" branch hits
    # because scanned_files starts at 0 which is not < 0 (wait, ≥0). The break
    # in the outer `for root in paths` runs when scanned_files >= max_files.
    # By passing 0, that fires immediately for the first iteration.
    # Note: clamp_limit is server-side; helper itself accepts arbitrary values.
    report = agent_tools.scan_addons_source_report(
        addons_paths=[str(tmp_path)], max_files=0
    )
    assert report["summary"]["scanned_files"] == 0


def test_scan_python_file_handles_class_def_with_annotated_field_assignment(tmp_path):
    src = (
        "from odoo import fields, models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    total: float = fields.Float()\n"  # AnnAssign w/ no compute
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "custom_model_class" in codes


def test_scan_python_file_handles_compute_via_annotated_assign(tmp_path):
    src = (
        "from odoo import fields, models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    total: float = fields.Float(compute='_compute_total')\n"
        "    def _compute_total(self):\n"
        "        for record in self:\n"
        "            record.total = 0\n"
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "computed_method_missing_depends" in codes


def test_scan_python_file_api_depends_decorator_with_non_call_decorator(tmp_path):
    # Decorator that's not a call (e.g., bare @staticmethod)
    src = (
        "from odoo import models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    @staticmethod\n"
        "    def helper():\n"
        "        return 1\n"
        "    def write(self, vals):\n"
        "        return super().write(vals)\n"
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "custom_model_class" in codes


def test_scan_python_file_field_compute_with_non_fields_call(tmp_path):
    # Trigger _field_compute_method's "expr is Call but func doesn't start with fields."
    src = (
        "from odoo import models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    total = some_helper()\n"
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    codes = {f["code"] for f in findings}
    assert "custom_model_class" in codes


def test_scan_python_file_api_depends_with_non_string_argument_is_ignored(tmp_path):
    src = (
        "from odoo import api, fields, models\n"
        "class X(models.Model):\n"
        "    _name = 'x'\n"
        "    total = fields.Float(compute='_compute')\n"
        "    @api.depends(123)\n"
        "    def _compute(self):\n"
        "        for r in self:\n"
        "            r.total = r.qty\n"
    )
    file_path = tmp_path / "x.py"
    file_path.write_text(src, encoding="utf-8")
    findings = agent_tools._scan_python_file(file_path)
    # 123 is not a string, so depends is empty → missing_depends triggers
    codes = {f["code"] for f in findings}
    assert "computed_method_missing_depends" in codes


def test_super_call_detection_handles_super_attribute_call_without_super_name():
    # Create a synthetic AST node where super_method_call's value is not super()
    import ast as _ast

    src = (
        "def write(self, vals):\n"
        "    return obj.write(vals)\n"  # Not a super().write() call
    )
    func = _ast.parse(src).body[0]
    assert agent_tools._super_method_call(func, "write") is None
    assert agent_tools._super_call_returned(func, "write") is False


def test_api_depends_arguments_skips_non_call_decorators_and_other_calls():
    import ast as _ast

    src = (
        "@staticmethod\n"  # not an ast.Call decorator → line 714 (continue)
        "@some_other_decorator()\n"  # call, but name != api.depends → line 716
        "def foo(self):\n"
        "    return 1\n"
    )
    func = _ast.parse(src).body[0]
    assert agent_tools._api_depends_arguments(func) == set()


def test_contains_super_method_call_returns_false_for_none_expr():
    assert agent_tools._contains_super_method_call(None, "write") is False


def test_is_super_method_call_returns_false_when_not_super_attribute():
    import ast as _ast

    # Plain function call, not Attribute
    expr = _ast.parse("foo()").body[0].value
    assert agent_tools._is_super_method_call(expr, "write") is False
    # Attribute call where attr name does not match method_name
    expr2 = _ast.parse("super().create(vals)").body[0].value
    assert agent_tools._is_super_method_call(expr2, "write") is False


def test_expr_name_returns_empty_for_unsupported_node_type():
    import ast as _ast

    # ast.Subscript is not Name or Attribute → returns ""
    expr = _ast.parse("a[0]").body[0].value
    assert agent_tools._expr_name(expr) == ""
