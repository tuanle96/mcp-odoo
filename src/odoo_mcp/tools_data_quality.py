"""
MCP tools: data-quality domain.

Includes: data_quality_report.
"""

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import Context

from .data_quality import ALL_CHECKS, build_data_quality_report
from .schemas import ToolResponse
from .server_core import READ_ONLY_TOOL, mcp, _resolve_odoo
from .tool_helpers import clamp_limit, validate_model_name


class DataQualityReportResponse(ToolResponse):
    """Typed envelope for data_quality_report (payload stays permissive)."""

    model: Optional[str] = None
    instance: Optional[str] = None
    sample_limit: Optional[int] = None
    checks_run: Optional[List[str]] = None
    skipped_restricted_fields: Optional[List[str]] = None
    results: Optional[List[Dict[str, Any]]] = None
    summary: Optional[Dict[str, Any]] = None


@mcp.tool(
    description=(
        "Run read-only data-quality checks on one Odoo model: duplicates, "
        "missing required values, orphaned references, format anomalies"
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def data_quality_report(
    ctx: Context,
    model: str,
    checks: Optional[List[str]] = None,
    key_fields: Optional[List[str]] = None,
    sample_limit: int = 500,
    instance: Optional[str] = None,
) -> DataQualityReportResponse:
    """
    Evidence-first data-quality report for a model (never modifies data).

    ``checks`` defaults to all of: duplicates, missing_required,
    orphaned_references, format_anomalies. ``key_fields`` overrides the
    duplicate-scan key heuristic (email/vat/ref/...). Sampled checks read at
    most ``sample_limit`` records (capped at 2000). Run it before a migration
    or before trusting aggregate answers on a messy database; route any
    remediation through the gated write workflow.
    """
    try:
        validate_model_name(model)
        sample_limit = clamp_limit(sample_limit, maximum=2000)
        instance_name, odoo = _resolve_odoo(ctx, instance)
        return build_data_quality_report(  # type: ignore[return-value]
            odoo,
            instance_name,
            model,
            checks=checks,
            key_fields=key_fields,
            sample_limit=sample_limit,
        )
    except Exception as e:
        return {  # type: ignore[return-value]
            "success": False,
            "tool": "data_quality_report",
            "error": str(e),
        }


__all__ = ["data_quality_report", "DataQualityReportResponse", "ALL_CHECKS"]
