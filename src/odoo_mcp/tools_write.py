"""
MCP tools: write domain.

Includes: preview_write, validate_write, execute_approved_write,
chatter_post, execute_method, write_field_from_file + WriteConfirmation +
elicitation logic.
"""

import base64
import hashlib
import json
import os
import stat
import xmlrpc.client
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from mcp.server.fastmcp import Context
from pydantic import Field

from .agent_tools import (
    build_approval_token,
    build_write_preview_report,
    validate_write_report,
    verify_write_approval,
)
from .audit import record_write_event
from .diagnostics import DESTRUCTIVE_METHODS, classify_method_safety
from .tool_helpers import (
    max_attachment_upload_bytes,
    max_field_file_bytes,
    normalize_domain_input,
    truthy_env,
    validate_method_name,
    validate_model_name,
)
from .write_policy import chatter_direct_enabled, side_effect_method_allowed, writes_enabled
from .rate_limit import check_rate
from .server_core import (
    DESTRUCTIVE_TOOL,
    PREVIEW_TOOL,
    READ_ONLY_TOOL,
    WRITE_APPROVAL_TTL_SECONDS,
    WriteConfirmation,
    ELICIT_WRITES_ENV,
    mcp,
    _resolve_odoo,
    register_write_approval,
    require_validated_write_approval,
    restrict_attachment_upload_path,
    restrict_field_file_path,
    write_approval_payload,
)

_FROM_PATH_SUFFIX = "_from_path"

# Odoo serializes XML-RPC responses with allow_none=False, so a method that
# returns None executes (and commits) server-side, then faults with this text.
_NONE_MARSHAL_FAULT_MARKER = "cannot marshal None unless allow_none is enabled"


def _read_field_file(path: Path, cap: int) -> bytes:
    """Open, size-check, and read ``path`` through a single file descriptor.

    Mirrors ``_read_attachment_source_file``: opening once with O_NOFOLLOW
    and reading through that same fd means the size cap and SHA-256 we
    compute are always over the *actual* bytes returned, not a stale
    ``stat()`` from an earlier, possibly-swapped file. The path has
    already passed ``restrict_field_file_path`` so we know it sits inside
    a configured root; this is the second line of defence.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError(f"{path} does not exist or is not a regular file") from exc
    with os.fdopen(fd, "rb") as handle:
        file_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} is not a regular file")
        if file_stat.st_size > cap:
            raise ValueError(
                f"{path} is {file_stat.st_size} bytes; cap is {cap} "
                "(raise ODOO_MCP_MAX_FIELD_FILE_BYTES to allow it)"
            )
        return handle.read()


def _read_attachment_source_file(path: Path, cap: int) -> bytes:
    """Open, size-check, and read ``path`` through a single file descriptor.

    ``restrict_attachment_upload_path`` only proves the path was inside a
    trusted root *at resolve time*. A writable upload root still leaves a
    TOCTOU window between that check and the read: the entry on disk could
    be swapped for a symlink pointing outside the root before we get to it.
    Opening once with ``O_NOFOLLOW`` (refuses a symlink as the final path
    component) and deriving both the size cap and the hash from the bytes
    read through that same fd closes the gap — the size/hash checked are
    always the bytes actually returned, not a stale ``stat()`` from an
    earlier, possibly-swapped file.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError(f"{path} does not exist or is not a regular file") from exc
    with os.fdopen(fd, "rb") as handle:
        file_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} does not exist or is not a regular file")
        if file_stat.st_size > cap:
            raise ValueError(
                f"{path} is {file_stat.st_size} bytes; cap is {cap} "
                "(raise ODOO_MCP_MAX_ATTACHMENT_UPLOAD_BYTES to allow it)"
            )
        return handle.read()


def _resolve_binary_from_path_fields(
    values: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Replace ``<field>_from_path`` entries with a content fingerprint.

    Lets a caller attach a local file (e.g. a resume) to an Odoo binary field
    without ever putting the base64 content in a tool call — the content
    would otherwise have to pass through the calling agent's context, which
    does not scale past a few hundred KB.

    Returns ``(values, real_base64_by_field)``. The returned ``values`` dict
    only ever holds a ``sha256:<hex>:<byte length>`` fingerprint for each
    resolved field — safe to hash into the approval token, store, and echo
    back to the caller. The real base64 is returned separately so it can be
    stored server-side only (see ``register_write_approval``) and substituted
    back in at execution time (see ``_execute_approved_write_gated``).
    """
    values = dict(values)
    resolved: Dict[str, str] = {}
    for key in [k for k in values if k.endswith(_FROM_PATH_SUFFIX)]:
        real_field = key[: -len(_FROM_PATH_SUFFIX)]
        if real_field in values:
            raise ValueError(f"pass either {real_field!r} or {key!r}, not both")
        raw_path = values.pop(key)
        path = restrict_attachment_upload_path(str(raw_path))
        data = _read_attachment_source_file(path, max_attachment_upload_bytes())
        digest = hashlib.sha256(data).hexdigest()
        values[real_field] = f"sha256:{digest}:{len(data)}"
        resolved[real_field] = base64.b64encode(data).decode("ascii")
    return values, resolved


def _srv() -> Any:
    """Late import of server module to resolve patchable symbols at call time."""
    from . import server
    return server


def _write_elicitation_message(approval: Dict[str, Any]) -> str:
    """Render a human-readable summary of the pending write."""
    operation = str(approval.get("operation") or "?")
    model = str(approval.get("model") or "?")
    record_ids = approval.get("record_ids") or []
    values = approval.get("values") or {}
    instance = str(approval.get("instance") or "default")
    lines = [f"Odoo write pending approval: {operation} on {model}"]
    if record_ids:
        lines.append(f"Records: {record_ids}")
    if values:
        changes = ", ".join(
            f"{key} -> {json.dumps(value, default=str)[:80]}"
            for key, value in sorted(values.items())
        )
        lines.append(f"Changes: {changes}")
    lines.append(f"Instance: {instance}")
    return "\n".join(lines)


async def _elicit_write_confirmation(
    ctx: Context, approval: Dict[str, Any]
) -> tuple[str, Optional[str]]:
    """Ask the human via MCP elicitation when ODOO_MCP_ELICIT_WRITES=1.

    Returns (decision, detail): "skipped" (gate off), "approved",
    "declined", or "unsupported" (client cannot elicit — fall back to the
    token flow).
    """
    if not truthy_env(ELICIT_WRITES_ENV):
        return "skipped", None
    try:
        result = await ctx.elicit(
            message=_write_elicitation_message(approval),
            schema=WriteConfirmation,
        )
    except Exception as exc:
        return "unsupported", str(exc)
    data = getattr(result, "data", None)
    if (
        getattr(result, "action", None) == "accept"
        and data is not None
        and data.approve
    ):
        return "approved", None
    return "declined", str(getattr(result, "action", "declined"))


@mcp.tool(
    description="Preview create, write, or unlink without executing it",
    annotations=PREVIEW_TOOL,
    structured_output=True,
)
def preview_write(
    model: Annotated[str, Field(description="Technical Odoo model name, e.g. 'res.partner'.")],
    operation: Annotated[
        str, Field(description='Write operation: "create", "write", or "unlink".')
    ],
    values: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional single-record payload for create/write."),
    ] = None,
    values_list: Annotated[
        Optional[List[Dict[str, Any]]],
        Field(
            description=(
                "Optional batch payload for create — one dict per record, max 100. "
                "Executes as a single atomic Odoo create(vals_list) call."
            )
        ),
    ] = None,
    record_ids: Annotated[
        Optional[List[int]],
        Field(description="Optional list of record IDs (required for write/unlink)."),
    ] = None,
    context: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional Odoo context dict applied to the write call."),
    ] = None,
    instance: Annotated[
        Optional[str],
        Field(description="Optional configured Odoo instance name; uses the default if omitted."),
    ] = None,
) -> Dict[str, Any]:
    """Build a canonical approval token for a later approved write.

    Batch create: pass ``values_list`` (one dict per record, max 100) —
    executes as a single atomic Odoo ``create(vals_list)`` call.
    """
    try:
        validate_model_name(model)
        report = build_write_preview_report(
            model=model,
            operation=operation,
            values=values,
            values_list=values_list,
            record_ids=record_ids,
            context=context,
            instance=_srv().resolve_instance_name(instance),
        )
        record_write_event(
            "preview",
            outcome="success" if report.get("success") else "rejected",
            model=model,
            operation=str(operation).strip().lower(),
            record_ids=[int(rid) for rid in record_ids or []],
            instance=_srv().resolve_instance_name(instance),
            token=str((report.get("approval") or {}).get("token") or "") or None,
        )
        return report
    except Exception as e:
        return {"success": False, "tool": "preview_write", "error": str(e)}


@mcp.tool(
    description="Validate a standard write payload against optional fields_get metadata",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def validate_write(
    ctx: Context,
    model: Annotated[str, Field(description="Technical Odoo model name, e.g. 'res.partner'.")],
    operation: Annotated[
        str, Field(description='Write operation: "create", "write", or "unlink".')
    ],
    values: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional single-record payload for create/write."),
    ] = None,
    values_list: Annotated[
        Optional[List[Dict[str, Any]]],
        Field(description="Optional batch payload for create — one dict per record, max 100."),
    ] = None,
    record_ids: Annotated[
        Optional[List[int]],
        Field(description="Optional list of record IDs (required for write/unlink)."),
    ] = None,
    context: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Optional Odoo context dict applied to the write call."),
    ] = None,
    fields_metadata: Annotated[
        Optional[Dict[str, Any]],
        Field(
            description=(
                "Optional pre-fetched fields_get dict for validation. When omitted "
                "and use_live_metadata is true, the tool fetches it via bounded fields_get."
            )
        ),
    ] = None,
    use_live_metadata: Annotated[
        bool, Field(description="When true and no fields_metadata is provided, fetch live fields_get from Odoo.")
    ] = True,
    instance: Annotated[
        Optional[str],
        Field(description="Optional configured Odoo instance name; uses the default if omitted."),
    ] = None,
) -> Dict[str, Any]:
    """Validate write shape and return an approval payload when safe."""
    try:
        validate_model_name(model)
        instance_name = _srv().resolve_instance_name(instance)

        resolved_binary_values: Dict[str, Any] = {}
        if values:
            values, resolved_binary_values = _resolve_binary_from_path_fields(values)
        if resolved_binary_values and (fields_metadata is not None or not use_live_metadata):
            return {
                "success": False,
                "tool": "validate_write",
                "error": (
                    "*_from_path uploads require validation against trusted live "
                    "Odoo metadata; call with use_live_metadata=True (the default) "
                    "and no explicit fields_metadata."
                ),
            }

        metadata_source = "input" if fields_metadata is not None else "none"
        if fields_metadata is None and use_live_metadata:
            metadata_source = "server"
            _, odoo = _resolve_odoo(ctx, instance)
            fields_metadata = odoo.get_model_fields(model)
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
            values_list=values_list,
            record_ids=record_ids,
            context=context,
            fields_metadata=fields_metadata,
            metadata_source=metadata_source,
            instance=instance_name,
        )
        trusted_live_metadata = (
            metadata_source == "server"
            and isinstance(fields_metadata, dict)
            and bool(fields_metadata)
        )
        if trusted_live_metadata:
            stored = register_write_approval(
                ctx.request_context.lifespan_context,
                report,
                resolved_binary_values=resolved_binary_values or None,
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
        record_write_event(
            "validate",
            outcome=(
                "approved" if report["approval_status"].get("stored") else "rejected"
            ),
            model=model,
            operation=str(operation).strip().lower(),
            record_ids=[int(rid) for rid in record_ids or []],
            instance=instance_name,
            token=str((report.get("approval") or {}).get("token") or "") or None,
            detail=None if report.get("success") else "validation issues present",
        )
        return report
    except Exception as e:
        return {"success": False, "tool": "validate_write", "error": str(e)}


@mcp.tool(
    name="execute_approved_write",
    description="Execute a previously previewed and confirmed standard write",
    annotations=DESTRUCTIVE_TOOL,
    structured_output=True,
)
async def execute_approved_write_tool(
    ctx: Context,
    approval: Dict[str, Any],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Tool entry point: optional human elicitation gate, then the sync gates."""
    decision, detail = await _elicit_write_confirmation(ctx, approval)
    if decision == "declined":
        record_write_event(
            "elicit",
            outcome="declined",
            model=str(approval.get("model") or "") or None,
            operation=str(approval.get("operation") or "") or None,
            instance=str(approval.get("instance") or "") or None,
            token=str(approval.get("token") or "") or None,
            detail=detail,
        )
        return {
            "success": False,
            "tool": "execute_approved_write",
            "error": "write declined by the human reviewer via elicitation",
        }
    return execute_approved_write(ctx, approval, confirm)


def execute_approved_write(
    ctx: Context,
    approval: Dict[str, Any],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Execute create/write/unlink only after token, confirm, and env gates pass."""
    report = _execute_approved_write_gated(ctx, approval, confirm)
    safe_record_ids = [
        int(rid)
        for rid in approval.get("record_ids") or []
        if isinstance(rid, (int, str)) and str(rid).isdigit()
    ]
    record_write_event(
        "execute",
        outcome="success" if report.get("success") else "denied",
        model=str(approval.get("model") or "") or None,
        operation=str(approval.get("operation") or "") or None,
        record_ids=safe_record_ids,
        instance=str(approval.get("instance") or "") or None,
        token=str(approval.get("token") or "") or None,
        detail=report.get("error"),
    )
    return report


def _execute_approved_write_gated(
    ctx: Context,
    approval: Dict[str, Any],
    confirm: bool,
) -> Dict[str, Any]:
    """Run every write gate and the final execution; audit-free inner body."""
    try:
        is_valid, _ = verify_write_approval(approval)
        if not is_valid:
            return {
                "success": False,
                "tool": "execute_approved_write",
                "error": (
                    "approval token does not match the canonical payload; "
                    "re-run preview_write and validate_write"
                ),
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
        resolved_binary_values = validation_record.get("resolved_binary_values") or {}
        for field_name, real_base64 in resolved_binary_values.items():
            # The client only ever held a sha256 fingerprint for these fields
            # (see _resolve_binary_from_path_fields) — swap in the real base64
            # that the server read from disk at validate_write time.
            if field_name in values:
                values[field_name] = real_base64
        values_list = approval.get("values_list")
        record_ids = [int(record_id) for record_id in approval.get("record_ids") or []]
        context = dict(approval.get("context") or {})
        kwargs: Dict[str, Any] = {"context": context} if context else {}
        if operation == "create" and values_list is not None:
            args: List[Any] = [list(values_list)]
        elif operation == "create":
            args = [values]
        elif operation == "write":
            args = [record_ids, values]
        else:
            args = [record_ids]

        approval_instance = str(approval.get("instance") or "") or None
        if (
            approval_instance is None
            or approval_instance == _srv().resolve_default_instance_name()
        ):
            odoo = app_context.odoo
        else:
            _, odoo = app_context.get_client(approval_instance)

        result = odoo.execute_method(model, operation, *args, **kwargs)
        app_context.write_approvals.pop(str(approval.get("token", "")), None)
        return {
            "success": True,
            "tool": "execute_approved_write",
            "model": model,
            "operation": operation,
            "result": result,
            "instance": approval_instance or _srv().resolve_default_instance_name(),
        }
    except Exception as e:
        return {"success": False, "tool": "execute_approved_write", "error": str(e)}


def _build_chatter_payload(
    *,
    model: str,
    record_id: int,
    body: str,
    message_type: str,
    subtype_xmlid: Optional[str],
    partner_ids: Optional[List[int]],
    attachment_ids: Optional[List[int]],
    instance: str = "default",
) -> Dict[str, Any]:
    """Build the canonical message_post call payload (deterministic ordering)."""
    kwargs: Dict[str, Any] = {"body": body, "message_type": message_type}
    if subtype_xmlid:
        kwargs["subtype_xmlid"] = subtype_xmlid
    if partner_ids:
        kwargs["partner_ids"] = [int(pid) for pid in partner_ids]
    if attachment_ids:
        kwargs["attachment_ids"] = [int(aid) for aid in attachment_ids]
    return {
        "model": model,
        "method": "message_post",
        "record_ids": [int(record_id)],
        "kwargs": kwargs,
        "instance": instance or "default",
    }


@mcp.tool(
    description=(
        "Post a chatter message on a mail.thread record. Default mode requires "
        "an approval token returned from a preview call; set MCP_CHATTER_DIRECT=1 "
        "to bypass and post immediately."
    ),
    annotations=DESTRUCTIVE_TOOL,
    structured_output=True,
)
def chatter_post(
    ctx: Context,
    model: Annotated[str, Field(description="Technical Odoo model name, e.g. 'project.task'.")],
    record_id: Annotated[int, Field(description="ID of the record to post the message on.")],
    body: Annotated[str, Field(description="Message body text (plaintext or HTML).")],
    message_type: Annotated[
        str, Field(description="Message type: 'comment' (default) or 'notification'.")
    ] = "comment",
    subtype_xmlid: Annotated[
        Optional[str],
        Field(description="Optional mail.message.subtype XMLID, e.g. 'mail.mt_note'."),
    ] = None,
    partner_ids: Annotated[
        Optional[List[int]],
        Field(description="Optional list of res.partner IDs to notify in addition to followers."),
    ] = None,
    attachment_ids: Annotated[
        Optional[List[int]],
        Field(description="Optional list of ir.attachment IDs to attach to the message."),
    ] = None,
    approval: Annotated[
        Optional[Dict[str, Any]],
        Field(
            description=(
                "Execute-mode only: the approval payload returned from a previous "
                "preview call. Omit on the first call to receive a preview token."
            )
        ),
    ] = None,
    confirm: Annotated[
        bool, Field(description="Required true to execute; ignored in preview mode.")
    ] = False,
    instance: Annotated[
        Optional[str],
        Field(description="Optional configured Odoo instance name; uses the default if omitted."),
    ] = None,
) -> Dict[str, Any]:
    """Post a message on the chatter of a mail.thread-derived record.

    Modes:
    - Default (gated): first call returns ``mode=preview`` with an approval
      token. Re-call with the same arguments plus ``approval`` and
      ``confirm=true`` to send.
    - Direct (``MCP_CHATTER_DIRECT=1``): the message is posted on the first
      call without a token.

    Allowed ``message_type`` values: ``comment`` (default), ``notification``.
    """
    try:
        instance_name, odoo = _resolve_odoo(ctx, instance)
        validate_model_name(model)
        if record_id < 1:
            raise ValueError("record_id must be greater than 0")
        body_text = (body or "").strip()
        if not body_text:
            raise ValueError("body must be a non-empty string")
        if message_type not in {"comment", "notification"}:
            raise ValueError("message_type must be 'comment' or 'notification'.")

        canonical = _build_chatter_payload(
            model=model,
            record_id=record_id,
            body=body_text,
            message_type=message_type,
            subtype_xmlid=subtype_xmlid,
            partner_ids=partner_ids,
            attachment_ids=attachment_ids,
            instance=instance_name,
        )
        token = build_approval_token(canonical)

        direct_mode = chatter_direct_enabled()
        if direct_mode:
            result = odoo.execute_method(
                model,
                "message_post",
                [record_id],
                **canonical["kwargs"],
            )
            record_write_event(
                "chatter_post",
                outcome="success",
                model=model,
                operation="message_post",
                record_ids=[record_id],
                instance=instance_name,
                detail="direct mode",
            )
            return {
                "success": True,
                "mode": "direct",
                "model": model,
                "record_id": record_id,
                "approval_required": False,
                "result": result,
            }

        if approval is None:
            return {
                "success": True,
                "mode": "preview",
                "model": model,
                "record_id": record_id,
                "approval": {**canonical, "token": token},
                "warnings": [
                    "Preview only. Re-call chatter_post with the returned approval "
                    "and confirm=true to actually post."
                ],
            }

        provided_token = str(approval.get("token", ""))
        if provided_token != token:
            raise ValueError(
                "Approval token does not match the chatter payload — re-run preview."
            )
        if not confirm:
            raise ValueError(
                "confirm=true is required to execute an approved chatter post."
            )

        result = odoo.execute_method(
            model,
            "message_post",
            [record_id],
            **canonical["kwargs"],
        )
        record_write_event(
            "chatter_post",
            outcome="success",
            model=model,
            operation="message_post",
            record_ids=[record_id],
            instance=instance_name,
            token=provided_token,
        )
        return {
            "success": True,
            "mode": "execute",
            "model": model,
            "record_id": record_id,
            "approval_required": True,
            "result": result,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    description=(
        "Set one field on an Odoo record from the contents of a local file "
        "(two-phase preview/execute; file content never enters the agent context)"
    ),
    annotations=DESTRUCTIVE_TOOL,
    structured_output=True,
)
def write_field_from_file(
    ctx: Context,
    model: Annotated[
        str, Field(description="Technical Odoo model name, for example 'res.partner'.")
    ],
    record_id: Annotated[
        int, Field(description="ID of the record to write the field to.")
    ],
    field: Annotated[
        str,
        Field(
            description=(
                "Name of the field to set. The on-disk interpretation is "
                "controlled by ``encoding`` — it does NOT mirror Odoo's "
                "field type. ``encoding='base64'`` (default for binary "
                "fields) means the file holds RAW BYTES that the server "
                "will base64-encode for Odoo's wire format; "
                "``encoding='utf-8'`` (default for text/HTML fields) "
                "means the file is text decoded as a Unicode string."
            )
        ),
    ],
    input_path: Annotated[
        str,
        Field(
            description=(
                "Absolute path of the file to read the new field value from. "
                "Must sit inside the configured field-file root."
            )
        ),
    ],
    file_root: Annotated[
        Optional[str],
        Field(
            description=(
                "Optional selector for which configured root the input_path "
                "must sit inside; must equal one of the ODOO_MCP_FIELD_FILE_ROOTS "
                "entries. Defaults to the first entry. The argument cannot "
                "widen the operator's allow-list."
            )
        ),
    ] = None,
    encoding: Annotated[
        Optional[str],
        Field(
            description=(
                "How to interpret the file contents: 'utf-8' (default for "
                "text/HTML fields, file is read as text and pushed as a "
                "Unicode string) or 'base64' (default for binary fields, "
                "file holds raw bytes that are base64-encoded for Odoo's "
                "wire format)."
            )
        ),
    ] = None,
    approval: Annotated[
        Optional[Dict[str, Any]],
        Field(
            description=(
                "Execute-mode only: the approval payload returned from a "
                "previous preview call. Omit on the first call to receive a "
                "preview token."
            )
        ),
    ] = None,
    confirm: Annotated[
        bool,
        Field(description="Required true to execute; ignored in preview mode."),
    ] = False,
    instance: Annotated[
        Optional[str],
        Field(description="Optional configured Odoo instance name; uses the default if omitted."),
    ] = None,
) -> Dict[str, Any]:
    """
    Set a single field on an Odoo record by streaming the contents of a local file.

    Avoids the JSON-RPC escaping and context-bloat pain of long HTML/Code
    payloads: the file content never enters the agent's context — the
    preview token only carries the SHA-256 fingerprint, and the execute
    call re-reads the file server-side, re-checks the hash, and routes
    through the gated write pipeline.

    Modes:
    - Default (gated): first call returns ``mode="preview"`` with an
      approval token. Re-call with the same arguments plus ``approval``
      and ``confirm=true`` to actually write.
    - Direct: ``approval`` and ``confirm=true`` together execute the write.

    Security:
    - Path must be absolute and inside ``file_root`` (or the first
      configured ODOO_MCP_FIELD_FILE_ROOTS entry).
    - File is opened with ``O_NOFOLLOW`` and read once; size cap and
      SHA-256 are derived from the same fd to defeat TOCTOU swaps.
    - Preview token never contains the file content. Execute re-reads
      the file and re-checks the hash, so a tampered or swapped file
      between the two calls is rejected before Odoo is touched.
    - Field existence + ``readonly=False`` is revalidated against live
      ``fields_get`` at execute time — preview-only checks would let an
      attacker mutate the field on the server between the two calls.
    - Same hard gates as ``execute_approved_write``: requires
      ``ODOO_MCP_ENABLE_WRITES=1`` and ``confirm=true``.
    """
    try:
        validate_model_name(model)
        if record_id < 1:
            raise ValueError("record_id must be greater than 0")
        if not field or not field.strip():
            raise ValueError("field must be a non-empty string")
        cap = max_field_file_bytes()

        # Resolve + size-check + hash the file before building any token.
        resolved_path, resolved_root = restrict_field_file_path(input_path, file_root)
        file_bytes = _read_field_file(resolved_path, cap)
        digest = hashlib.sha256(file_bytes).hexdigest()
        size_bytes = len(file_bytes)

        instance_name = _srv().resolve_instance_name(instance)

        # Build a deterministic payload: hash + size, no content.
        canonical: Dict[str, Any] = {
            "model": model,
            "operation": "write",
            "record_ids": [int(record_id)],
            "field": field,
            "input_path": str(resolved_path),
            "content_sha256": f"sha256:{digest}:{size_bytes}",
            "content_bytes": size_bytes,
            "encoding": (encoding or "").strip().lower() or None,
            "instance": instance_name,
        }
        token = build_approval_token(canonical)

        # ----- Preview mode -------------------------------------------------
        if approval is None:
            record_write_event(
                "write_field_from_file",
                outcome="preview",
                model=model,
                operation="write",
                record_ids=[int(record_id)],
                instance=instance_name,
                token=token,
                detail=f"field={field} bytes={size_bytes}",
            )
            return {
                "success": True,
                "tool": "write_field_from_file",
                "mode": "preview",
                "model": model,
                "record_id": int(record_id),
                "field": field,
                "input_path": str(resolved_path),
                "file_root": str(resolved_root),
                "encoding": canonical["encoding"],
                "content_sha256": canonical["content_sha256"],
                "bytes_written": size_bytes,
                "approval": {**canonical, "token": token},
                "warnings": [
                    "Preview only. Re-call write_field_from_file with the returned "
                    "approval and confirm=true to actually write the field."
                ],
                "metadata_used": {
                    "instance": instance_name,
                    "file_root": str(resolved_root),
                    "encoding": canonical["encoding"],
                    "max_bytes": cap,
                },
            }

        # ----- Execute mode -------------------------------------------------
        if not confirm:
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": "confirm=true is required for destructive execution",
            }
        if not writes_enabled():
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": (
                    "write execution disabled; set ODOO_MCP_ENABLE_WRITES=1 to enable"
                ),
            }

        provided_token = str(approval.get("token", ""))
        if provided_token != token:
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": (
                    "approval token does not match the current file content; "
                    "re-run preview and confirm the SHA-256 fingerprint."
                ),
            }
        # Re-read once more, then compare hash. Catches both a malicious
        # tamper between the two calls and a benign race where the file
        # was edited by a different process in between.
        re_read = _read_field_file(resolved_path, cap)
        if hashlib.sha256(re_read).hexdigest() != digest:
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": (
                    "file contents changed between preview and execute; "
                    "re-run preview and confirm."
                ),
            }
        # The two reads were identical; either is fine for the actual write.
        if len(re_read) != size_bytes:
            # Should not happen since the hash covers size, but be explicit.
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": "file size changed between preview and execute",
            }

        # Decide encoding for the actual value pushed to Odoo.
        _, odoo = _resolve_odoo(ctx, instance)
        fields_metadata = odoo.get_model_fields(model)
        if not isinstance(fields_metadata, dict) or "error" in fields_metadata:
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": (
                    "could not load live fields_get metadata; refusing to write "
                    f"{model}.{field}: {fields_metadata.get('error') if isinstance(fields_metadata, dict) else fields_metadata}"
                ),
            }
        if field not in fields_metadata:
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": f"field {field!r} does not exist on {model}",
            }
        field_meta = fields_metadata[field] if isinstance(fields_metadata.get(field), dict) else {}
        declared_binary = field_meta.get("type") == "binary"
        declared_readonly = bool(field_meta.get("readonly"))
        if declared_readonly:
            return {
                "success": False,
                "tool": "write_field_from_file",
                "error": f"field {field!r} on {model} is readonly; refusing to write",
            }

        chosen_encoding = (encoding or "").strip().lower()
        if not chosen_encoding:
            chosen_encoding = "base64" if declared_binary else "utf-8"

        if chosen_encoding == "base64":
            field_value: Any = base64.b64encode(re_read).decode("ascii")
        else:
            try:
                field_value = re_read.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"file at {resolved_path} is not valid utf-8; pass "
                    "encoding='base64' to interpret it as a binary blob"
                ) from exc

        # IMPORTANT: Odoo's XML-RPC execute_kw expects ids + vals as
        # *separate* positional arguments — packing them into a single
        # list ([[ids], {vals}]) would unpack to ProjectTask.write(*[[ids],
        # {vals}]) which yields write([ids], {vals}) and fails with
        # 'missing 1 required positional argument: vals'. Mirrors
        # execute_approved_write above, which already splats *args, **kwargs.
        result = odoo.execute_method(
            model,
            "write",
            [int(record_id)],
            {field: field_value},
        )
        record_write_event(
            "write_field_from_file",
            outcome="success",
            model=model,
            operation="write",
            record_ids=[int(record_id)],
            instance=instance_name,
            token=provided_token,
            detail=f"field={field} bytes={size_bytes}",
        )
        return {
            "success": True,
            "tool": "write_field_from_file",
            "mode": "execute",
            "model": model,
            "record_id": int(record_id),
            "field": field,
            "input_path": str(resolved_path),
            "file_root": str(resolved_root),
            "encoding": chosen_encoding,
            "bytes_written": size_bytes,
            "content_sha256": f"sha256:{digest}:{size_bytes}",
            "result": result,
            "metadata_used": {
                "instance": instance_name,
                "file_root": str(resolved_root),
                "encoding": chosen_encoding,
                "max_bytes": cap,
                "field_type": field_meta.get("type") if isinstance(field_meta, dict) else None,
            },
        }
    except Exception as e:
        return {"success": False, "tool": "write_field_from_file", "error": str(e)}


@mcp.tool(
    description="Execute a custom method on an Odoo model",
    annotations=DESTRUCTIVE_TOOL,
    structured_output=True,
)
def execute_method(
    ctx: Context,
    model: Annotated[
        str, Field(description="Technical Odoo model name, for example 'res.partner'.")
    ],
    method: Annotated[
        str,
        Field(
            description=(
                "Odoo model method to call; direct create, write, and unlink are blocked."
            )
        ),
    ],
    args: Annotated[
        Optional[List[Any]], Field(description="Optional positional method arguments.")
    ] = None,
    kwargs: Annotated[
        Optional[Dict[str, Any]], Field(description="Optional keyword method arguments.")
    ] = None,
    instance: Annotated[
        Optional[str],
        Field(description="Optional configured Odoo instance name; uses the default if omitted."),
    ] = None,
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
        review_required = safety["safety"] in {"side_effect", "unknown"}
        if (
            review_required
            and not side_effect_method_allowed(model, method)
            and not truthy_env("ODOO_MCP_ALLOW_UNKNOWN_METHODS")
        ):
            return {
                "success": False,
                "error": (
                    "Unreviewed side-effect methods are blocked by default. Review "
                    "custom source, then add the exact 'model.method' to the policy "
                    "file (ODOO_MCP_POLICY_FILE, default ./odoo_mcp_policy.json, "
                    "re-read on every request — see odoo_mcp_policy.json.example) "
                    "or to ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS=model.method, or "
                    "set ODOO_MCP_ALLOW_UNKNOWN_METHODS=1 only for trusted "
                    "deployments."
                ),
                "classification": safety,
            }
        args = args or []
        kwargs = kwargs or {}

        search_methods = ["search", "search_count", "search_read"]
        if method in search_methods and args:
            normalized_args = list(args)
            if len(normalized_args) > 0:
                normalized_args[0] = normalize_domain_input(normalized_args[0])
                args = normalized_args

        instance_name, odoo = _resolve_odoo(ctx, instance)
        refusal = check_rate(instance_name, "execute_method")
        if refusal is not None:
            return refusal
        try:
            result = odoo.execute_method(model, method, *args, **kwargs)
        except xmlrpc.client.Fault as fault:
            if _NONE_MARSHAL_FAULT_MARKER not in str(fault.faultString or ""):
                raise
            # Odoo already executed and committed the call; only serializing
            # the None return value failed. Report success, not a phantom
            # failure that tempts a retry of a side-effect method.
            return {
                "success": True,
                "result": None,
                "warning": (
                    "Method executed and committed server-side; Odoo could not "
                    "marshal its None return value over XML-RPC, so no result "
                    "payload is available. Verify state with a read if needed."
                ),
            }
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
