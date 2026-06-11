"""
MCP tools: read-only cross-instance fan-out.

Ask one question across many configured Odoo instances and get a merged,
instance-attributed answer with a partial-failure map. Each instance is
queried under its own field ACL and rate-limit budget; an instance can opt
out with ``"cross_instance": false`` in config. Gated writes stay
single-instance by design.

The ``run_*`` functions take an AppContext + params so both the synchronous
tools here and the async task path (tools_async) share one implementation.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from mcp.server.fastmcp import Context

from .accounting_tools import build_aging_report, fetch_aging_lines, parse_as_of
from .audit import record_write_event
from .cross_instance import (
    DEFAULT_LIMIT_PER_INSTANCE,
    MAX_LIMIT_PER_INSTANCE,
    combine_aggregate_rows,
    combine_bucket_reports,
    envelope,
    parse_instances_meta,
    select_instances,
    tag_and_merge,
)
from .field_policy import get_field_policy
from .rate_limit import check_rate
from .server_core import READ_ONLY_TOOL, mcp
from .tool_helpers import (
    clamp_limit,
    normalize_domain_input,
    parse_measure_spec,
    validate_model_name,
)

DEFAULT_CROSS_INSTANCE_WORKERS = 4


def _srv() -> Any:
    from . import server

    return server


def _max_workers() -> int:
    raw = os.environ.get("ODOO_MCP_CROSS_INSTANCE_WORKERS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_CROSS_INSTANCE_WORKERS
    except ValueError:
        value = DEFAULT_CROSS_INSTANCE_WORKERS
    return max(1, value)


def _resolve_selection(app_context: Any, instances: Any) -> Any:
    summary = _srv().list_configured_instances()
    metas = parse_instances_meta(summary)
    return select_instances(instances, metas)


def _fan_out(
    selected: List[str], worker: Callable[[str], Any]
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Run ``worker(instance)`` across instances; collect results + errors."""
    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    if not selected:
        return results, errors
    with ThreadPoolExecutor(max_workers=_max_workers()) as executor:
        future_map = {executor.submit(worker, name): name for name in selected}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                results[name] = future.result()
            except Exception as exc:  # noqa: BLE001 - per-instance isolation
                errors[name] = f"{type(exc).__name__}: {exc}"
    return results, errors


def _guard_rate(instance: str, tool: str) -> None:
    refusal = check_rate(instance, tool)
    if refusal is not None:
        raise RuntimeError(refusal["error"])


# --- shared operations (used by sync tools and async jobs) -----------------


def run_search_across(app_context: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    model = str(params["model"])
    validate_model_name(model)
    domain = normalize_domain_input(params.get("domain"))
    fields = params.get("fields")
    limit = clamp_limit(
        int(params.get("limit_per_instance", DEFAULT_LIMIT_PER_INSTANCE)),
        maximum=MAX_LIMIT_PER_INSTANCE,
    )
    selection = _resolve_selection(app_context, params.get("instances"))

    def worker(instance: str) -> List[Dict[str, Any]]:
        name, client = app_context.get_client(instance)
        _guard_rate(name, "search_across_instances")
        records = client.search_read(model, domain, fields=fields, limit=limit)
        records, _ = get_field_policy().redact_records(name, model, list(records))
        return records

    results, errors = _fan_out(selection.selected, worker)
    merged = tag_and_merge(results)
    record_write_event(
        "cross_instance_query",
        outcome="ok",
        model=model,
        operation="search",
        instance=",".join(selection.selected) or None,
        detail=f"domain_len={len(domain)} instances={len(selection.selected)}",
    )
    payload = envelope(
        {name: {"count": len(rows)} for name, rows in results.items()},
        errors,
        selection,
    )
    payload["model"] = model
    payload["merged"] = merged
    payload["merged_count"] = len(merged)
    return payload


def run_aggregate_across(app_context: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    model = str(params["model"])
    validate_model_name(model)
    group_by = list(params.get("group_by") or [])
    if not group_by:
        raise ValueError("group_by must include at least one field")
    measures = list(params.get("measures") or [])
    domain = normalize_domain_input(params.get("domain"))
    parsed = [parse_measure_spec(spec) for spec in measures]
    measure_fields = [field for field, _ in parsed]
    normalized_measures = [f"{field}:{agg}" for field, agg in parsed]
    selection = _resolve_selection(app_context, params.get("instances"))

    policy = get_field_policy()
    referenced = [entry.split(":", 1)[0] for entry in group_by] + measure_fields

    def worker(instance: str) -> List[Dict[str, Any]]:
        name, client = app_context.get_client(instance)
        _guard_rate(name, "aggregate_across_instances")
        block = policy.check_aggregate(name, model, referenced)
        if block is not None:
            raise RuntimeError(block)
        return list(
            client.execute_method(
                model, "read_group", domain, normalized_measures, group_by
            )
        )

    results, errors = _fan_out(selection.selected, worker)
    combined = combine_aggregate_rows(results, measure_fields)
    record_write_event(
        "cross_instance_query",
        outcome="ok",
        model=model,
        operation="aggregate",
        instance=",".join(selection.selected) or None,
        detail=f"group_by={group_by} instances={len(selection.selected)}",
    )
    payload = envelope(
        {name: rows for name, rows in results.items()}, errors, selection
    )
    payload["model"] = model
    payload.update(combined)
    return payload


def run_accounting_health_across(
    app_context: Any, params: Dict[str, Any]
) -> Dict[str, Any]:
    direction = str(params.get("direction", "receivable"))
    if direction not in ("receivable", "payable"):
        raise ValueError("direction must be 'receivable' or 'payable'")
    as_of = parse_as_of(params.get("as_of"))
    top_partners = clamp_limit(int(params.get("top_partners", 10)), maximum=100)
    selection = _resolve_selection(app_context, params.get("instances"))

    def worker(instance: str) -> Dict[str, Any]:
        name, client = app_context.get_client(instance)
        _guard_rate(name, "accounting_health_across_instances")
        lines = fetch_aging_lines(client, direction)
        return build_aging_report(
            lines, direction, as_of, top_partners=top_partners
        )

    results, errors = _fan_out(selection.selected, worker)
    combined = combine_bucket_reports(results)
    record_write_event(
        "cross_instance_query",
        outcome="ok",
        model="account.move.line",
        operation=f"aging_{direction}",
        instance=",".join(selection.selected) or None,
        detail=f"as_of={as_of.isoformat()} instances={len(selection.selected)}",
    )
    payload = envelope(results, errors, selection)
    payload["direction"] = direction
    payload["as_of"] = as_of.isoformat()
    payload.update(combined)
    return payload


# --- MCP tool surface ------------------------------------------------------


@mcp.tool(
    description="Read-only search fanned out across configured Odoo instances, merged and attributed",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def search_across_instances(
    ctx: Context,
    model: str,
    domain: Optional[Any] = None,
    fields: Optional[List[str]] = None,
    limit_per_instance: int = DEFAULT_LIMIT_PER_INSTANCE,
    instances: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run one search across many instances; rows are tagged with `_instance`.

    `instances`: omit or "all" for every opted-in instance, a list of names,
    or {"tags": ["..."]}. Each instance is queried under its own field ACL;
    one instance being down yields a partial result + `errors` map, never a
    total failure. Read-only. For very large fleets, wrap with
    submit_async_task(operation="search_across_instances").
    """
    try:
        app_context = ctx.request_context.lifespan_context
        return {
            "tool": "search_across_instances",
            **run_search_across(
                app_context,
                {
                    "model": model,
                    "domain": domain,
                    "fields": fields,
                    "limit_per_instance": limit_per_instance,
                    "instances": instances,
                },
            ),
        }
    except Exception as e:
        return {"success": False, "tool": "search_across_instances", "error": str(e)}


@mcp.tool(
    description="Read-only aggregate fanned out across instances with combined grand totals",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def aggregate_across_instances(
    ctx: Context,
    model: str,
    group_by: List[str],
    measures: Optional[List[str]] = None,
    domain: Optional[Any] = None,
    instances: Optional[Any] = None,
) -> Dict[str, Any]:
    """Group/aggregate per instance plus additive grand totals across them.

    `measures` are "field:agg" strings. Combined totals sum additive measures
    (sum/count) across instances; averages are not combined (use per-instance
    rows). Denied aggregate fields are rejected per instance.
    """
    try:
        app_context = ctx.request_context.lifespan_context
        return {
            "tool": "aggregate_across_instances",
            **run_aggregate_across(
                app_context,
                {
                    "model": model,
                    "group_by": group_by,
                    "measures": measures,
                    "domain": domain,
                    "instances": instances,
                },
            ),
        }
    except Exception as e:
        return {
            "success": False,
            "tool": "aggregate_across_instances",
            "error": str(e),
        }


@mcp.tool(
    description="AR/AP aging fanned out across instances — the partner-network sweep",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def accounting_health_across_instances(
    ctx: Context,
    direction: str = "receivable",
    as_of: Optional[str] = None,
    top_partners: int = 10,
    instances: Optional[Any] = None,
) -> Dict[str, Any]:
    """Aged receivable/payable across every client DB, with combined buckets.

    The anti-Peliqan flagship: ask "which clients have AR over 90 days?" once
    and get per-instance aging plus summed buckets. Read-only; partial
    results on per-instance failure.
    """
    try:
        app_context = ctx.request_context.lifespan_context
        return {
            "tool": "accounting_health_across_instances",
            **run_accounting_health_across(
                app_context,
                {
                    "direction": direction,
                    "as_of": as_of,
                    "top_partners": top_partners,
                    "instances": instances,
                },
            ),
        }
    except Exception as e:
        return {
            "success": False,
            "tool": "accounting_health_across_instances",
            "error": str(e),
        }
