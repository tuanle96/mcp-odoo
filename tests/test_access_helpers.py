"""Unit tests for access_helpers module ACL/record-rule analysis."""


from odoo_mcp import access_helpers


class TestAccessPermissionField:
    """Test access_permission_field operation -> ACL field mapping."""

    def test_create_maps_to_perm_create(self):
        """Map create operation to perm_create."""
        assert access_helpers.access_permission_field("create") == "perm_create"
        assert access_helpers.access_permission_field("CREATE") == "perm_create"
        assert access_helpers.access_permission_field("  create  ") == "perm_create"

    def test_write_maps_to_perm_write(self):
        """Map write operation to perm_write."""
        assert access_helpers.access_permission_field("write") == "perm_write"
        assert access_helpers.access_permission_field("WRITE") == "perm_write"

    def test_unlink_delete_maps_to_perm_unlink(self):
        """Map unlink/delete operations to perm_unlink."""
        assert access_helpers.access_permission_field("unlink") == "perm_unlink"
        assert access_helpers.access_permission_field("delete") == "perm_unlink"
        assert access_helpers.access_permission_field("DELETE") == "perm_unlink"

    def test_read_operations_map_to_perm_read(self):
        """Map read operations to perm_read."""
        assert access_helpers.access_permission_field("read") == "perm_read"
        assert access_helpers.access_permission_field("search") == "perm_read"
        assert access_helpers.access_permission_field("search_read") == "perm_read"
        assert access_helpers.access_permission_field("search_count") == "perm_read"
        assert access_helpers.access_permission_field("name_search") == "perm_read"

    def test_unknown_operation_defaults(self):
        """Unknown operations map based on safety classification."""
        # This depends on classify_method_safety behavior
        # Default case: safe operation -> perm_read
        result = access_helpers.access_permission_field("safe_method")
        assert result in {"perm_read", "perm_write"}

    def test_whitespace_handling(self):
        """Strip whitespace from operation."""
        assert access_helpers.access_permission_field("  read  ") == "perm_read"
        assert access_helpers.access_permission_field("\tcreate\n") == "perm_create"


class TestM2oId:
    """Test _m2o_id extraction from many-to-one values."""

    def test_integer_value(self):
        """Extract ID from integer."""
        assert access_helpers._m2o_id(5) == 5
        assert access_helpers._m2o_id(0) == 0

    def test_list_with_id_and_name(self):
        """Extract ID from [id, display_name] format."""
        assert access_helpers._m2o_id([5, "Partner Name"]) == 5
        assert access_helpers._m2o_id([1, ""]) == 1

    def test_tuple_with_id_and_name(self):
        """Extract ID from (id, display_name) format."""
        assert access_helpers._m2o_id((5, "Partner Name")) == 5

    def test_empty_list_returns_none(self):
        """Return None for empty list."""
        assert access_helpers._m2o_id([]) is None

    def test_empty_tuple_returns_none(self):
        """Return None for empty tuple."""
        assert access_helpers._m2o_id(()) is None

    def test_list_non_int_first_element_returns_none(self):
        """Return None when first element is not int."""
        assert access_helpers._m2o_id(["not_int", "name"]) is None
        assert access_helpers._m2o_id([1.5, "name"]) is None

    def test_non_list_non_tuple_non_int_returns_none(self):
        """Return None for other types."""
        assert access_helpers._m2o_id("string") is None
        assert access_helpers._m2o_id({"id": 5}) is None
        assert access_helpers._m2o_id(None) is None


class TestM2mIds:
    """Test _m2m_ids extraction from many-to-many values."""

    def test_empty_list(self):
        """Extract empty set from empty list."""
        assert access_helpers._m2m_ids([]) == set()

    def test_list_of_integers(self):
        """Extract integer IDs from list."""
        assert access_helpers._m2m_ids([1, 2, 3]) == {1, 2, 3}

    def test_list_of_tuples(self):
        """Extract IDs from (id, display_name) tuples."""
        assert access_helpers._m2m_ids([(1, "A"), (2, "B"), (3, "C")]) == {1, 2, 3}

    def test_list_of_lists(self):
        """Extract IDs from [id, display_name] lists."""
        assert access_helpers._m2m_ids([[1, "A"], [2, "B"], [3, "C"]]) == {1, 2, 3}

    def test_mixed_formats(self):
        """Extract from mixed integer and tuple formats."""
        assert access_helpers._m2m_ids([1, (2, "B"), [3, "C"]]) == {1, 2, 3}

    def test_ignores_non_int_items(self):
        """Skip items that don't match m2o pattern."""
        assert access_helpers._m2m_ids([1, "string", (2, "B"), None]) == {1, 2}

    def test_non_list_input_returns_empty(self):
        """Return empty set for non-list input."""
        assert access_helpers._m2m_ids("not_list") == set()
        assert access_helpers._m2m_ids(123) == set()
        assert access_helpers._m2m_ids(None) == set()

    def test_tuples_with_non_int_first_element(self):
        """Skip tuples with non-int first element."""
        assert access_helpers._m2m_ids([("string", "B"), (1, "A")]) == {1}

    def test_deduplicates_ids(self):
        """Deduplicate IDs in result."""
        assert access_helpers._m2m_ids([1, (1, "A"), [1, "B"], 1]) == {1}


class TestFieldNames:
    """Test _field_names extraction from metadata."""

    def test_empty_dict(self):
        """Extract empty set from empty dict."""
        assert access_helpers._field_names({}) == set()

    def test_dict_with_field_names(self):
        """Extract field names from dict keys."""
        metadata = {"id": {}, "name": {}, "email": {}}
        assert access_helpers._field_names(metadata) == {"id", "name", "email"}

    def test_non_dict_returns_empty(self):
        """Return empty set for non-dict input."""
        assert access_helpers._field_names([]) == set()
        assert access_helpers._field_names("string") == set()
        assert access_helpers._field_names(None) == set()

    def test_converts_keys_to_strings(self):
        """Convert keys to strings."""
        metadata = {123: {}, "name": {}, 456: {}}
        result = access_helpers._field_names(metadata)
        assert "123" in result
        assert "name" in result
        assert "456" in result


class TestAvailableUserReadFields:
    """Test _available_user_read_fields field selection."""

    def test_empty_available_fields(self):
        """Return base candidates when no fields available."""
        result = access_helpers._available_user_read_fields(set())
        assert "id" in result
        assert "name" in result

    def test_filters_to_available_fields(self):
        """Filter candidates to available fields."""
        available = {"id", "name", "company_id"}
        result = access_helpers._available_user_read_fields(available)
        assert set(result) == {"id", "name", "company_id"}

    def test_includes_group_fields_when_available(self):
        """Include group fields when available."""
        available = {"id", "name", "groups_id", "company_id"}
        result = access_helpers._available_user_read_fields(available)
        assert "groups_id" in result

    def test_prefers_groups_id_over_group_ids(self):
        """Prefer groups_id when both present."""
        available = {"id", "groups_id", "group_ids"}
        result = access_helpers._available_user_read_fields(available)
        assert "groups_id" in result

    def test_includes_all_group_ids(self):
        """Include all_group_ids when available."""
        available = {"id", "name", "all_group_ids"}
        result = access_helpers._available_user_read_fields(available)
        assert "all_group_ids" in result


class TestGroupFieldNames:
    """Test _group_field_names field detection in records."""

    def test_both_group_fields_present(self):
        """Detect both groups_id and all_group_ids."""
        record = {"groups_id": [1, 2], "all_group_ids": [1, 2, 3]}
        direct, all_groups = access_helpers._group_field_names(record)
        assert direct == "groups_id"
        assert all_groups == "all_group_ids"

    def test_only_direct_group_field(self):
        """Detect only direct group field."""
        record = {"groups_id": [1, 2]}
        direct, all_groups = access_helpers._group_field_names(record)
        assert direct == "groups_id"
        assert all_groups is None

    def test_only_all_group_ids(self):
        """Detect only all_group_ids."""
        record = {"all_group_ids": [1, 2, 3]}
        direct, all_groups = access_helpers._group_field_names(record)
        assert direct is None
        assert all_groups == "all_group_ids"

    def test_no_group_fields(self):
        """Return None when no group fields present."""
        record = {"id": 1, "name": "Test"}
        direct, all_groups = access_helpers._group_field_names(record)
        assert direct is None
        assert all_groups is None

    def test_group_ids_as_fallback(self):
        """Use group_ids when groups_id not present."""
        record = {"group_ids": [1, 2]}
        direct, all_groups = access_helpers._group_field_names(record)
        assert direct == "group_ids"


class TestAclRowApplies:
    """Test _acl_row_applies ACL applicability logic."""

    def test_no_group_id_restriction_applies(self):
        """ACL with no group_id applies to all users."""
        row = {"perm_read": True, "group_id": None}
        assert access_helpers._acl_row_applies(row, {1, 2, 3}) is True
        assert access_helpers._acl_row_applies(row, None) is True

    def test_group_id_in_user_groups_applies(self):
        """ACL applies when group_id is in user's groups."""
        row = {"perm_read": True, "group_id": [2, "Group Two"]}
        assert access_helpers._acl_row_applies(row, {1, 2, 3}) is True

    def test_group_id_not_in_user_groups_doesnt_apply(self):
        """ACL doesn't apply when group_id not in user's groups."""
        row = {"perm_read": True, "group_id": [5, "Group Five"]}
        assert access_helpers._acl_row_applies(row, {1, 2, 3}) is False

    def test_group_id_with_no_user_groups_doesnt_apply(self):
        """ACL with group_id doesn't apply when user has no groups."""
        row = {"perm_read": True, "group_id": [2, "Group Two"]}
        assert access_helpers._acl_row_applies(row, None) is False

    def test_group_id_as_integer(self):
        """Handle group_id as integer."""
        row = {"perm_read": True, "group_id": 2}
        assert access_helpers._acl_row_applies(row, {1, 2, 3}) is True
        assert access_helpers._acl_row_applies(row, {1, 3}) is False


class TestRuleApplies:
    """Test _rule_applies record rule applicability logic."""

    def test_no_groups_restriction_applies(self):
        """Rule with no groups applies to all users."""
        row = {"domain": "[]"}
        assert access_helpers._rule_applies(row, {1, 2, 3}) is True
        assert access_helpers._rule_applies(row, None) is True

    def test_groups_in_user_groups_applies(self):
        """Rule applies when group in user's groups."""
        row = {"groups": [(2, "Group Two"), (3, "Group Three")]}
        assert access_helpers._rule_applies(row, {1, 2, 3}) is True

    def test_groups_not_in_user_groups_doesnt_apply(self):
        """Rule doesn't apply when no user group matches."""
        row = {"groups": [(5, "Group Five"), (6, "Group Six")]}
        assert access_helpers._rule_applies(row, {1, 2, 3}) is False

    def test_groups_with_no_user_groups_doesnt_apply(self):
        """Rule with groups doesn't apply when user has no groups."""
        row = {"groups": [(2, "Group Two")]}
        assert access_helpers._rule_applies(row, None) is False

    def test_groups_as_integers(self):
        """Handle groups as integer IDs."""
        row = {"groups": [2, 3]}
        assert access_helpers._rule_applies(row, {1, 2, 3}) is True
        assert access_helpers._rule_applies(row, {1, 4}) is False


class TestRecordIdDomain:
    """Test _record_id_domain building."""

    def test_empty_record_ids(self):
        """Return empty domain for no record IDs."""
        assert access_helpers._record_id_domain([]) == []
        assert access_helpers._record_id_domain(None) == []

    def test_single_record_id(self):
        """Build domain for single record ID."""
        result = access_helpers._record_id_domain([5])
        assert result == [["id", "in", [5]]]

    def test_multiple_record_ids(self):
        """Build domain for multiple record IDs."""
        result = access_helpers._record_id_domain([1, 2, 3])
        assert result == [["id", "in", [1, 2, 3]]]

    def test_filters_zero_and_negative_ids(self):
        """Filter out zero and negative IDs."""
        result = access_helpers._record_id_domain([1, 0, -5, 2])
        assert result == [["id", "in", [1, 2]]]

    def test_string_ids_converted_to_int(self):
        """Convert string IDs to integers."""
        result = access_helpers._record_id_domain(["1", "2", "3"])
        assert result == [["id", "in", [1, 2, 3]]]

    def test_all_invalid_ids_returns_empty(self):
        """Return empty when all IDs are invalid."""
        result = access_helpers._record_id_domain([0, -1, -2])
        assert result == []


class TestAccessDiagnosisCodes:
    """Test _access_diagnosis_codes code generation."""

    def test_no_issues_returns_no_issue_detected(self):
        """Return 'no_issue_detected' when all clear."""
        codes = access_helpers._access_diagnosis_codes(
            metadata_errors=[],
            acl_rows=[],
            granting_acl_rows=[],
            active_rules=[],
            applicable_rules=[],
            actual_count=10,
            expected_count=10,
            record_ids=[],
        )
        assert len(codes) == 1
        assert codes[0]["code"] == "no_access_issue_detected"

    def test_metadata_errors_included(self):
        """Include metadata_access_unavailable when metadata errors exist."""
        codes = access_helpers._access_diagnosis_codes(
            metadata_errors=[{"error": "failed to read"}],
            acl_rows=[],
            granting_acl_rows=[],
            active_rules=[],
            applicable_rules=[],
            actual_count=None,
            expected_count=None,
            record_ids=[],
        )
        assert any(c["code"] == "metadata_access_unavailable" for c in codes)

    def test_acl_denied_when_no_granting_rows(self):
        """Include acl_denied_likely when ACL rows exist but none grant."""
        codes = access_helpers._access_diagnosis_codes(
            metadata_errors=[],
            acl_rows=[{"perm_read": False}],
            granting_acl_rows=[],
            active_rules=[],
            applicable_rules=[],
            actual_count=10,
            expected_count=10,
            record_ids=[],
        )
        assert any(c["code"] == "acl_denied_likely" for c in codes)

    def test_record_rule_filter_when_count_mismatch_and_rules_active(self):
        """Include record_rule_filter_likely when rules exist and count mismatches."""
        codes = access_helpers._access_diagnosis_codes(
            metadata_errors=[],
            acl_rows=[],
            granting_acl_rows=[{"perm_read": True}],
            active_rules=[{"domain": "['user_id', '=', uid]"}],
            applicable_rules=[],
            actual_count=5,
            expected_count=10,
            record_ids=[],
        )
        assert any(c["code"] == "record_rule_filter_likely" for c in codes)

    def test_domain_or_rule_filter_when_count_mismatch_no_rules(self):
        """Include domain_or_rule_filter_likely when count mismatches without rules."""
        codes = access_helpers._access_diagnosis_codes(
            metadata_errors=[],
            acl_rows=[],
            granting_acl_rows=[{"perm_read": True}],
            active_rules=[],
            applicable_rules=[],
            actual_count=5,
            expected_count=10,
            record_ids=[],
        )
        assert any(c["code"] == "domain_or_rule_filter_likely" for c in codes)

    def test_record_ids_mismatch_detection(self):
        """Detect count mismatch based on record_ids length."""
        codes = access_helpers._access_diagnosis_codes(
            metadata_errors=[],
            acl_rows=[],
            granting_acl_rows=[{"perm_read": True}],
            active_rules=[],
            applicable_rules=[],
            actual_count=2,  # Less than requested 5
            expected_count=None,
            record_ids=[1, 2, 3, 4, 5],  # Requested 5
        )
        assert any(c["code"] == "domain_or_rule_filter_likely" for c in codes)

    def test_no_mismatch_when_counts_match(self):
        """Don't report mismatch when counts are equal."""
        codes = access_helpers._access_diagnosis_codes(
            metadata_errors=[],
            acl_rows=[],
            granting_acl_rows=[{"perm_read": True}],
            active_rules=[],
            applicable_rules=[],
            actual_count=10,
            expected_count=10,
            record_ids=[],
        )
        filter_codes = [c for c in codes if "filter_likely" in c["code"]]
        assert len(filter_codes) == 0
