"""
MCP tools: write domain.

Includes: preview_write, validate_write, execute_approved_write,
chatter_post, execute_method + WriteConfirmation + elicitation logic.
"""

import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from .agent_tools import (
    build_approval_token,
    build_write_preview_report,
    validate_write_report,
    verify_write_approval,
)
from .audit import record_write_event
from .diagnostics import DESTRUCTIVE_METHODS, classify_method_safety
from .tool_helpers import (
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
    write_approval_payload,
)


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
    model: str,
    operation: str,
    values: Optional[Dict[str, Any]] = None,
    values_list: Optional[List[Dict[str, Any]]] = None,
    record_ids: Optional[List[int]] = None,
    context: Optional[Dict[str, Any]] = None,
    instance: Optional[str] = None,
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
    model: str,
    operation: str,
    values: Optional[Dict[str, Any]] = None,
    values_list: Optional[List[Dict[str, Any]]] = None,
    record_ids: Optional[List[int]] = None,
    context: Optional[Dict[str, Any]] = None,
    fields_metadata: Optional[Dict[str, Any]] = None,
    use_live_metadata: bool = True,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate write shape and return an approval payload when safe."""
    try:
        validate_model_name(model)
        instance_name = _srv().resolve_instance_name(instance)
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
    model: str,
    record_id: int,
    body: str,
    message_type: str = "comment",
    subtype_xmlid: Optional[str] = None,
    partner_ids: Optional[List[int]] = None,
    attachment_ids: Optional[List[int]] = None,
    approval: Optional[Dict[str, Any]] = None,
    confirm: bool = False,
    instance: Optional[str] = None,
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
    instance: Optional[str] = None,
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
                    "custom source and allow exact methods through "
                    "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS=model.method, or set "
                    "ODOO_MCP_ALLOW_UNKNOWN_METHODS=1 only for trusted deployments."
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
        result = odoo.execute_method(model, method, *args, **kwargs)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
