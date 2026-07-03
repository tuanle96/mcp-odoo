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


class PreviewWriteResponse(ToolResponse):
    model: Optional[str] = None
    operation: Optional[str] = None
    approval: Optional[Dict[str, Any]] = Field(
        default=None, description="Canonical payload plus approval token."
    )
    execute_method: Optional[Dict[str, Any]] = Field(
        default=None, description="Equivalent Odoo RPC call preview."
    )
    issues: Optional[List[Dict[str, Any]]] = None
    warnings: Optional[List[Dict[str, Any]]] = None
    metadata_used: Optional[Dict[str, Any]] = None


class ValidateWriteResponse(ToolResponse):
    model: Optional[str] = None
    operation: Optional[str] = None
    issues: Optional[List[Dict[str, Any]]] = None
    field_hints: Optional[List[Dict[str, Any]]] = None
    approval: Optional[Dict[str, Any]] = Field(
        default=None, description="Approval payload when validation succeeds."
    )
    approval_status: Optional[Dict[str, Any]] = Field(
        default=None, description="Whether the approval was stored for execution."
    )
    metadata_used: Optional[Dict[str, Any]] = None


class ExecuteApprovedWriteResponse(ToolResponse):
    model: Optional[str] = None
    operation: Optional[str] = None
    result: Optional[Any] = Field(default=None, description="Odoo RPC result.")
    instance: Optional[str] = None


class ChatterPostResponse(ToolResponse):
    mode: Optional[str] = Field(default=None, description="preview, execute, or direct.")
    model: Optional[str] = None
    record_id: Optional[int] = None
    approval_required: Optional[bool] = None
    approval: Optional[Dict[str, Any]] = None
    warnings: Optional[List[str]] = None
    result: Optional[Any] = Field(default=None, description="Odoo message_post result.")


class ExecuteMethodResponse(ToolResponse):
    result: Optional[Any] = Field(default=None, description="Odoo method result.")
    classification: Optional[Dict[str, Any]] = Field(
        default=None, description="Safety classification for blocked side effects."
    )
    rate_limited: Optional[bool] = None
