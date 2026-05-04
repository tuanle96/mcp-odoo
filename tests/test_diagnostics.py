from odoo_mcp import diagnostics


def test_generate_json2_payload_builds_search_read_preview_without_client_side_effects():
    report = diagnostics.generate_json2_payload_report(
        model="res.partner",
        method="search_read",
        args=[[["is_company", "=", True]]],
        kwargs={"fields": ["name"], "limit": 5},
        base_url="odoo.example.test",
        database="demo-db",
    )

    assert report["success"] is True
    assert report["endpoint"] == {
        "path": "/json/2/res.partner/search_read",
        "url": "https://odoo.example.test/json/2/res.partner/search_read",
    }
    assert report["headers"]["Authorization"] == "bearer <api-key>"
    assert report["headers"]["X-Odoo-Database"] == "demo-db"
    assert report["body"] == {
        "domain": [["is_company", "=", True]],
        "fields": ["name"],
        "limit": 5,
    }
    assert report["metadata_used"] == {"client_instantiated": False}


def test_generate_json2_payload_omits_x_odoo_database_when_disabled():
    report = diagnostics.generate_json2_payload_report(
        model="res.partner",
        method="search_read",
        args=[[]],
        database="demo-db",
        include_database_header=False,
    )

    assert report["headers"]["X-Odoo-Database"] is None


def test_diagnose_odoo_call_marks_write_create_unlink_as_destructive_without_execution():
    for method in ["write", "create", "unlink"]:
        report = diagnostics.diagnose_odoo_call_report(
            model="res.partner",
            method=method,
            args=[[7], {"name": "Ada"}] if method == "write" else [],
        )

        assert report["classification"]["safety"] == "destructive"
        assert report["classification"]["destructive_method"] is True
        assert any(issue["code"] == "destructive_method" for issue in report["issues"])


def test_diagnose_odoo_call_marks_common_read_methods_as_read_only():
    for method in [
        "search",
        "search_count",
        "search_read",
        "read",
        "fields_get",
        "name_search",
    ]:
        report = diagnostics.diagnose_odoo_call_report(
            model="res.partner",
            method=method,
            args=[],
        )

        assert report["classification"]["safety"] == "read_only"
        assert report["classification"]["destructive_method"] is False


def test_diagnose_odoo_call_marks_common_side_effect_methods():
    for method in ["message_post", "action_confirm", "button_validate", "send_mail"]:
        report = diagnostics.diagnose_odoo_call_report(
            model="sale.order",
            method=method,
            args=[],
        )

        assert report["classification"]["safety"] == "side_effect"
        assert any(issue["code"] == "side_effect_method" for issue in report["issues"])


def test_diagnose_odoo_call_warns_unknown_positional_json2_method_without_execution():
    report = diagnostics.diagnose_odoo_call_report(
        model="res.partner",
        method="custom_method",
        args=["positional"],
    )

    assert report["success"] is False
    assert report["classification"]["json2_ready"] is False
    assert any(
        issue["code"] == "json2_positional_unsupported" for issue in report["issues"]
    )


def test_odoo_error_redacts_debug_by_default_and_allows_explicit_debug():
    error = {
        "name": "odoo.exceptions.AccessError",
        "message": "Access denied",
        "arguments": ["Access denied"],
        "context": {"model": "res.partner"},
        "debug": "traceback details",
    }

    redacted = diagnostics.sanitize_odoo_error(error)
    unredacted = diagnostics.sanitize_odoo_error(error, include_debug=True)

    assert redacted == {
        "name": "odoo.exceptions.AccessError",
        "message": "Access denied",
        "arguments": ["Access denied"],
        "context": {"model": "res.partner"},
        "debug": "[redacted]",
    }
    assert unredacted["debug"] == "traceback details"


def test_odoo_error_parser_accepts_top_level_json_error_wrapper():
    wrapped = (
        'HTTP 403: {"error": {"name": "odoo.exceptions.AccessError", '
        '"message": "Access denied", "debug": "traceback details"}}'
    )

    report = diagnostics.sanitize_odoo_error(wrapped)

    assert report == {
        "name": "odoo.exceptions.AccessError",
        "message": "Access denied",
        "arguments": [],
        "context": {},
        "debug": "[redacted]",
    }


def test_inspect_model_relationships_groups_relational_and_required_fields_from_metadata():
    report = diagnostics.inspect_model_relationships_report(
        model="res.partner",
        fields_metadata={
            "name": {"type": "char", "required": True, "readonly": False},
            "company_id": {
                "type": "many2one",
                "relation": "res.company",
                "required": False,
                "readonly": False,
            },
            "category_id": {
                "type": "many2many",
                "relation": "res.partner.category",
                "required": False,
                "readonly": False,
            },
            "child_ids": {
                "type": "one2many",
                "relation": "res.partner",
                "readonly": True,
            },
        },
        metadata_source="input",
    )

    assert report["success"] is True
    assert report["summary"]["relationship_count"] == 3
    assert report["required_fields"] == [
        {"name": "name", "type": "char", "relation": None}
    ]
    assert report["relationships"]["many2one"][0]["relation"] == "res.company"
    assert report["metadata_used"]["source"] == "input"


def test_upgrade_risk_report_flags_odoo20_rpc_removal_and_destructive_methods():
    report = diagnostics.upgrade_risk_report(
        source_version="18.0",
        target_version="20.0",
        methods=[{"model": "res.partner", "method": "write"}],
    )

    assert report["summary"] == {"risk": "high", "blocked": True}
    assert report["transport"]["xmlrpc_jsonrpc_deprecation"] == "Odoo 20 fall 2026"
    assert report["destructive_methods"] == [
        {"model": "res.partner", "method": "write", "source": "input"}
    ]
    assert any(risk["code"] == "xmlrpc_jsonrpc_removal" for risk in report["risks"])


def test_build_json2_body_warns_on_too_many_positional_args_and_duplicates():
    body, warnings = diagnostics.build_json2_body(
        "res.partner",
        "search_read",
        args=["dom", "fields", 0, 1, "asc", "extra"],  # > 5 mapped names
        kwargs={"domain": "kw-domain"},  # collides with positional
    )
    codes = {w["code"] for w in warnings}
    assert "json2_too_many_positional_args" in codes
    assert "json2_duplicate_argument" in codes
    # kwarg wins on collision
    assert body["domain"] == "kw-domain"


def test_parse_error_string_returns_message_when_no_json_braces():
    parsed = diagnostics._parse_error_string("Plain text error")
    assert parsed == {"message": "Plain text error", "arguments": [], "context": {}}


def test_parse_error_string_returns_message_on_invalid_json():
    parsed = diagnostics._parse_error_string("HTTP 500: {not really json}")
    assert parsed["message"] == "HTTP 500: {not really json}"
    assert parsed["arguments"] == []


def test_parse_error_string_returns_top_level_dict_when_no_error_key():
    parsed = diagnostics._parse_error_string('{"name": "Boom", "message": "oh no"}')
    assert parsed == {"name": "Boom", "message": "oh no"}


def test_diagnose_odoo_call_rejects_invalid_model_name():
    report = diagnostics.diagnose_odoo_call_report(
        model="bad model name", method="search_read"
    )
    assert report["success"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert "invalid_model_name" in codes


def test_diagnose_odoo_call_flags_xmlrpc_against_odoo_20():
    report = diagnostics.diagnose_odoo_call_report(
        model="res.partner",
        method="search_read",
        target_version="20.0",
        transport="xmlrpc",
    )
    codes = {issue["code"] for issue in report["issues"]}
    assert "deprecated_rpc_transport" in codes
    assert report["success"] is False


def test_inspect_model_relationships_returns_error_payload_when_metadata_missing():
    report = diagnostics.inspect_model_relationships_report(
        model="res.partner",
        fields_metadata=None,
        metadata_source="server",
        metadata_error="Access denied",
    )
    assert report["success"] is False
    assert report["error"] == "Access denied"
    assert report["summary"] == {
        "field_count": 0,
        "relationship_count": 0,
        "required_count": 0,
    }


def test_inspect_model_relationships_skips_non_dict_meta_and_filters_readonly_computed():
    report = diagnostics.inspect_model_relationships_report(
        model="res.partner",
        fields_metadata={
            "broken": "not-a-dict",
            "ro_field": {"type": "char", "readonly": True, "required": True},
            "computed_field": {"type": "char", "compute": "_x", "required": False},
            "name": {"type": "char", "required": True},
        },
        metadata_source="input",
        include_readonly=False,
        include_computed=False,
    )
    assert report["success"] is True
    field_names = {f["name"] for f in report["required_fields"]}
    # readonly excluded
    assert "ro_field" not in field_names
    # computed excluded
    assert "computed_field" not in field_names
    assert "name" in field_names


def test_upgrade_risk_report_target_19_warns_about_json2_migration():
    report = diagnostics.upgrade_risk_report(target_version="19.0")
    codes = {risk["code"] for risk in report["risks"]}
    assert "json2_migration" in codes


def test_upgrade_risk_report_flags_unknown_custom_methods_and_custom_modules():
    report = diagnostics.upgrade_risk_report(
        methods=[{"model": "x.thing", "method": "do_magic"}],
        modules=[{"name": "x_custom", "custom": True}, {"name": "studio_x"}],
        source_findings=[
            {
                "code": "custom_method",
                "severity": "warning",
                "evidence": "x.py:5",
                "recommendation": "Review.",
            }
        ],
        observed_errors=["Plain error"],
    )
    codes = {risk["code"] for risk in report["risks"]}
    assert "unknown_custom_method" in codes
    assert "custom_module_upgrade" in codes
    assert "custom_method" in codes
    assert report["odoo_errors"][0]["message"] == "Plain error"


def test_transport_compatibility_handles_each_transport_branch():
    assert diagnostics._transport_compatibility("auto", True) == "both"
    assert diagnostics._transport_compatibility("auto", False) == "xmlrpc"
    assert diagnostics._transport_compatibility("json2", True) == "json2"
    assert diagnostics._transport_compatibility("json2", False) == "unknown"
    assert diagnostics._transport_compatibility("xmlrpc", True) == "xmlrpc"
    assert diagnostics._transport_compatibility("nonsense", True) == "unknown"


def test_max_risk_returns_low_when_no_risks_collected():
    assert diagnostics._max_risk([]) == "low"
    assert (
        diagnostics._max_risk([{"severity": "warning", "code": "x", "evidence": "y", "recommendation": "z"}])
        == "medium"
    )


def test_fit_gap_report_uses_terminology_evidence_when_no_models_or_modules():
    report = diagnostics.fit_gap_report(
        requirements=["Manage contact list"],
        available_models=None,
        installed_modules=None,
    )
    item = report["items"][0]
    assert item["classification"] == "standard"
    assert item["evidence"] == ["Matches common standard Odoo app terminology."]


def test_diagnose_odoo_call_includes_next_actions_for_metadata_unused():
    report = diagnostics.diagnose_odoo_call_report(
        model="res.partner",
        method="search_read",
    )
    assert "Call inspect_model_relationships for field-level hints." in report[
        "next_actions"
    ]


def test_fit_gap_report_normalizes_requirement_classifications_and_safe_discovery_calls():
    report = diagnostics.fit_gap_report(
        requirements=[
            "Track contacts",
            "Configure approval sequence",
            "Add custom field on partner form view",
            "Complex external API workflow",
            "Bypass access rules",
            "Something novel",
        ],
        available_models=["res.partner"],
        installed_modules=["base", "sale"],
    )

    classifications = {
        item["requirement"]: item["classification"] for item in report["items"]
    }
    assert classifications == {
        "Track contacts": "standard",
        "Configure approval sequence": "configuration",
        "Add custom field on partner form view": "studio",
        "Complex external API workflow": "custom_module",
        "Bypass access rules": "avoid",
        "Something novel": "unknown",
    }
    for item in report["items"]:
        for call in item["recommended_next_calls"]:
            assert call["tool"] in {"list_models", "inspect_model_relationships"}
