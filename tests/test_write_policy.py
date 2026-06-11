"""Unit tests for write_policy module write enablement and side-effect policy."""

import json

import pytest

from odoo_mcp import write_policy


class TestWritesEnabled:
    """Test writes_enabled() env gate."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1", True),
            ("true", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("", False),
        ],
    )
    def test_env_var_values(self, monkeypatch, value, expected):
        """Test various ODOO_MCP_ENABLE_WRITES values."""
        if value:
            monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", value)
        else:
            monkeypatch.delenv("ODOO_MCP_ENABLE_WRITES", raising=False)
        assert write_policy.writes_enabled() is expected

    def test_unset_returns_false(self):
        """Return False when ODOO_MCP_ENABLE_WRITES unset."""
        import os
        if "ODOO_MCP_ENABLE_WRITES" in os.environ:
            del os.environ["ODOO_MCP_ENABLE_WRITES"]
        assert write_policy.writes_enabled() is False


class TestChatterDirectEnabled:
    """Test chatter_direct_enabled() env gate."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1", True),
            ("true", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("", False),
        ],
    )
    def test_env_var_values(self, monkeypatch, value, expected):
        """Test various MCP_CHATTER_DIRECT values."""
        if value:
            monkeypatch.setenv("MCP_CHATTER_DIRECT", value)
        else:
            monkeypatch.delenv("MCP_CHATTER_DIRECT", raising=False)
        assert write_policy.chatter_direct_enabled() is expected

    def test_unset_returns_false(self):
        """Return False when MCP_CHATTER_DIRECT unset."""
        import os
        if "MCP_CHATTER_DIRECT" in os.environ:
            del os.environ["MCP_CHATTER_DIRECT"]
        assert write_policy.chatter_direct_enabled() is False


class TestPolicyFilePath:
    """Test policy_file_path() resolution."""

    def test_explicit_env_var_takes_priority(self, monkeypatch, tmp_path):
        """ODOO_MCP_POLICY_FILE env var takes priority."""
        policy_file = tmp_path / "custom_policy.json"
        policy_file.write_text("{}")
        monkeypatch.setenv("ODOO_MCP_POLICY_FILE", str(policy_file))
        monkeypatch.chdir(tmp_path)

        result = write_policy.policy_file_path()
        assert result == str(policy_file)

    def test_default_file_when_exists(self, monkeypatch, tmp_path):
        """Use default odoo_mcp_policy.json when it exists."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text("{}")

        result = write_policy.policy_file_path()
        assert result == "odoo_mcp_policy.json"

    def test_none_when_no_file_configured(self, monkeypatch, tmp_path):
        """Return None when no policy file configured or found."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ODOO_MCP_POLICY_FILE", raising=False)

        result = write_policy.policy_file_path()
        assert result is None

    def test_env_var_with_empty_string_treated_as_unset(self, monkeypatch, tmp_path):
        """Empty ODOO_MCP_POLICY_FILE treated as unset."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ODOO_MCP_POLICY_FILE", "")
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text("{}")

        result = write_policy.policy_file_path()
        assert result == "odoo_mcp_policy.json"

    def test_env_var_with_whitespace_stripped(self, monkeypatch, tmp_path):
        """Whitespace stripped from ODOO_MCP_POLICY_FILE."""
        policy_file = tmp_path / "custom.json"
        policy_file.write_text("{}")
        monkeypatch.setenv("ODOO_MCP_POLICY_FILE", f"  {str(policy_file)}  ")

        result = write_policy.policy_file_path()
        assert result == str(policy_file)


class TestLoadSideEffectPolicy:
    """Test load_side_effect_policy() file loading."""

    def test_returns_none_path_when_no_file(self):
        """Return path=None when no policy file."""
        result = write_policy.load_side_effect_policy()
        assert result["path"] is None
        assert result["methods"] == []
        assert result["error"] is None

    def test_loads_string_entries(self, monkeypatch, tmp_path):
        """Load side-effect methods as plain strings."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "sale.order.action_confirm",
                    "stock.move.action_done",
                ]
            })
        )

        result = write_policy.load_side_effect_policy()
        assert result["path"] == "odoo_mcp_policy.json"
        assert set(result["methods"]) == {
            "sale.order.action_confirm",
            "stock.move.action_done",
        }
        assert result["error"] is None

    def test_loads_dict_entries_with_method_key(self, monkeypatch, tmp_path):
        """Load side-effect methods from objects with 'method' key."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    {
                        "method": "sale.order.action_confirm",
                        "reviewed_by": "alice@example.com",
                        "date": "2024-01-01",
                    },
                    {
                        "method": "stock.move.action_done",
                        "reason": "Stock adjustment",
                    },
                ]
            })
        )

        result = write_policy.load_side_effect_policy()
        assert set(result["methods"]) == {
            "sale.order.action_confirm",
            "stock.move.action_done",
        }
        assert result["error"] is None

    def test_mixed_string_and_dict_entries(self, monkeypatch, tmp_path):
        """Load mixed string and dict entries."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "sale.order.action_confirm",
                    {"method": "stock.move.action_done", "reviewed_by": "bob"},
                    "account.move.action_post",
                ]
            })
        )

        result = write_policy.load_side_effect_policy()
        assert set(result["methods"]) == {
            "sale.order.action_confirm",
            "stock.move.action_done",
            "account.move.action_post",
        }

    def test_skips_invalid_entries(self, monkeypatch, tmp_path):
        """Skip entries with missing or empty method names."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "sale.order.action_confirm",
                    {"reviewed_by": "alice"},  # No method key
                    {"method": ""},  # Empty method
                    {"method": "  "},  # Whitespace-only method
                    123,  # Non-string, non-dict
                    {"method": "stock.move.action_done"},
                ]
            })
        )

        result = write_policy.load_side_effect_policy()
        assert result["methods"] == [
            "sale.order.action_confirm",
            "stock.move.action_done",
        ]

    def test_strips_whitespace_from_method_names(self, monkeypatch, tmp_path):
        """Strip whitespace from method names."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "  sale.order.action_confirm  ",
                    {"method": "  stock.move.action_done  "},
                ]
            })
        )

        result = write_policy.load_side_effect_policy()
        assert result["methods"] == [
            "sale.order.action_confirm",
            "stock.move.action_done",
        ]

    def test_missing_allowed_side_effect_methods_key(self, monkeypatch, tmp_path):
        """Return empty methods list when key missing."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(json.dumps({"other_key": []}))

        result = write_policy.load_side_effect_policy()
        assert result["methods"] == []
        assert result["error"] is None

    def test_empty_allowed_side_effect_methods_list(self, monkeypatch, tmp_path):
        """Handle empty methods list."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(json.dumps({"allowed_side_effect_methods": []}))

        result = write_policy.load_side_effect_policy()
        assert result["methods"] == []

    def test_null_allowed_side_effect_methods_list(self, monkeypatch, tmp_path):
        """Handle null/None in methods list."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(json.dumps({"allowed_side_effect_methods": None}))

        result = write_policy.load_side_effect_policy()
        assert result["methods"] == []

    def test_file_not_found_returns_error(self, monkeypatch, tmp_path):
        """Return error when file doesn't exist."""
        monkeypatch.setenv("ODOO_MCP_POLICY_FILE", str(tmp_path / "nonexistent.json"))

        result = write_policy.load_side_effect_policy()
        assert result["path"] == str(tmp_path / "nonexistent.json")
        assert result["methods"] == []
        assert result["error"] is not None
        assert "No such file" in result["error"] or "cannot find" in result["error"].lower()

    def test_invalid_json_returns_error(self, monkeypatch, tmp_path):
        """Return error on invalid JSON."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text("{broken json}")

        result = write_policy.load_side_effect_policy()
        assert result["path"] == "odoo_mcp_policy.json"
        assert result["methods"] == []
        assert result["error"] is not None
        assert "JSON" in result["error"] or "Expecting" in result["error"]

    def test_fail_closed_on_read_error(self, monkeypatch, tmp_path):
        """Don't expose methods when file read fails."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text("broken")

        result = write_policy.load_side_effect_policy()
        assert result["error"] is not None
        assert result["methods"] == []

    def test_preserves_entry_order(self, monkeypatch, tmp_path):
        """Preserve order of entries from file."""
        monkeypatch.chdir(tmp_path)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "z.method",
                    "a.method",
                    "m.method",
                ]
            })
        )

        result = write_policy.load_side_effect_policy()
        assert result["methods"] == ["z.method", "a.method", "m.method"]


class TestAllowedSideEffectMethods:
    """Test allowed_side_effect_methods() env + file merge."""

    def test_empty_when_no_env_or_file(self):
        """Return empty list when no env or file configured."""
        import os
        if "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS" in os.environ:
            del os.environ["ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS"]

        result = write_policy.allowed_side_effect_methods()
        assert result == []

    def test_env_var_only(self, monkeypatch):
        """Load from env var only."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm,stock.move.action_done"
        )

        result = write_policy.allowed_side_effect_methods()
        assert set(result) == {"sale.order.action_confirm", "stock.move.action_done"}

    def test_env_var_with_whitespace(self, monkeypatch):
        """Handle whitespace in env var."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "  sale.order.action_confirm  ,  stock.move.action_done  "
        )

        result = write_policy.allowed_side_effect_methods()
        assert set(result) == {"sale.order.action_confirm", "stock.move.action_done"}

    def test_file_only(self, monkeypatch, tmp_path):
        """Load from file only."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", raising=False)
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "sale.order.action_confirm",
                    "stock.move.action_done",
                ]
            })
        )

        result = write_policy.allowed_side_effect_methods()
        assert set(result) == {"sale.order.action_confirm", "stock.move.action_done"}

    def test_merges_env_and_file(self, monkeypatch, tmp_path):
        """Merge env var and file methods."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm"
        )
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "stock.move.action_done",
                ]
            })
        )

        result = write_policy.allowed_side_effect_methods()
        assert set(result) == {"sale.order.action_confirm", "stock.move.action_done"}

    def test_deduplicates_merged_list(self, monkeypatch, tmp_path):
        """Deduplicate when same method in env and file."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm,shared.method"
        )
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "shared.method",
                    "stock.move.action_done",
                ]
            })
        )

        result = write_policy.allowed_side_effect_methods()
        assert result.count("shared.method") == 1
        assert set(result) == {
            "sale.order.action_confirm",
            "shared.method",
            "stock.move.action_done",
        }

    def test_preserves_env_then_file_order(self, monkeypatch, tmp_path):
        """Preserve env methods first, then file methods."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "z.method,a.method"
        )
        policy_file = tmp_path / "odoo_mcp_policy.json"
        policy_file.write_text(
            json.dumps({
                "allowed_side_effect_methods": [
                    "m.method",
                ]
            })
        )

        result = write_policy.allowed_side_effect_methods()
        # Env methods come first
        assert result.index("z.method") < result.index("m.method")
        assert result.index("a.method") < result.index("m.method")

    def test_env_var_empty_entries_skipped(self, monkeypatch):
        """Skip empty entries in env var."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm,,stock.move.action_done,"
        )

        result = write_policy.allowed_side_effect_methods()
        assert set(result) == {"sale.order.action_confirm", "stock.move.action_done"}


class TestSideEffectMethodAllowed:
    """Test side_effect_method_allowed() exact match check."""

    def test_exact_match_returns_true(self, monkeypatch):
        """Return True for exact method match."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm"
        )
        assert write_policy.side_effect_method_allowed("sale.order", "action_confirm") is True

    def test_no_match_returns_false(self, monkeypatch):
        """Return False when method not in allowlist."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm"
        )
        assert (
            write_policy.side_effect_method_allowed("sale.order", "action_cancel")
            is False
        )

    def test_partial_model_match_returns_false(self, monkeypatch):
        """Return False for partial model matches."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm"
        )
        assert (
            write_policy.side_effect_method_allowed("sale", "action_confirm") is False
        )

    def test_multiple_methods_one_match(self, monkeypatch):
        """Return True when one of multiple methods matches."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm,stock.move.action_done"
        )
        assert write_policy.side_effect_method_allowed("stock.move", "action_done") is True

    def test_empty_allowlist_returns_false(self, monkeypatch):
        """Return False when allowlist empty."""
        monkeypatch.delenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", raising=False)

        assert write_policy.side_effect_method_allowed("any.model", "any_method") is False

    def test_case_sensitive_match(self, monkeypatch):
        """Matching is case-sensitive."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "sale.order.action_confirm"
        )
        assert (
            write_policy.side_effect_method_allowed("sale.order", "Action_Confirm") is False
        )

    def test_full_method_name_format(self, monkeypatch):
        """Construct full model.method format for matching."""
        monkeypatch.setenv(
            "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS",
            "res.partner.custom_method,sale.order.action_confirm"
        )
        assert (
            write_policy.side_effect_method_allowed("res.partner", "custom_method") is True
        )
        assert (
            write_policy.side_effect_method_allowed("sale.order", "action_confirm") is True
        )
        assert (
            write_policy.side_effect_method_allowed("res.partner", "other_method") is False
        )
