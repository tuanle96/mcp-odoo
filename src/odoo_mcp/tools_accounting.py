"""
MCP tools: accounting domain (read-only).

Includes: receivable_payable_aging, accounting_health_summary.

Domain-specific reads over account.move / account.move.line — no write
path; the gated write workflow remains the only mutation surface.
"""

from datetime import date
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context

from .accounting_tools import (
    MAX_AGING_LINES,
    build_aging_report,
    build_unreconciled_summary,
    fetch_aging_lines,
    parse_as_of,
)
from .server_core import READ_ONLY_TOOL, _resolve_odoo, mcp
from .tool_helpers import clamp_limit


def build_direction_aging(
    odoo: Any,
    direction: str,
    as_of: date,
    top_partners: int,
    limit: int,
) -> Dict[str, Any]:
    """Fetch open items and bucket them (shared by sync and async paths)."""
    lines = fetch_aging_lines(odoo, direction, limit=limit)
    report = build_aging_report(
        lines, direction, as_of, top_partners=top_partners
    )
    if len(lines) >= limit:
        report["truncated"] = (
            f"Line fetch hit the {limit} cap; totals may be partial. "
            "Narrow the scope or raise limit."
        )
    return report


@mcp.tool(
    description="Aged receivable/payable report bucketed by days overdue",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def receivable_payable_aging(
    ctx: Context,
    direction: str = "receivable",
    as_of: Optional[str] = None,
    top_partners: int = 15,
    limit: int = MAX_AGING_LINES,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Bucket open posted items (not due / 1-30 / 31-60 / 61-90 / 90+ days).

    direction: "receivable" (customers owing you) or "payable" (you owing
    vendors). as_of: ISO date, defaults to today. Works on Odoo 16+.

    Note: the item population is always *currently* open items
    (live amount_residual); ``as_of`` only shifts the bucketing reference
    date — a backdated as_of is not a historical aging snapshot.
    """
    try:
        if direction not in ("receivable", "payable"):
            raise ValueError("direction must be 'receivable' or 'payable'")
        top_partners = clamp_limit(top_partners, maximum=100)
        limit = clamp_limit(limit, maximum=MAX_AGING_LINES)
        _, odoo = _resolve_odoo(ctx, instance)
        report = build_direction_aging(
            odoo, direction, parse_as_of(as_of), top_partners, limit
        )
        return {"success": True, "tool": "receivable_payable_aging", **report}
    except Exception as e:
        return {
            "success": False,
            "tool": "receivable_payable_aging",
            "error": str(e),
        }


@mcp.tool(
    description="Open receivable/payable item counts and draft invoice backlog",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def accounting_health_summary(
    ctx: Context,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Quick accounting posture: open AR/AP item counts plus draft invoices."""
    try:
        _, odoo = _resolve_odoo(ctx, instance)
        summary = build_unreconciled_summary(odoo)
        return {"success": True, "tool": "accounting_health_summary", **summary}
    except Exception as e:
        return {
            "success": False,
            "tool": "accounting_health_summary",
            "error": str(e),
        }
