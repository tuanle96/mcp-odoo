"""
MCP tools: local-first knowledge search domain.

Includes: index_knowledge, search_knowledge, knowledge_stats.

Records are fetched once over the existing read surface, then ranked
locally with BM25 — no embeddings service and no data leaving the machine.
"""

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from .field_policy import get_field_policy
from .knowledge_index import get_knowledge_store
from .server_core import (
    PREVIEW_TOOL,
    READ_ONLY_TOOL,
    _resolve_odoo,
    mcp,
    resolve_read_fields,
)
from .tool_helpers import clamp_limit, normalize_domain_input, validate_model_name

MAX_INDEX_FETCH = 2000


def _srv() -> Any:
    """Late import of server module to resolve patchable symbols at call time."""
    from . import server

    return server


def fetch_and_index(
    odoo: Any,
    instance_name: str,
    model: str,
    domain: List[Any],
    fields: Optional[List[str]],
    limit: int,
    replace: bool,
) -> Dict[str, Any]:
    """Fetch records and feed them to the BM25 store (sync or async path)."""
    records = odoo.search_read(model, domain, fields=fields, limit=limit)
    # Field ACL: denied fields must never enter the local index, or their
    # values would be cached and searchable despite the policy.
    records, redacted_fields = get_field_policy().redact_records(
        instance_name, model, list(records)
    )
    outcome = get_knowledge_store().index_records(
        instance_name, model, records, replace=replace
    )
    outcome["fetched"] = len(records)
    outcome["indexed_fields"] = fields if fields else "smart selection"
    if redacted_fields:
        outcome["redacted_fields"] = redacted_fields
    return outcome


@mcp.tool(
    description="Fetch a bounded slice of records and build a local BM25 knowledge index",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def index_knowledge(
    ctx: Context,
    model: str,
    domain: Optional[Any] = None,
    fields: Optional[List[str]] = None,
    limit: int = 500,
    replace: bool = False,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Index records for free-text relevance search without further RPC calls.

    Data stays in process memory on this machine. ``fields=None`` uses the
    same smart business-field selection as search_records; pass an explicit
    list to index specific text fields. ``replace=True`` rebuilds the index
    for the model. Budget: ODOO_MCP_KNOWLEDGE_MAX_DOCS documents total.
    """
    try:
        validate_model_name(model)
        limit = clamp_limit(limit, maximum=MAX_INDEX_FETCH)
        instance_name, odoo = _resolve_odoo(ctx, instance)
        app_context = ctx.request_context.lifespan_context
        normalized_domain = normalize_domain_input(domain)
        read_fields = resolve_read_fields(
            app_context, odoo, model, fields, instance_name
        )
        outcome = fetch_and_index(
            odoo, instance_name, model, normalized_domain, read_fields, limit, replace
        )
        return {"tool": "index_knowledge", **outcome}
    except Exception as e:
        return {"success": False, "tool": "index_knowledge", "error": str(e)}


@mcp.tool(
    description="Relevance-ranked local BM25 search over previously indexed records",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def search_knowledge(
    query: str,
    model: str,
    limit: int = 5,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Rank indexed records against a free-text query (accent-insensitive).

    Purely local: never contacts Odoo. Run index_knowledge for the model
    first. Returns record ids, BM25 scores, and text snippets.
    """
    try:
        validate_model_name(model)
        limit = clamp_limit(limit, maximum=50)
        instance_name = str(_srv().resolve_instance_name(instance))
        result = get_knowledge_store().search(
            instance_name, model, query, limit=limit
        )
        return {"tool": "search_knowledge", **result}
    except Exception as e:
        return {"success": False, "tool": "search_knowledge", "error": str(e)}


@mcp.tool(
    description="Report local knowledge index sizes and document budget",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def knowledge_stats() -> Dict[str, Any]:
    """List per-model index sizes, total documents, and the configured cap."""
    try:
        return {
            "success": True,
            "tool": "knowledge_stats",
            **get_knowledge_store().stats(),
        }
    except Exception as e:
        return {"success": False, "tool": "knowledge_stats", "error": str(e)}
