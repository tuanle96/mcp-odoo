"""Module-boundary coverage for the extracted addon scanner."""

import importlib
import importlib.util

from odoo_mcp import agent_tools


def test_agent_tools_reexports_addon_scanner_api():
    spec = importlib.util.find_spec("odoo_mcp.addon_scanner")
    assert spec is not None

    addon_scanner = importlib.import_module("odoo_mcp.addon_scanner")
    exported_names = (
        "scan_addons_source_report",
        "_normalize_scan_paths",
        "_read_manifest",
        "_scan_python_file",
        "_scan_xml_file",
    )
    for name in exported_names:
        assert getattr(agent_tools, name) is getattr(addon_scanner, name)
