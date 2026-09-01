"""Typed per-tool response models (MCP structured output).

Every tool keeps the hand-rolled envelope convention — ``{"success": True,
...}`` on the happy path, ``{"success": False, "error": str}`` on failure —
so all payload fields are Optional and models allow extra keys. The point is
the *outputSchema* clients see in ``tools/list``: typed, documented fields
instead of a generic ``{"type": "object"}`` wrapper.

Core module: must not import the MCP surface (enforced by import-linter).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ToolResponse(BaseModel):
    """Common envelope shared by every odoo-mcp tool."""

    model_config = ConfigDict(extra="allow")

    success: bool = Field(description="False when the call failed; see error.")
    tool: Optional[str] = Field(default=None, description="Reporting tool name.")
    error: Optional[str] = Field(
        default=None, description="Sanitized error message when success is false."
    )


class ModelSummary(BaseModel):
    """One model entry from list_models / schema_catalog."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(description="Technical model name, e.g. res.partner.")
    name: str = Field(default="", description="Human display name.")


class GetOdooProfileResponse(ToolResponse):
    profile: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Server, user-context, transport, and module metadata.",
    )
    metadata_used: Optional[Dict[str, Any]] = None


class SchemaCatalogResponse(ToolResponse):
    count: Optional[int] = None
    result: Optional[List[ModelSummary]] = Field(
        default=None, description="Model entries; fields included when requested."
    )
    metadata_used: Optional[Dict[str, Any]] = None


class HealthCheckResponse(ToolResponse):
    server: Optional[Dict[str, Any]] = Field(
        default=None, description="Server name, instructions, surface counts."
    )
    runtime: Optional[Dict[str, Any]] = Field(
        default=None, description="Non-secret runtime security posture."
    )
    rate_limits: Optional[Dict[str, Any]] = None
    plugins: Optional[Dict[str, Any]] = Field(
        default=None, description="Opt-in plugin load state and tool filtering."
    )
    identity: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Identity posture: configured vs per-request mode, warnings.",
    )


class ListInstancesResponse(ToolResponse):
    default: Optional[str] = Field(
        default=None, description="Name of the default instance."
    )
    instance_count: Optional[int] = None
    instances: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Instance entries (never credentials)."
    )


class ListModelsResponse(ToolResponse):
    count: Optional[int] = None
    result: Optional[List[ModelSummary]] = None


class GetModelFieldsResponse(ToolResponse):
    count: Optional[int] = None
    result: Optional[Dict[str, Any]] = Field(
        default=None, description="Mapping of field name to fields_get metadata."
    )
    relevance_applied: Optional[bool] = None
    ranking: Optional[List[Dict[str, Any]]] = Field(
        default=None, description='Relevance scores when relevance="top".'
    )
    restricted_fields: Optional[List[str]] = Field(
        default=None, description="Fields marked restricted by the field ACL."
    )


class SearchRecordsResponse(ToolResponse):
    count: Optional[int] = None
    result: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Matched records (field-ACL redacted)."
    )
    smart_fields_applied: Optional[bool] = None
    fields_used: Optional[List[str]] = None
    query_fields_used: Optional[List[str]] = Field(
        default=None, description="Fields matched by the free-text query shortcut."
    )
    redacted_fields: Optional[List[str]] = None


class ReadRecordResponse(ToolResponse):
    result: Optional[Dict[str, Any]] = Field(
        default=None, description="The record (field-ACL redacted)."
    )
    smart_fields_applied: Optional[bool] = None
    fields_used: Optional[List[str]] = None
    redacted_fields: Optional[List[str]] = None


class BuildDomainResponse(ToolResponse):
    domain: Optional[List[Any]] = Field(
        default=None, description="Validated Odoo domain expression."
    )
    conditions: Optional[List[List[Any]]] = Field(
        default=None, description="Normalized field/operator/value conditions."
    )
    issues: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Validation errors and warnings."
    )
    metadata_used: Optional[Dict[str, Any]] = None


class ReadAttachmentResponse(ToolResponse):
    attachment: Optional[Dict[str, Any]] = Field(
        default=None, description="ir.attachment metadata row."
    )
    data_base64: Optional[str] = Field(
        default=None, description="Base64 content when under the size cap."
    )
    data_included: Optional[bool] = None
    max_bytes: Optional[int] = None
    warnings: Optional[List[str]] = None


class AggregateRecordsResponse(ToolResponse):
    method: Optional[str] = Field(
        default=None, description="formatted_read_group (19+) or read_group."
    )
    major_version: Optional[int] = None
    fallback_reason: Optional[str] = None
    model: Optional[str] = None
    group_by: Optional[List[str]] = None
    measures: Optional[List[str]] = None
    row_count: Optional[int] = None
    rows: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Aggregated group rows."
    )


class IndexKnowledgeResponse(ToolResponse):
    instance: Optional[str] = None
    model: Optional[str] = None
    indexed: Optional[int] = None
    skipped_over_budget: Optional[int] = None
    documents_in_index: Optional[int] = None
    max_documents: Optional[int] = None
    fetched: Optional[int] = None
    indexed_fields: Optional[Any] = Field(
        default=None, description="Explicit field list or 'smart selection'."
    )
    redacted_fields: Optional[List[str]] = None


class SearchKnowledgeResponse(ToolResponse):
    instance: Optional[str] = None
    model: Optional[str] = None
    query: Optional[str] = None
    results: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="BM25-ranked snippets from the local index."
    )


class KnowledgeStatsResponse(ToolResponse):
    indexes: Optional[List[Dict[str, Any]]] = None
    total_documents: Optional[int] = None
    max_documents: Optional[int] = None


class ReceivablePayableAgingResponse(ToolResponse):
    direction: Optional[str] = None
    as_of: Optional[str] = None
    buckets: Optional[Dict[str, Any]] = None
    total_outstanding: Optional[float] = None
    partner_count: Optional[int] = None
    partners: Optional[List[Dict[str, Any]]] = None
    line_count: Optional[int] = None
    skipped_lines: Optional[int] = None
    truncated: Optional[str] = None


class AccountingHealthSummaryResponse(ToolResponse):
    open_receivable_items: Optional[int] = None
    open_payable_items: Optional[int] = None
    draft_invoices: Optional[int] = None


class AsyncTaskResponse(ToolResponse):
    task_id: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    note: Optional[str] = None


class ListAsyncTasksResponse(ToolResponse):
    tasks: Optional[List[Dict[str, Any]]] = None


class SearchAcrossInstancesResponse(ToolResponse):
    model: Optional[str] = None
    merged: Optional[List[Dict[str, Any]]] = None
    merged_count: Optional[int] = None
    instances_queried: Optional[List[str]] = None
    instance_count: Optional[int] = None
    errors: Optional[Dict[str, str]] = None
    results: Optional[Dict[str, Any]] = None
    skipped_opt_out: Optional[List[str]] = None
    unknown_instances: Optional[List[str]] = None
    elapsed_ms: Optional[float] = None


class AggregateAcrossInstancesResponse(ToolResponse):
    model: Optional[str] = None
    combined_count: Optional[int] = None
    combined_measures: Optional[Dict[str, float]] = None
    instances_queried: Optional[List[str]] = None
    instance_count: Optional[int] = None
    errors: Optional[Dict[str, str]] = None
    results: Optional[Dict[str, Any]] = None
    skipped_opt_out: Optional[List[str]] = None
    unknown_instances: Optional[List[str]] = None
    elapsed_ms: Optional[float] = None


class AccountingHealthAcrossInstancesResponse(ToolResponse):
    direction: Optional[str] = None
    as_of: Optional[str] = None
    combined_buckets: Optional[Dict[str, float]] = None
    combined_total_outstanding: Optional[float] = None
    instances_queried: Optional[List[str]] = None
    instance_count: Optional[int] = None
    errors: Optional[Dict[str, str]] = None
    results: Optional[Dict[str, Any]] = None
    skipped_opt_out: Optional[List[str]] = None
    unknown_instances: Optional[List[str]] = None
    elapsed_ms: Optional[float] = None
