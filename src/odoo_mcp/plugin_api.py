"""Stable API surface for third-party odoo-mcp tool plugins.

A plugin is a normal Python package exposing an entry point in the
``odoo_mcp.tools`` group whose target is a ``register(api)`` callable::

    [project.entry-points."odoo_mcp.tools"]
    my_plugin = "my_pkg.plugin:register"

``register`` receives this module. Plugins load **only** when the operator
opts in via ``ODOO_MCP_PLUGINS=my_plugin,other`` — installation alone never
activates code. A plugin that raises is isolated: the server keeps serving
builtin tools and reports the failure in ``health_check``.

Contract (v1): use ``api.tool`` to register, ``api.resolve_odoo`` to get the
(instance_name, client) pair, ``api.redact_records`` before returning record
data, and the ``{"success": ...}`` envelope for results. Plugins run in the
server process with the server's credentials — install only plugins you
trust, and route any data modification through the gated write workflow
(direct writes in plugins are a contract violation; see docs/plugins.md).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from mcp.server.mcpserver import Context

from .field_policy import get_field_policy
from .server_core import (
    PREVIEW_TOOL,
    READ_ONLY_TOOL,
    _resolve_odoo,
    mcp,
)
from .tool_helpers import clamp_limit, validate_model_name

PLUGIN_API_VERSION = 1

# The decorator plugins use: api.tool(description=..., annotations=api.READ_ONLY_TOOL)
tool = mcp.tool


def resolve_odoo(ctx: Context, instance: Optional[str] = None) -> Tuple[str, Any]:
    """Resolve (instance_name, odoo_client) exactly like builtin tools do."""
    return _resolve_odoo(ctx, instance)


def redact_records(
    instance_name: str, model: str, records: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply the deployment's field ACL; returns (records, redacted_fields)."""
    return get_field_policy().redact_records(instance_name, model, records)


def error_envelope(tool_name: str, error: Exception | str) -> Dict[str, Any]:
    """The standard failure envelope every odoo-mcp tool returns."""
    return {"success": False, "tool": tool_name, "error": str(error)}


__all__ = [
    "PLUGIN_API_VERSION",
    "PREVIEW_TOOL",
    "READ_ONLY_TOOL",
    "clamp_limit",
    "error_envelope",
    "redact_records",
    "resolve_odoo",
    "tool",
    "validate_model_name",
]
