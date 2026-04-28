"""
MCP server for Odoo integration

Provides MCP tools and resources for interacting with Odoo ERP systems
"""

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from .diagnostics import (
    diagnose_odoo_call_report,
    fit_gap_report as build_fit_gap_report,
    generate_json2_payload_report,
    inspect_model_relationships_report,
    upgrade_risk_report as build_upgrade_risk_report,
)
from .odoo_client import OdooClient, get_odoo_client

MODEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
MAX_SEARCH_LIMIT = 100


@dataclass
class AppContext:
    """Application context with lazy Odoo client access."""

    odoo_factory: Callable[[], OdooClient] = field(
        default_factory=lambda: get_odoo_client
    )
    _odoo: OdooClient | None = None

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


# ----- MCP Resources -----


@mcp.resource(
    "odoo://models", description="List all available models in the Odoo system"
)
def get_models() -> str:
    """Lists all available models in the Odoo system"""
    odoo_client = get_odoo_client()
    models = odoo_client.get_models()
    return json.dumps(models, indent=2)


@mcp.resource(
    "odoo://model/{model_name}",
    description="Get detailed information about a specific model including fields",
)
def get_model_info(model_name: str) -> str:
    """
    Get information about a specific model

    Parameters:
        model_name: Name of the Odoo model (e.g., 'res.partner')
    """
    odoo_client = get_odoo_client()
    try:
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
        record_id_int = int(record_id)
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
        # Parse domain from JSON string
        domain_list = json.loads(domain)

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


# ----- MCP Tools -----


@mcp.tool(description="Diagnose an Odoo model call without executing it")
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


@mcp.tool(description="Build a JSON-2 request preview without network access")
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


@mcp.tool(description="Inspect model relationships and required field metadata")
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


@mcp.tool(description="Report Odoo upgrade and JSON-2 migration risks")
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


@mcp.tool(description="Classify Odoo requirements into fit/gap implementation buckets")
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


@mcp.tool(description="Execute a custom method on an Odoo model")
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
    odoo = ctx.request_context.lifespan_context.odoo
    try:
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

        result = odoo.execute_method(model, method, *args, **kwargs)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(description="List Odoo models with optional name filtering")
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


@mcp.tool(description="Get field metadata for a specific Odoo model")
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


@mcp.tool(description="Search Odoo records with read-only search_read")
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


@mcp.tool(description="Read a single Odoo record by model and ID")
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


@mcp.tool(description="Search for employees by name")
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


@mcp.tool(description="Search for holidays within a date range")
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
