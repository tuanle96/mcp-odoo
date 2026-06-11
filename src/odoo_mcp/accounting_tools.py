"""Read-only accounting report builders (receivable/payable aging).

Domain-specific helpers for the accounting tool surface: aging buckets and
an unreconciled-items summary computed from ``account.move.line`` data.
Works on Odoo 16+ where ``account.account.account_type`` is a selection
(``asset_receivable`` / ``liability_payable``).

Pure functions: callers pass an Odoo client; nothing here mutates state.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

AGING_BUCKETS = (
    ("not_due", None, 0),
    ("1-30", 1, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("90+", 91, None),
)

_DIRECTION_ACCOUNT_TYPE = {
    "receivable": "asset_receivable",
    "payable": "liability_payable",
}

MAX_AGING_LINES = 5000


def parse_as_of(as_of: Optional[str]) -> date:
    """Parse an ISO date string, defaulting to today."""
    if not as_of:
        return date.today()
    return datetime.strptime(as_of[:10], "%Y-%m-%d").date()


def aging_domain(direction: str) -> List[Any]:
    """Open posted items for one direction, ready for search_read."""
    account_type = _DIRECTION_ACCOUNT_TYPE[direction]
    return [
        ("account_id.account_type", "=", account_type),
        ("parent_state", "=", "posted"),
        ("reconciled", "=", False),
        ("amount_residual", "!=", 0),
    ]


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def bucket_for_days(days_overdue: int) -> str:
    for name, low, high in AGING_BUCKETS:
        if low is None and days_overdue <= 0:
            return name
        if low is not None and days_overdue >= low and (
            high is None or days_overdue <= high
        ):
            return name
    return "not_due"


def build_aging_report(
    lines: List[Dict[str, Any]],
    direction: str,
    as_of: date,
    top_partners: int = 15,
) -> Dict[str, Any]:
    """Bucket open items by days overdue and aggregate per partner.

    ``lines`` come from search_read on account.move.line with fields
    ``amount_residual``, ``date_maturity``, ``date``, ``partner_id``.
    Payable residuals are negative in Odoo; they are sign-flipped so the
    report always shows positive outstanding amounts.
    """
    sign = 1.0 if direction == "receivable" else -1.0
    totals = {name: 0.0 for name, _, _ in AGING_BUCKETS}
    partners: Dict[str, Dict[str, Any]] = {}
    skipped_no_amount = 0

    for line in lines:
        residual = line.get("amount_residual")
        if not isinstance(residual, (int, float)) or residual == 0:
            skipped_no_amount += 1
            continue
        amount = sign * float(residual)
        due = _parse_date(line.get("date_maturity")) or _parse_date(line.get("date"))
        days_overdue = (as_of - due).days if due else 0
        bucket = bucket_for_days(days_overdue)
        totals[bucket] += amount

        partner_field = line.get("partner_id")
        if isinstance(partner_field, (list, tuple)) and len(partner_field) >= 2:
            partner_key = str(partner_field[1])
        else:
            partner_key = "(no partner)"
        entry = partners.setdefault(
            partner_key,
            {"partner": partner_key, "total": 0.0, **{n: 0.0 for n, _, _ in AGING_BUCKETS}},
        )
        entry["total"] += amount
        entry[bucket] += amount

    ranked = sorted(partners.values(), key=lambda item: item["total"], reverse=True)
    for entry in ranked:
        entry["total"] = round(entry["total"], 2)
        for name, _, _ in AGING_BUCKETS:
            entry[name] = round(entry[name], 2)

    return {
        "direction": direction,
        "as_of": as_of.isoformat(),
        "buckets": {name: round(value, 2) for name, value in totals.items()},
        "total_outstanding": round(sum(totals.values()), 2),
        "partners": ranked[: max(1, top_partners)],
        "partner_count": len(ranked),
        "line_count": len(lines) - skipped_no_amount,
        "skipped_lines": skipped_no_amount,
    }


def fetch_aging_lines(
    client: Any, direction: str, limit: int = MAX_AGING_LINES
) -> List[Dict[str, Any]]:
    """Pull open items for one direction via the standard client surface."""
    return list(
        client.search_read(
            "account.move.line",
            aging_domain(direction),
            fields=["amount_residual", "date_maturity", "date", "partner_id"],
            limit=max(1, min(limit, MAX_AGING_LINES)),
        )
    )


def build_unreconciled_summary(client: Any) -> Dict[str, Any]:
    """Counts of open receivable/payable items plus draft invoices."""
    summary: Dict[str, Any] = {}
    for direction in ("receivable", "payable"):
        domain = aging_domain(direction)
        summary[f"open_{direction}_items"] = client.execute_method(
            "account.move.line", "search_count", domain
        )
    summary["draft_invoices"] = client.execute_method(
        "account.move",
        "search_count",
        [("state", "=", "draft"), ("move_type", "in", ["out_invoice", "in_invoice"])],
    )
    return summary
