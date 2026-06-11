"""
MCP tools: background task domain.

Includes: submit_async_task, get_async_task, cancel_async_task,
list_async_tasks.

Long-running operations run on a bounded worker pool so the agent can keep
reasoning and poll for results. Only an allowlist of read-only/preview
operations can run in the background — the gated write workflow stays
synchronous and explicit by design.
"""

from datetime import date
from typing import Any, Callable, Dict, Optional

from mcp.server.fastmcp import Context

from .accounting_tools import MAX_AGING_LINES, parse_as_of
from .agent_tools import scan_addons_source_report
from .server_core import (
    PREVIEW_TOOL,
    _resolve_odoo,
    mcp,
    resolve_read_fields,
    restrict_addons_paths,
)
from .task_queue import get_task_manager
from .tool_helpers import clamp_limit, normalize_domain_input, validate_model_name
from .tools_accounting import build_direction_aging
from .tools_knowledge import MAX_INDEX_FETCH, fetch_and_index

ASYNC_OPERATIONS = (
    "scan_addons_source",
    "index_knowledge",
    "receivable_payable_aging",
)


def _build_scan_addons_job(params: Dict[str, Any]) -> Callable[[], Dict[str, Any]]:
    addons_paths = restrict_addons_paths(params.get("addons_paths"))
    max_files = clamp_limit(int(params.get("max_files", 200)), maximum=1000)
    max_file_bytes = int(params.get("max_file_bytes", 300_000))
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be greater than 0")

    def job() -> Dict[str, Any]:
        return scan_addons_source_report(
            addons_paths=addons_paths,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
        )

    return job


def _build_index_knowledge_job(
    ctx: Context, instance: Optional[str], params: Dict[str, Any]
) -> Callable[[], Dict[str, Any]]:
    model = str(params.get("model", ""))
    validate_model_name(model)
    limit = clamp_limit(int(params.get("limit", 500)), maximum=MAX_INDEX_FETCH)
    replace = bool(params.get("replace", False))
    domain = normalize_domain_input(params.get("domain"))
    # Resolve connection and field selection now, while the request context
    # is alive; the worker thread only fetches and indexes.
    instance_name, odoo = _resolve_odoo(ctx, instance)
    app_context = ctx.request_context.lifespan_context
    fields = resolve_read_fields(
        app_context, odoo, model, params.get("fields"), instance_name
    )

    def job() -> Dict[str, Any]:
        return fetch_and_index(
            odoo, instance_name, model, domain, fields, limit, replace
        )

    return job


def _build_aging_job(
    ctx: Context, instance: Optional[str], params: Dict[str, Any]
) -> Callable[[], Dict[str, Any]]:
    direction = str(params.get("direction", "receivable"))
    if direction not in ("receivable", "payable"):
        raise ValueError("direction must be 'receivable' or 'payable'")
    as_of: date = parse_as_of(params.get("as_of"))
    top_partners = clamp_limit(int(params.get("top_partners", 15)), maximum=100)
    limit = clamp_limit(
        int(params.get("limit", MAX_AGING_LINES)), maximum=MAX_AGING_LINES
    )
    _, odoo = _resolve_odoo(ctx, instance)

    def job() -> Dict[str, Any]:
        return build_direction_aging(odoo, direction, as_of, top_partners, limit)

    return job


@mcp.tool(
    description="Run an allowlisted long-running read operation in the background",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def submit_async_task(
    ctx: Context,
    operation: str,
    params: Optional[Dict[str, Any]] = None,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a background task; poll with get_async_task.

    Allowlisted operations: scan_addons_source (params: addons_paths,
    max_files, max_file_bytes), index_knowledge (params: model, domain,
    fields, limit, replace), receivable_payable_aging (params: direction,
    as_of, top_partners, limit). Writes are never accepted here.
    """
    try:
        params = params or {}
        if operation == "scan_addons_source":
            job = _build_scan_addons_job(params)
        elif operation == "index_knowledge":
            job = _build_index_knowledge_job(ctx, instance, params)
        elif operation == "receivable_payable_aging":
            job = _build_aging_job(ctx, instance, params)
        else:
            raise ValueError(
                f"Unknown operation '{operation}'. "
                f"Allowed: {', '.join(ASYNC_OPERATIONS)}"
            )
        submitted = get_task_manager().submit(operation, job)
        return {"tool": "submit_async_task", **submitted}
    except Exception as e:
        return {"success": False, "tool": "submit_async_task", "error": str(e)}


@mcp.tool(
    description="Poll a background task's status and result",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def get_async_task(task_id: str) -> Dict[str, Any]:
    """Return task status; includes the result once status is succeeded."""
    try:
        return {"tool": "get_async_task", **get_task_manager().status(task_id)}
    except Exception as e:
        return {"success": False, "tool": "get_async_task", "error": str(e)}


@mcp.tool(
    description="Cancel a pending or running background task",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def cancel_async_task(task_id: str) -> Dict[str, Any]:
    """Cancel a task: pending tasks never start; running ones are discarded."""
    try:
        return {"tool": "cancel_async_task", **get_task_manager().cancel(task_id)}
    except Exception as e:
        return {"success": False, "tool": "cancel_async_task", "error": str(e)}


@mcp.tool(
    description="List recent background tasks newest-first",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def list_async_tasks() -> Dict[str, Any]:
    """List live and recently finished tasks (results omitted; poll by id)."""
    try:
        return {
            "success": True,
            "tool": "list_async_tasks",
            "tasks": get_task_manager().list_tasks(),
        }
    except Exception as e:
        return {"success": False, "tool": "list_async_tasks", "error": str(e)}
