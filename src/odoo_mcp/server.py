"""
MCP server for Odoo integration

Provides MCP tools and resources for interacting with Odoo ERP systems
"""

import json
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import Annotations, ToolAnnotations
from pydantic import BaseModel, Field

from .agent_tools import (
    build_domain_report,
    build_write_preview_report,
    business_pack_report as build_business_pack_report,
    scan_addons_source_report,
    validate_write_report,
    verify_write_approval,
)
from .diagnostics import (
    DESTRUCTIVE_METHODS,
    classify_method_safety,
    diagnose_odoo_call_report,
    fit_gap_report as build_fit_gap_report,
    generate_json2_payload_report,
    inspect_model_relationships_report,
    upgrade_risk_report as build_upgrade_risk_report,
)
from .odoo_client import OdooClient, get_odoo_client

MODEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
METHOD_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
MAX_SEARCH_LIMIT = 100
WRITE_APPROVAL_TTL_SECONDS = 10 * 60


@dataclass
class AppContext:
    """Application context with lazy Odoo client access."""

    odoo_factory: Callable[[], OdooClient] = field(
        default_factory=lambda: get_odoo_client
    )
    _odoo: OdooClient | None = None
    schema_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    write_approvals: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def odoo(self) -> OdooClient:
        """Resolve the Odoo client only when a live Odoo tool needs it."""
        if self._odoo is None:
            self._odoo = self.odoo_factory()
        return self._odoo


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """
    Application lifespan for initialization and cleanup
    """
    yield AppContext()


# Create MCP server
mcp = FastMCP(
    "Odoo MCP Server",
    instructions="MCP Server for interacting with Odoo ERP systems",
    dependencies=["requests"],
    lifespan=app_lifespan,
)

READ_ONLY_TOOL = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
PREVIEW_TOOL = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE_TOOL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
RESOURCE_HINT = Annotations(audience=["assistant"], priority=0.8)


# ----- MCP Resources -----


@mcp.resource(
    "odoo://models",
    description="List all available models in the Odoo system",
    mime_type="application/json",
    annotations=RESOURCE_HINT,
)
def get_models() -> str:
    """Lists all available models in the Odoo system"""
    odoo_client = get_odoo_client()
    models = odoo_client.get_models()
    return json.dumps(models, indent=2)


@mcp.resource(
    "odoo://model/{model_name}",
    description="Get detailed information about a specific model including fields",
    mime_type="application/json",
    annotations=RESOURCE_HINT,
)
def get_model_info(model_name: str) -> str:
    """
    Get information about a specific model

    Parameters:
        model_name: Name of the Odoo model (e.g., 'res.partner')
    """
    odoo_client = get_odoo_client()
    try:
        validate_model_name(model_name)
        # Get model info
        model_info = odoo_client.get_model_info(model_name)

        # Get field definitions
        fields = odoo_client.get_model_fields(model_name)
        model_info["fields"] = fields

        return json.dumps(model_info, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource(
    "odoo://record/{model_name}/{record_id}",
    description="Get detailed information of a specific record by ID",
    mime_type="application/json",
    annotations=RESOURCE_HINT,
)
def get_record(model_name: str, record_id: str) -> str:
    """
    Get a specific record by ID

    Parameters:
        model_name: Name of the Odoo model (e.g., 'res.partner')
        record_id: ID of the record
    """
    odoo_client = get_odoo_client()
    try:
        validate_model_name(model_name)
        record_id_int = int(record_id)
        if record_id_int < 1:
            raise ValueError("record_id must be greater than 0")
        record = odoo_client.read_records(model_name, [record_id_int])
        if not record:
            return json.dumps(
                {"error": f"Record not found: {model_name} ID {record_id}"}, indent=2
            )
        return json.dumps(record[0], indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource(
    "odoo://search/{model_name}/{domain}",
    description="Search for records matching the domain",
    mime_type="application/json",
    annotations=RESOURCE_HINT,
)
def search_records_resource(model_name: str, domain: str) -> str:
    """
    Search for records that match a domain

    Parameters:
        model_name: Name of the Odoo model (e.g., 'res.partner')
        domain: Search domain in JSON format (e.g., '[["name", "ilike", "test"]]')
    """
    odoo_client = get_odoo_client()
    try:
        validate_model_name(model_name)
        # Parse domain from JSON string
        domain_list = json.loads(domain)
        if not isinstance(domain_list, list):
            raise ValueError("domain must decode to an Odoo domain list")

        # Set a reasonable default limit
        limit = 10

        # Perform search_read for efficiency
        results = odoo_client.search_read(model_name, domain_list, limit=limit)

        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


# ----- Pydantic models for type safety -----


class DomainCondition(BaseModel):
    """A single condition in a search domain"""

    field: str = Field(description="Field name to search")
    operator: str = Field(
        description="Operator (e.g., '=', '!=', '>', '<', 'in', 'not in', 'like', 'ilike')"
    )
    value: Any = Field(description="Value to compare against")

    def to_tuple(self) -> List:
        """Convert to Odoo domain condition tuple"""
        return [self.field, self.operator, self.value]


class SearchDomain(BaseModel):
    """Search domain for Odoo models"""

    conditions: List[DomainCondition] = Field(
        default_factory=list,
        description="List of conditions for searching. All conditions are combined with AND operator.",
    )

    def to_domain_list(self) -> List[List]:
        """Convert to Odoo domain list format"""
        return [condition.to_tuple() for condition in self.conditions]


class EmployeeSearchResult(BaseModel):
    """Represents a single employee search result."""

    id: int = Field(description="Employee ID")
    name: str = Field(description="Employee name")


class SearchEmployeeResponse(BaseModel):
    """Response model for the search_employee tool."""

    success: bool = Field(description="Indicates if the search was successful")
    result: Optional[List[EmployeeSearchResult]] = Field(
        default=None, description="List of employee search results"
    )
    error: Optional[str] = Field(default=None, description="Error message, if any")


class Holiday(BaseModel):
    """Represents a single holiday."""

    display_name: str = Field(description="Display name of the holiday")
    start_datetime: str = Field(description="Start date and time of the holiday")
    stop_datetime: str = Field(description="End date and time of the holiday")
    employee_id: List[Union[int, str]] = Field(
        description="Employee ID associated with the holiday"
    )
    name: str = Field(description="Name of the holiday")
    state: str = Field(description="State of the holiday")


class SearchHolidaysResponse(BaseModel):
    """Response model for the search_holidays tool."""

    success: bool = Field(description="Indicates if the search was successful")
    result: Optional[List[Holiday]] = Field(
        default=None, description="List of holidays found"
    )
    error: Optional[str] = Field(default=None, description="Error message, if any")


def validate_model_name(model_name: str) -> None:
    """Reject obviously unsafe model names before forwarding to Odoo."""
    if not MODEL_NAME_RE.fullmatch(model_name):
        raise ValueError(
            "Invalid model name. Use Odoo technical model names like 'res.partner'."
        )


def validate_method_name(method_name: str) -> None:
    """Reject obviously unsafe method names before forwarding to Odoo."""
    if not METHOD_NAME_RE.fullmatch(method_name):
        raise ValueError("Invalid method name. Use Odoo method names like 'search_read'.")


def clamp_limit(limit: int, maximum: int = MAX_SEARCH_LIMIT) -> int:
    """Keep read-only tools bounded for agent safety."""
    if limit < 1:
        raise ValueError("limit must be greater than 0")
    return min(limit, maximum)


def normalize_domain_input(domain: Any) -> List[Any]:
    """Normalize common MCP/JSON domain shapes to an Odoo domain list."""
    if domain is None:
        return []
    if isinstance(domain, SearchDomain):
        return domain.to_domain_list()

    domain_value = domain
    if isinstance(domain_value, str):
        try:
            domain_value = json.loads(domain_value)
        except json.JSONDecodeError:
            try:
                import ast

                domain_value = ast.literal_eval(domain_value)
            except (SyntaxError, ValueError):
                return []

    if isinstance(domain_value, dict):
        conditions = domain_value.get("conditions")
        if isinstance(conditions, list):
            return [
                [cond["field"], cond["operator"], cond["value"]]
                for cond in conditions
                if isinstance(cond, dict)
                and all(k in cond for k in ["field", "operator", "value"])
            ]
        return []

    if not isinstance(domain_value, list):
        return []

    if len(domain_value) == 1 and isinstance(domain_value[0], list) and domain_value[0]:
        domain_value = domain_value[0]

    if not domain_value:
        return []
    if (
        len(domain_value) == 3
        and isinstance(domain_value[0], str)
        and domain_value[0] not in ["&", "|", "!"]
        and isinstance(domain_value[1], str)
    ):
        domain_list = [domain_value]
    else:
        domain_list = domain_value

    valid_conditions: List[Any] = []
    for cond in domain_list:
        if isinstance(cond, str) and cond in ["&", "|", "!"]:
            valid_conditions.append(cond)
            continue
        if (
            isinstance(cond, list)
            and len(cond) == 3
            and isinstance(cond[0], str)
            and isinstance(cond[1], str)
        ):
            valid_conditions.append(cond)

    return valid_conditions


def truthy_env(name: str) -> bool:
    """Read a common boolean environment flag."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def writes_enabled() -> bool:
    """Return whether destructive approved writes are enabled for this process."""
    return truthy_env("ODOO_MCP_ENABLE_WRITES")


def runtime_security_report() -> Dict[str, Any]:
    """Expose MCP runtime safety posture without including secrets."""
    security = getattr(mcp.settings, "transport_security", None)
    return {
        "transport": os.environ.get("MCP_TRANSPORT", "stdio"),
        "host": getattr(mcp.settings, "host", None),
        "port": getattr(mcp.settings, "port", None),
        "streamable_http_path": getattr(mcp.settings, "streamable_http_path", None),
        "remote_http_allowed": truthy_env("MCP_ALLOW_REMOTE_HTTP"),
        "write_execution_enabled": writes_enabled(),
        "unknown_execute_method_enabled": truthy_env(
            "ODOO_MCP_ALLOW_UNKNOWN_METHODS"
        ),
        "allowed_hosts": getattr(security, "allowed_hosts", None),
        "allowed_origins": getattr(security, "allowed_origins", None),
        "notes": [
            "HTTP transports are local-only by default in the CLI entry point.",
            "execute_approved_write requires ODOO_MCP_ENABLE_WRITES and confirm=true.",
            "execute_method blocks standard destructive methods and unknown side-effect methods by default.",
        ],
    }


def mcp_surface_counts() -> Dict[str, int]:
    """Read current registered MCP surface counts from FastMCP managers."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    resource_manager = getattr(mcp, "_resource_manager", None)
    prompt_manager = getattr(mcp, "_prompt_manager", None)
    resources = getattr(resource_manager, "_resources", {})
    templates = getattr(resource_manager, "_templates", {})
    return {
        "tool_count": len(getattr(tool_manager, "_tools", {})),
        "resource_count": len(resources) + len(templates),
        "prompt_count": len(getattr(prompt_manager, "_prompts", {})),
    }


def register_write_approval(app_context: AppContext, report: Dict[str, Any]) -> bool:
    """Persist validated write approvals inside the current server lifespan."""
    approval = report.get("approval")
    if not report.get("success") or not isinstance(approval, dict):
        return False
    token = str(approval.get("token", ""))
    if not token:
        return False
    now = time.time()
    app_context.write_approvals[token] = {
        "approval": dict(approval),
        "payload": write_approval_payload(approval),
        "validated_at": now,
        "expires_at": now + WRITE_APPROVAL_TTL_SECONDS,
    }
    approval["validated_at"] = now
    approval["expires_at"] = now + WRITE_APPROVAL_TTL_SECONDS
    return True


def require_validated_write_approval(
    app_context: AppContext, approval: Dict[str, Any]
) -> Dict[str, Any] | None:
    """Return a server-side validation record or None when it is missing/expired."""
    token = str(approval.get("token", ""))
    record = app_context.write_approvals.get(token)
    if record is None:
        return None
    if time.time() > float(record.get("expires_at", 0)):
        app_context.write_approvals.pop(token, None)
        return None
    return record


def write_approval_payload(approval: Dict[str, Any]) -> Dict[str, Any]:
    """Return the canonical approval payload fields used for execution."""
    return {
        "model": approval.get("model"),
        "operation": approval.get("operation"),
        "record_ids": approval.get("record_ids") or [],
        "values": approval.get("values") or {},
        "context": approval.get("context") or {},
    }


def configured_addons_roots() -> List[Path]:
    """Return trusted local addon roots configured by the operator."""
    roots: List[Path] = []
    for raw_path in os.environ.get("ODOO_ADDONS_PATHS", "").split(os.pathsep):
        if not raw_path:
            continue
        roots.append(Path(raw_path).expanduser().resolve(strict=False))
    return roots


def restrict_addons_paths(addons_paths: Optional[List[str]]) -> Optional[List[str]]:
    """Restrict source scans to ODOO_ADDONS_PATHS roots."""
    if addons_paths is None:
        return None
    roots = configured_addons_roots()
    if not roots:
        raise ValueError(
            "scan_addons_source requires ODOO_ADDONS_PATHS when addons_paths are provided."
        )

    restricted_paths: List[str] = []
    for raw_path in addons_paths:
        candidate = Path(raw_path).expanduser().resolve(strict=False)
        if not any(candidate == root or _is_relative_to(candidate, root) for root in roots):
            raise ValueError(
                f"{candidate} is outside configured ODOO_ADDONS_PATHS roots."
            )
        restricted_paths.append(str(candidate))
    return restricted_paths


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ----- MCP Tools -----


@mcp.tool(
    description="Diagnose an Odoo model call without executing it",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def diagnose_odoo_call(
    model: str,
    method: str,
    args: Optional[List[Any]] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    transport: str = "auto",
    target_version: Optional[str] = None,
    observed_error: Optional[Any] = None,
    include_debug: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    use_live_metadata: bool = False,
) -> Dict[str, Any]:
    """
    Diagnose model/method/payload issues without executing the candidate call.
    """
    report = diagnose_odoo_call_report(
        model=model,
        method=method,
        args=args,
        kwargs=kwargs,
        transport=transport,
        target_version=target_version,
        observed_error=observed_error,
        include_debug=include_debug,
        metadata=metadata,
    )
    if use_live_metadata:
        report["issues"].append(
            {
                "code": "live_metadata_not_used",
                "severity": "info",
                "message": (
                    "diagnose_odoo_call is preview-only; pass metadata explicitly "
                    "or use inspect_model_relationships for live fields_get metadata."
                ),
            }
        )
    return report


@mcp.tool(
    description="Build a JSON-2 request preview without network access",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def generate_json2_payload(
    model: str,
    method: str,
    args: Optional[List[Any]] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    database: Optional[str] = None,
    include_database_header: bool = True,
) -> Dict[str, Any]:
    """
    Generate a JSON-2 endpoint, headers, and named JSON body.
    """
    return generate_json2_payload_report(
        model=model,
        method=method,
        args=args,
        kwargs=kwargs,
        base_url=base_url,
        database=database,
        include_database_header=include_database_header,
    )


@mcp.tool(
    description="Inspect model relationships and required field metadata",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def inspect_model_relationships(
    ctx: Context,
    model: str,
    fields_metadata: Optional[Dict[str, Any]] = None,
    include_readonly: bool = True,
    include_computed: bool = True,
    use_live_metadata: bool = True,
) -> Dict[str, Any]:
    """
    Summarize relationship fields using provided metadata or bounded fields_get.
    """
    try:
        validate_model_name(model)
        metadata_source = "input" if fields_metadata is not None else "none"
        metadata_error = None
        if fields_metadata is None and use_live_metadata:
            metadata_source = "server"
            try:
                odoo = ctx.request_context.lifespan_context.odoo
                fields_metadata = odoo.get_model_fields(model)
                if "error" in fields_metadata:
                    metadata_error = str(fields_metadata["error"])
                    fields_metadata = None
            except Exception as exc:
                metadata_error = str(exc)
                fields_metadata = None
        return inspect_model_relationships_report(
            model=model,
            fields_metadata=fields_metadata,
            metadata_source=metadata_source,
            metadata_error=metadata_error,
            include_readonly=include_readonly,
            include_computed=include_computed,
        )
    except Exception as e:
        return {"success": False, "tool": "inspect_model_relationships", "error": str(e)}


@mcp.tool(
    description="Report Odoo upgrade and JSON-2 migration risks",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def upgrade_risk_report(
    source_version: Optional[str] = None,
    target_version: Optional[str] = None,
    modules: Optional[List[Dict[str, Any]]] = None,
    methods: Optional[List[Dict[str, Any]]] = None,
    source_findings: Optional[List[Dict[str, Any]]] = None,
    observed_errors: Optional[List[Any]] = None,
    use_live_metadata: bool = False,
    include_debug: bool = False,
) -> Dict[str, Any]:
    """
    Build an input-driven upgrade risk report without executing Odoo calls.
    """
    report = build_upgrade_risk_report(
        source_version=source_version,
        target_version=target_version,
        modules=modules,
        methods=methods,
        source_findings=source_findings,
        observed_errors=observed_errors,
        include_debug=include_debug,
    )
    if use_live_metadata:
        report["risks"].append(
            {
                "code": "live_metadata_not_used",
                "severity": "info",
                "evidence": "upgrade_risk_report is input-driven in this release.",
                "recommendation": "Pass module/method/source findings explicitly.",
            }
        )
    return report


@mcp.tool(
    description="Classify Odoo requirements into fit/gap implementation buckets",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def fit_gap_report(
    requirements: List[Any],
    available_models: Optional[List[str]] = None,
    available_fields: Optional[Dict[str, Any]] = None,
    installed_modules: Optional[List[Any]] = None,
    business_context: Optional[Dict[str, Any]] = None,
    use_live_metadata: bool = False,
) -> Dict[str, Any]:
    """
    Normalize requirements into standard/config/Studio/custom/avoid/unknown buckets.
    """
    report = build_fit_gap_report(
        requirements=requirements,
        available_models=available_models,
        available_fields=available_fields,
        installed_modules=installed_modules,
        business_context=business_context,
    )
    if use_live_metadata:
        report["assumptions"].append(
            "fit_gap_report is input-driven in this release; use list_models/get_model_fields first."
        )
    return report


@mcp.tool(
    description="Read a bounded profile of the connected Odoo environment",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def get_odoo_profile(
    ctx: Context,
    include_modules: bool = True,
    module_limit: int = 100,
) -> Dict[str, Any]:
    """Return server, user-context, transport, and installed-module metadata."""
    try:
        module_limit = clamp_limit(module_limit, maximum=500)
        odoo = ctx.request_context.lifespan_context.odoo
        if include_modules:
            profile = odoo.get_profile(module_limit=module_limit)
        else:
            profile = {
                "url": getattr(odoo, "url", None),
                "hostname": getattr(odoo, "hostname", None),
                "database": getattr(odoo, "db", None),
                "username": getattr(odoo, "username", None),
                "transport": getattr(odoo, "transport", None),
                "timeout": getattr(odoo, "timeout", None),
                "verify_ssl": getattr(odoo, "verify_ssl", None),
                "json2_database_header": getattr(
                    odoo, "json2_database_header", None
                ),
                "server_version": odoo.get_server_version(),
                "user_context": odoo.get_user_context(),
                "installed_modules": [],
                "installed_module_count": None,
            }
        return {
            "success": True,
            "tool": "get_odoo_profile",
            "profile": profile,
            "metadata_used": {
                "live_odoo": True,
                "installed_modules": include_modules,
            },
        }
    except Exception as e:
        return {"success": False, "tool": "get_odoo_profile", "error": str(e)}


@mcp.tool(
    description="Build and cache a bounded Odoo model schema catalog",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def schema_catalog(
    ctx: Context,
    query: Optional[str] = None,
    models: Optional[List[str]] = None,
    include_fields: bool = False,
    refresh: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return a cached catalog of model names, labels, and optional fields."""
    try:
        limit = clamp_limit(limit, maximum=500)
        if models:
            for model_name in models:
                validate_model_name(model_name)

        app_context = ctx.request_context.lifespan_context
        cache_key = json.dumps(
            {
                "query": query,
                "models": sorted(models or []),
                "include_fields": include_fields,
                "limit": limit,
            },
            sort_keys=True,
        )
        if not refresh and cache_key in app_context.schema_cache:
            cached = dict(app_context.schema_cache[cache_key])
            cached["metadata_used"] = {**cached["metadata_used"], "cache_hit": True}
            return cached

        odoo = app_context.odoo
        raw_models = odoo.get_models()
        if "error" in raw_models:
            return {
                "success": False,
                "tool": "schema_catalog",
                "error": raw_models["error"],
            }

        model_names = list(raw_models.get("model_names", []))
        model_details = raw_models.get("models_details", {})
        if models:
            model_filter = set(models)
            model_names = [name for name in model_names if name in model_filter]
        if query:
            query_lower = query.lower()
            model_names = [
                name
                for name in model_names
                if query_lower in name.lower()
                or query_lower
                in str(model_details.get(name, {}).get("name", "")).lower()
            ]

        records: List[Dict[str, Any]] = []
        for model_name in model_names[:limit]:
            record: Dict[str, Any] = {
                "model": model_name,
                "name": model_details.get(model_name, {}).get("name", ""),
            }
            if include_fields:
                fields = odoo.get_model_fields(model_name)
                record["fields"] = fields if "error" not in fields else {}
                record["field_error"] = fields.get("error") if "error" in fields else None
            records.append(record)

        report = {
            "success": True,
            "tool": "schema_catalog",
            "count": len(records),
            "result": records,
            "metadata_used": {
                "live_odoo": True,
                "fields_get": include_fields,
                "cache_hit": False,
            },
        }
        app_context.schema_cache[cache_key] = dict(report)
        return report
    except Exception as e:
        return {"success": False, "tool": "schema_catalog", "error": str(e)}


@mcp.tool(
    description="Preview create, write, or unlink without executing it",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def preview_write(
    model: str,
    operation: str,
    values: Optional[Dict[str, Any]] = None,
    record_ids: Optional[List[int]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a canonical approval token for a later approved write."""
    try:
        validate_model_name(model)
        return build_write_preview_report(
            model=model,
            operation=operation,
            values=values,
            record_ids=record_ids,
            context=context,
        )
    except Exception as e:
        return {"success": False, "tool": "preview_write", "error": str(e)}


@mcp.tool(
    description="Validate a standard write payload against optional fields_get metadata",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def validate_write(
    ctx: Context,
    model: str,
    operation: str,
    values: Optional[Dict[str, Any]] = None,
    record_ids: Optional[List[int]] = None,
    context: Optional[Dict[str, Any]] = None,
    fields_metadata: Optional[Dict[str, Any]] = None,
    use_live_metadata: bool = True,
) -> Dict[str, Any]:
    """Validate write shape and return an approval payload when safe."""
    try:
        validate_model_name(model)
        metadata_source = "input" if fields_metadata is not None else "none"
        if fields_metadata is None and use_live_metadata:
            metadata_source = "server"
            fields_metadata = ctx.request_context.lifespan_context.odoo.get_model_fields(
                model
            )
            if "error" in fields_metadata:
                return {
                    "success": False,
                    "tool": "validate_write",
                    "error": fields_metadata["error"],
                    "metadata_used": {"fields_get": False, "source": metadata_source},
                }
            if not fields_metadata:
                return {
                    "success": False,
                    "tool": "validate_write",
                    "error": "live fields_get metadata was empty; refusing to approve writes",
                    "metadata_used": {"fields_get": False, "source": metadata_source},
                    "approval_status": {
                        "stored": False,
                        "source": metadata_source,
                        "reason": "trusted live metadata was empty",
                    },
                }
        report = validate_write_report(
            model=model,
            operation=operation,
            values=values,
            record_ids=record_ids,
            context=context,
            fields_metadata=fields_metadata,
            metadata_source=metadata_source,
        )
        trusted_live_metadata = (
            metadata_source == "server"
            and isinstance(fields_metadata, dict)
            and bool(fields_metadata)
        )
        if trusted_live_metadata:
            stored = register_write_approval(
                ctx.request_context.lifespan_context, report
            )
            report["approval_status"] = {
                "stored": stored,
                "expires_in_seconds": WRITE_APPROVAL_TTL_SECONDS,
                "source": metadata_source,
            }
        else:
            report["approval_status"] = {
                "stored": False,
                "source": metadata_source,
                "reason": (
                    "execute_approved_write requires validation against trusted "
                    "live Odoo fields_get metadata"
                ),
            }
        return report
    except Exception as e:
        return {"success": False, "tool": "validate_write", "error": str(e)}


@mcp.tool(
    description="Execute a previously previewed and confirmed standard write",
    annotations=DESTRUCTIVE_TOOL,
    structured_output=True,
)
def execute_approved_write(
    ctx: Context,
    approval: Dict[str, Any],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Execute create/write/unlink only after token, confirm, and env gates pass."""
    try:
        is_valid, expected_token = verify_write_approval(approval)
        if not is_valid:
            return {
                "success": False,
                "tool": "execute_approved_write",
                "error": "approval token does not match the canonical payload",
                "expected_token": expected_token,
            }
        app_context = ctx.request_context.lifespan_context
        validation_record = require_validated_write_approval(app_context, approval)
        if validation_record is None:
            return {
                "success": False,
                "tool": "execute_approved_write",
                "error": (
                    "approval token has not been validated in this server session "
                    "or has expired; call validate_write first"
                ),
            }
        if write_approval_payload(approval) != validation_record.get("payload"):
            return {
                "success": False,
                "tool": "execute_approved_write",
                "error": "approval payload does not match the stored validation record",
            }
        if not confirm:
            return {
                "success": False,
                "tool": "execute_approved_write",
                "error": "confirm=true is required for destructive execution",
            }
        if not writes_enabled():
            return {
                "success": False,
                "tool": "execute_approved_write",
                "error": "write execution disabled; set ODOO_MCP_ENABLE_WRITES=1 to enable",
            }

        model = str(approval.get("model", ""))
        operation = str(approval.get("operation", "")).strip().lower()
        validate_model_name(model)
        if operation not in {"create", "write", "unlink"}:
            raise ValueError("operation must be one of create, write, or unlink")

        values = dict(approval.get("values") or {})
        record_ids = [int(record_id) for record_id in approval.get("record_ids") or []]
        context = dict(approval.get("context") or {})
        kwargs = {"context": context} if context else {}
        if operation == "create":
            args: List[Any] = [values]
        elif operation == "write":
            args = [record_ids, values]
        else:
            args = [record_ids]

        result = app_context.odoo.execute_method(
            model, operation, *args, **kwargs
        )
        app_context.write_approvals.pop(str(approval.get("token", "")), None)
        return {
            "success": True,
            "tool": "execute_approved_write",
            "model": model,
            "operation": operation,
            "result": result,
        }
    except Exception as e:
        return {"success": False, "tool": "execute_approved_write", "error": str(e)}


@mcp.tool(
    description="Scan local Odoo addon source without importing addon code",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def scan_addons_source(
    addons_paths: Optional[List[str]] = None,
    max_files: int = 200,
    max_file_bytes: int = 300_000,
) -> Dict[str, Any]:
    """Summarize manifests, custom models, risky methods, views, and ACL files."""
    try:
        max_files = clamp_limit(max_files, maximum=1000)
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be greater than 0")
        return scan_addons_source_report(
            addons_paths=restrict_addons_paths(addons_paths),
            max_files=max_files,
            max_file_bytes=max_file_bytes,
        )
    except Exception as e:
        return {"success": False, "tool": "scan_addons_source", "error": str(e)}


@mcp.tool(
    description="Build a validated Odoo domain from structured conditions",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def build_domain(
    conditions: List[Dict[str, Any]],
    logical_operator: str = "and",
    fields_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build safe domain arrays for search_records and Odoo ORM calls."""
    try:
        return build_domain_report(
            conditions=conditions,
            logical_operator=logical_operator,
            fields_metadata=fields_metadata,
        )
    except Exception as e:
        return {"success": False, "tool": "build_domain", "error": str(e)}


@mcp.tool(
    description="Report expected modules, models, and safe discovery calls for a business pack",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def business_pack_report(
    ctx: Context,
    pack: str,
    use_live_metadata: bool = True,
) -> Dict[str, Any]:
    """Summarize a domain pack such as sales, crm, inventory, accounting, or hr."""
    try:
        available_models: List[str] | None = None
        installed_modules: List[str] | None = None
        if use_live_metadata:
            odoo = ctx.request_context.lifespan_context.odoo
            models_report = odoo.get_models()
            if "error" not in models_report:
                available_models = list(models_report.get("model_names", []))
            installed_modules = [
                str(module.get("name"))
                for module in odoo.get_installed_modules(limit=200)
                if module.get("name")
            ]
        return build_business_pack_report(
            pack=pack,
            available_models=available_models,
            installed_modules=installed_modules,
        )
    except Exception as e:
        return {"success": False, "tool": "business_pack_report", "error": str(e)}


@mcp.tool(
    description="Report this MCP server's non-secret runtime safety posture",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def health_check() -> Dict[str, Any]:
    """Return local process health and hardening flags without opening Odoo."""
    surface_counts = mcp_surface_counts()
    return {
        "success": True,
        "tool": "health_check",
        "server": {
            "name": mcp.name,
            "instructions": mcp.instructions,
            **surface_counts,
        },
        "runtime": runtime_security_report(),
    }


@mcp.tool(
    description="Execute a custom method on an Odoo model",
    annotations=DESTRUCTIVE_TOOL,
    structured_output=True,
)
def execute_method(
    ctx: Context,
    model: str,
    method: str,
    args: Optional[List[Any]] = None,
    kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute a custom method on an Odoo model

    Parameters:
        model: The model name (e.g., 'res.partner')
        method: Method name to execute
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        Dictionary containing:
        - success: Boolean indicating success
        - result: Result of the method (if success)
        - error: Error message (if failure)
    """
    try:
        validate_model_name(model)
        validate_method_name(method)
        safety = classify_method_safety(method)
        if method in DESTRUCTIVE_METHODS:
            return {
                "success": False,
                "error": (
                    "Direct execute_method blocks create/write/unlink. Use "
                    "preview_write -> validate_write -> execute_approved_write."
                ),
            }
        if safety["safety"] == "unknown" and not truthy_env(
            "ODOO_MCP_ALLOW_UNKNOWN_METHODS"
        ):
            return {
                "success": False,
                "error": (
                    "Unknown side-effect methods are blocked by default. Review "
                    "custom source and set ODOO_MCP_ALLOW_UNKNOWN_METHODS=1 only "
                    "for trusted deployments."
                ),
            }
        args = args or []
        kwargs = kwargs or {}

        # Special handling for search methods like search, search_count, search_read
        search_methods = ["search", "search_count", "search_read"]
        if method in search_methods and args:
            # Search methods usually have domain as the first parameter
            # args: [[domain], limit, offset, ...] or [domain, limit, offset, ...]
            normalized_args = list(
                args
            )  # Create a copy to avoid affecting the original args

            if len(normalized_args) > 0:
                normalized_args[0] = normalize_domain_input(normalized_args[0])
                args = normalized_args

        odoo = ctx.request_context.lifespan_context.odoo
        result = odoo.execute_method(model, method, *args, **kwargs)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    description="List Odoo models with optional name filtering",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def list_models(
    ctx: Context,
    query: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    List available Odoo model technical names and display names.

    Prefer this read-only tool over execute_method when discovering models.
    """
    odoo = ctx.request_context.lifespan_context.odoo
    try:
        limit = clamp_limit(limit, maximum=500)
        models = odoo.get_models()
        if "error" in models:
            return {"success": False, "error": models["error"]}

        model_names = models.get("model_names", [])
        models_details = models.get("models_details", {})
        if query:
            query_lower = query.lower()
            model_names = [
                model_name
                for model_name in model_names
                if query_lower in model_name.lower()
                or query_lower
                in str(models_details.get(model_name, {}).get("name", "")).lower()
            ]

        records = [
            {
                "model": model_name,
                "name": models_details.get(model_name, {}).get("name", ""),
            }
            for model_name in model_names[:limit]
        ]
        return {"success": True, "count": len(records), "result": records}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    description="Get field metadata for a specific Odoo model",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def get_model_fields(
    ctx: Context,
    model: str,
    field_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Read field definitions for a model.

    Prefer this read-only tool over execute_method for model introspection.
    """
    odoo = ctx.request_context.lifespan_context.odoo
    try:
        validate_model_name(model)
        fields = odoo.get_model_fields(model)
        if "error" in fields:
            return {"success": False, "error": fields["error"]}
        if field_names:
            fields = {name: fields[name] for name in field_names if name in fields}
        return {"success": True, "count": len(fields), "result": fields}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    description="Search Odoo records with read-only search_read",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def search_records(
    ctx: Context,
    model: str,
    domain: Optional[Any] = None,
    fields: Optional[List[str]] = None,
    limit: int = 10,
    offset: int = 0,
    order: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search and read records with bounded read-only semantics.

    Domain accepts standard Odoo domain arrays, a JSON string, or
    {"conditions": [{"field": ..., "operator": ..., "value": ...}]}.
    """
    odoo = ctx.request_context.lifespan_context.odoo
    try:
        validate_model_name(model)
        limit = clamp_limit(limit)
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
        records = odoo.search_read(
            model_name=model,
            domain=normalize_domain_input(domain),
            fields=fields,
            offset=offset,
            limit=limit,
            order=order,
        )
        return {"success": True, "count": len(records), "result": records}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    description="Read a single Odoo record by model and ID",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def read_record(
    ctx: Context,
    model: str,
    record_id: int,
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Read one record by ID with bounded read-only semantics.
    """
    odoo = ctx.request_context.lifespan_context.odoo
    try:
        validate_model_name(model)
        if record_id < 1:
            raise ValueError("record_id must be greater than 0")
        records = odoo.read_records(model, [record_id], fields=fields)
        if not records:
            return {
                "success": False,
                "error": f"Record not found: {model} ID {record_id}",
            }
        return {"success": True, "result": records[0]}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    description="Search for employees by name",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def search_employee(
    ctx: Context,
    name: str,
    limit: int = 20,
) -> SearchEmployeeResponse:
    """
    Search for employees by name using Odoo's name_search method.

    Parameters:
        name: The name (or part of the name) to search for.
        limit: The maximum number of results to return (default 20).

    Returns:
        SearchEmployeeResponse containing results or error information.
    """
    odoo = ctx.request_context.lifespan_context.odoo
    model = "hr.employee"
    method = "name_search"

    args: List[Any] = []
    kwargs: Dict[str, Any] = {"name": name, "limit": limit}

    try:
        result = odoo.execute_method(model, method, *args, **kwargs)
        parsed_result = [
            EmployeeSearchResult(id=item[0], name=item[1]) for item in result
        ]
        return SearchEmployeeResponse(success=True, result=parsed_result)
    except Exception as e:
        return SearchEmployeeResponse(success=False, error=str(e))


@mcp.tool(
    description="Search for holidays within a date range",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def search_holidays(
    ctx: Context,
    start_date: str,
    end_date: str,
    employee_id: Optional[int] = None,
) -> SearchHolidaysResponse:
    """
    Searches for holidays within a specified date range.

    Parameters:
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        employee_id: Optional employee ID to filter holidays.

    Returns:
        SearchHolidaysResponse:  Object containing the search results.
    """
    odoo = ctx.request_context.lifespan_context.odoo

    # Validate date format using datetime
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return SearchHolidaysResponse(
            success=False, error="Invalid start_date format. Use YYYY-MM-DD."
        )
    try:
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return SearchHolidaysResponse(
            success=False, error="Invalid end_date format. Use YYYY-MM-DD."
        )

    # Calculate adjusted start_date (subtract one day)
    start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
    adjusted_start_date_dt = start_date_dt - timedelta(days=1)
    adjusted_start_date = adjusted_start_date_dt.strftime("%Y-%m-%d")

    # Build the domain
    domain: List[Any] = [
        "&",
        ["start_datetime", "<=", f"{end_date} 22:59:59"],
        # Use adjusted date
        ["stop_datetime", ">=", f"{adjusted_start_date} 23:00:00"],
    ]
    if employee_id:
        domain.append(
            ["employee_id", "=", employee_id],
        )

    try:
        holidays = odoo.search_read(
            model_name="hr.leave.report.calendar",
            domain=domain,
        )
        parsed_holidays = [Holiday(**holiday) for holiday in holidays]
        return SearchHolidaysResponse(success=True, result=parsed_holidays)

    except Exception as e:
        return SearchHolidaysResponse(success=False, error=str(e))


# ----- MCP Prompts -----


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
        "Use diagnose_odoo_call, inspect_model_relationships, and get_model_fields "
        "before execute_method. Preserve Odoo error details, but do not expose secrets."
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
        "Prioritize manifest dependencies, overridden create/write/unlink methods, "
        "sudo usage, automated actions, custom views, and security CSV files."
    )
