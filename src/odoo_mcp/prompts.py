"""
MCP prompts for the Odoo MCP server.
"""

from .server_core import mcp


@mcp.prompt(
    name="diagnose_failed_odoo_call",
    description="Guide an assistant through diagnosing a failed Odoo model call.",
)
def prompt_diagnose_failed_odoo_call(
    model: str,
    method: str,
    error: str = "",
) -> str:
    """Prompt for root-causing failed Odoo calls using the safe tools first."""
    return (
        "Diagnose this Odoo call without retrying destructive methods first.\n"
        f"Model: {model}\n"
        f"Method: {method}\n"
        f"Observed error: {error or '<not provided>'}\n\n"
        "Use diagnose_odoo_call, diagnose_access, inspect_model_relationships, "
        "and get_model_fields before execute_method. Preserve Odoo error details, "
        "but do not expose secrets."
    )


@mcp.prompt(
    name="fit_gap_workshop",
    description="Structure an Odoo fit/gap workshop from raw requirements.",
)
def prompt_fit_gap_workshop(requirement: str) -> str:
    """Prompt for classifying a business requirement safely."""
    return (
        "Classify this requirement into standard Odoo, configuration, Studio, "
        "custom module, avoid, or unknown.\n"
        f"Requirement: {requirement}\n\n"
        "Use fit_gap_report first, then schema_catalog/list_models for evidence. "
        "Recommend the smallest Odoo-native implementation path."
    )


@mcp.prompt(
    name="json2_migration_plan",
    description="Plan migration from XML-RPC/JSON-RPC style calls to Odoo JSON-2.",
)
def prompt_json2_migration_plan(model: str, method: str) -> str:
    """Prompt for JSON-2 named-argument and transaction migration planning."""
    return (
        "Prepare a JSON-2 migration plan for this Odoo call.\n"
        f"Model: {model}\n"
        f"Method: {method}\n\n"
        "Use generate_json2_payload and upgrade_risk_report. Call out named "
        "arguments, per-call transaction behavior, database header expectations, "
        "and destructive-method safeguards."
    )


@mcp.prompt(
    name="safe_write_review",
    description="Review a proposed create/write/unlink before execution.",
)
def prompt_safe_write_review(model: str, operation: str) -> str:
    """Prompt for approval-token write review."""
    return (
        "Review this proposed Odoo write before any execution.\n"
        f"Model: {model}\n"
        f"Operation: {operation}\n\n"
        "Use preview_write and validate_write. Only execute through "
        "execute_approved_write when the approval token matches, confirm=true is "
        "explicit, and the runtime has ODOO_MCP_ENABLE_WRITES=1."
    )


@mcp.prompt(
    name="custom_module_audit",
    description="Guide a local source audit for custom Odoo addons.",
)
def prompt_custom_module_audit(addons_path: str) -> str:
    """Prompt for local custom-addon review without importing code."""
    return (
        "Audit local Odoo addon source without importing addon modules.\n"
        f"Addons path: {addons_path}\n\n"
        "Use scan_addons_source, upgrade_risk_report, and business_pack_report. "
        "Prioritize manifest dependencies, computed field dependencies, overridden "
        "create/write/unlink methods, sudo usage, automated actions, custom views, "
        "and security CSV files."
    )
