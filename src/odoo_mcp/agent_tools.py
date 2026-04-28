"""Pure helper builders for agent-facing Odoo MCP tools.

This module avoids network, config, and Odoo client side effects. Server
adapters pass live metadata in when they have it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

WRITE_OPERATIONS = {"create", "write", "unlink"}
SAFE_DOMAIN_OPERATORS = {
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not in",
    "like",
    "not like",
    "ilike",
    "not ilike",
    "=like",
    "=ilike",
    "child_of",
    "parent_of",
}

BUSINESS_PACKS: dict[str, dict[str, Any]] = {
    "sales": {
        "modules": ["sale", "sale_management", "crm"],
        "models": ["sale.order", "sale.order.line", "res.partner", "product.product"],
        "safe_reports": ["quotation_pipeline", "order_status", "customer_activity"],
    },
    "crm": {
        "modules": ["crm"],
        "models": ["crm.lead", "crm.stage", "res.partner", "mail.activity"],
        "safe_reports": ["pipeline", "lost_reasons", "activity_backlog"],
    },
    "inventory": {
        "modules": ["stock", "product"],
        "models": ["stock.picking", "stock.move", "stock.quant", "product.product"],
        "safe_reports": ["on_hand", "open_transfers", "reordering_attention"],
    },
    "accounting": {
        "modules": ["account"],
        "models": ["account.move", "account.move.line", "account.journal", "res.partner"],
        "safe_reports": ["open_invoices", "journal_health", "partner_balances"],
    },
    "hr": {
        "modules": ["hr", "hr_holidays"],
        "models": ["hr.employee", "hr.leave", "hr.leave.report.calendar"],
        "safe_reports": ["employee_lookup", "leave_calendar", "leave_status"],
    },
}


def canonical_json(value: Any) -> str:
    """Return a stable JSON representation for hashing and comparisons."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def build_approval_token(payload: dict[str, Any]) -> str:
    """Build a deterministic approval token for a canonical write preview."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"odoo-write:{digest[:32]}"


def build_write_preview_report(
    *,
    model: str,
    operation: str,
    values: dict[str, Any] | None = None,
    record_ids: list[int] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a non-executing preview for standard ORM write operations."""
    normalized_operation = operation.strip().lower()
    issues: list[dict[str, str]] = []
    if normalized_operation not in WRITE_OPERATIONS:
        issues.append(
            {
                "code": "unsupported_write_operation",
                "severity": "error",
                "message": "operation must be one of create, write, or unlink.",
            }
        )

    normalized_values = dict(values or {})
    normalized_ids = [int(record_id) for record_id in record_ids or []]
    if normalized_operation == "create" and not normalized_values:
        issues.append(
            {
                "code": "missing_create_values",
                "severity": "error",
                "message": "create requires non-empty values.",
            }
        )
    if normalized_operation in {"write", "unlink"} and not normalized_ids:
        issues.append(
            {
                "code": "missing_record_ids",
                "severity": "error",
                "message": f"{normalized_operation} requires record_ids.",
            }
        )
    if normalized_operation == "write" and not normalized_values:
        issues.append(
            {
                "code": "missing_write_values",
                "severity": "error",
                "message": "write requires non-empty values.",
            }
        )

    canonical_payload = {
        "model": model,
        "operation": normalized_operation,
        "record_ids": normalized_ids,
        "values": normalized_values,
        "context": dict(context or {}),
    }
    approval_token = build_approval_token(canonical_payload)

    return {
        "success": not any(issue["severity"] == "error" for issue in issues),
        "tool": "preview_write",
        "model": model,
        "operation": normalized_operation,
        "approval": {**canonical_payload, "token": approval_token},
        "execute_method": _write_execute_method_args(canonical_payload),
        "issues": issues,
        "warnings": [
            {
                "code": "destructive_operation",
                "message": (
                    "This preview does not execute. execute_approved_write is "
                    "destructive and requires the matching approval token plus confirm=true."
                ),
            }
        ],
        "metadata_used": {"client_instantiated": False},
    }


def verify_write_approval(approval: dict[str, Any]) -> tuple[bool, str]:
    """Verify a write approval token against the canonical payload."""
    token = str(approval.get("token", ""))
    payload = {
        "model": approval.get("model"),
        "operation": approval.get("operation"),
        "record_ids": approval.get("record_ids") or [],
        "values": approval.get("values") or {},
        "context": approval.get("context") or {},
    }
    expected = build_approval_token(payload)
    return token == expected, expected


def validate_write_report(
    *,
    model: str,
    operation: str,
    values: dict[str, Any] | None,
    record_ids: list[int] | None,
    context: dict[str, Any] | None = None,
    fields_metadata: dict[str, Any] | None = None,
    metadata_source: str = "none",
) -> dict[str, Any]:
    """Validate write payload shape against optional fields_get metadata."""
    preview = build_write_preview_report(
        model=model,
        operation=operation,
        values=values,
        record_ids=record_ids,
        context=context,
    )
    issues: list[dict[str, str]] = list(preview["issues"])
    field_hints: list[dict[str, str]] = []
    normalized_values = dict(values or {})
    if fields_metadata is not None:
        for field_name in sorted(normalized_values):
            meta = fields_metadata.get(field_name)
            if not isinstance(meta, dict):
                issues.append(
                    {
                        "code": "unknown_field",
                        "severity": "error",
                        "message": f"{field_name!r} is not present in fields_get metadata.",
                    }
                )
                continue
            field_type = str(meta.get("type", ""))
            if meta.get("readonly"):
                issues.append(
                    {
                        "code": "readonly_field",
                        "severity": "error",
                        "message": f"{field_name!r} is readonly in fields_get metadata.",
                    }
                )
            elif field_type == "many2one":
                field_hints.append(
                    {"field": field_name, "hint": "many2one values should be record IDs."}
                )
            elif field_type in {"many2many", "one2many"}:
                field_hints.append(
                    {
                        "field": field_name,
                        "hint": "relational values should use Odoo command lists.",
                    }
                )

        if operation == "create":
            for field_name, raw_meta in sorted(fields_metadata.items()):
                if not isinstance(raw_meta, dict):
                    continue
                if (
                    raw_meta.get("required")
                    and not raw_meta.get("readonly")
                    and not raw_meta.get("compute")
                    and field_name not in normalized_values
                ):
                    field_hints.append(
                        {
                            "field": field_name,
                            "hint": "required on create unless Odoo provides a default.",
                        }
                    )

    success = not any(issue["severity"] == "error" for issue in issues)
    return {
        "success": success,
        "tool": "validate_write",
        "model": model,
        "operation": operation,
        "issues": issues,
        "field_hints": field_hints,
        "approval": preview["approval"] if success else None,
        "metadata_used": {
            "fields_get": fields_metadata is not None,
            "source": metadata_source,
        },
    }


def _write_execute_method_args(payload: dict[str, Any]) -> dict[str, Any]:
    operation = str(payload["operation"])
    context = payload.get("context") or {}
    kwargs = {"context": context} if context else {}
    if operation == "create":
        args: list[Any] = [payload.get("values") or {}]
    elif operation == "write":
        args = [payload.get("record_ids") or [], payload.get("values") or {}]
    elif operation == "unlink":
        args = [payload.get("record_ids") or []]
    else:
        args = []
    return {
        "model": payload.get("model"),
        "method": operation,
        "args": args,
        "kwargs": kwargs,
    }


def build_domain_report(
    *,
    conditions: list[dict[str, Any]],
    logical_operator: str = "and",
    fields_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate an Odoo domain from structured conditions."""
    issues: list[dict[str, str]] = []
    normalized_conditions: list[list[Any]] = []
    for index, condition in enumerate(conditions):
        field = str(condition.get("field", "")).strip()
        operator = str(condition.get("operator", "")).strip()
        value = condition.get("value")
        if not field:
            issues.append(
                {
                    "code": "missing_field",
                    "severity": "error",
                    "message": f"condition {index} is missing field.",
                }
            )
            continue
        if operator not in SAFE_DOMAIN_OPERATORS:
            issues.append(
                {
                    "code": "invalid_operator",
                    "severity": "error",
                    "message": f"{operator!r} is not an allowed Odoo domain operator.",
                }
            )
            continue
        if fields_metadata is not None and field not in fields_metadata:
            issues.append(
                {
                    "code": "unknown_field",
                    "severity": "error",
                    "message": f"{field!r} is not present in fields_get metadata.",
                }
            )
            continue
        if operator in {"in", "not in"} and not isinstance(value, list):
            issues.append(
                {
                    "code": "operator_requires_list",
                    "severity": "error",
                    "message": f"{operator!r} requires a list value.",
                }
            )
            continue
        normalized_conditions.append([field, operator, value])

    operator_name = logical_operator.strip().lower()
    if operator_name not in {"and", "or"}:
        issues.append(
            {
                "code": "invalid_logical_operator",
                "severity": "error",
                "message": "logical_operator must be 'and' or 'or'.",
            }
        )
    if operator_name == "or" and len(normalized_conditions) > 1:
        domain: list[Any] = ["|"] * (len(normalized_conditions) - 1)
        domain.extend(normalized_conditions)
    else:
        domain = normalized_conditions

    return {
        "success": not any(issue["severity"] == "error" for issue in issues),
        "tool": "build_domain",
        "domain": domain,
        "conditions": normalized_conditions,
        "issues": issues,
        "metadata_used": {"fields_get": fields_metadata is not None},
    }


def scan_addons_source_report(
    *,
    addons_paths: list[str] | None = None,
    max_files: int = 200,
    max_file_bytes: int = 300_000,
) -> dict[str, Any]:
    """Scan local Odoo addon source without importing addon code."""
    paths = _normalize_scan_paths(addons_paths)
    findings: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    scanned_files = 0
    skipped_files = 0

    for root in paths:
        if scanned_files >= max_files:
            break
        root_path = Path(root).expanduser()
        if not root_path.exists():
            findings.append(
                {
                    "code": "addons_path_missing",
                    "severity": "warning",
                    "evidence": str(root_path),
                    "recommendation": "Check ODOO_ADDONS_PATHS or pass addons_paths explicitly.",
                }
            )
            continue
        for file_path in root_path.rglob("*"):
            if scanned_files >= max_files:
                break
            if not file_path.is_file() or file_path.is_symlink():
                continue
            if file_path.suffix not in {".py", ".xml", ".csv"} and file_path.name != "__manifest__.py":
                continue
            try:
                if file_path.stat().st_size > max_file_bytes:
                    skipped_files += 1
                    continue
            except OSError:
                skipped_files += 1
                continue
            scanned_files += 1
            relative = str(file_path)
            if file_path.name == "__manifest__.py":
                manifest = _read_manifest(file_path)
                if manifest:
                    modules.append(manifest)
                    if manifest.get("installable") is False:
                        findings.append(
                            {
                                "code": "non_installable_module",
                                "severity": "info",
                                "evidence": relative,
                                "recommendation": "Confirm whether this module should be considered during upgrade.",
                            }
                        )
            elif file_path.suffix == ".py":
                findings.extend(_scan_python_file(file_path))
            elif file_path.suffix == ".xml":
                findings.extend(_scan_xml_file(file_path))
            elif file_path.suffix == ".csv" and "security" in file_path.parts:
                findings.append(
                    {
                        "code": "security_rule_file",
                        "severity": "info",
                        "evidence": relative,
                        "recommendation": "Review access CSV rules during upgrade testing.",
                    }
                )

    return {
        "success": True,
        "tool": "scan_addons_source",
        "paths": paths,
        "summary": {
            "modules": len(modules),
            "findings": len(findings),
            "scanned_files": scanned_files,
            "skipped_files": skipped_files,
            "max_files_reached": scanned_files >= max_files,
        },
        "modules": modules,
        "source_findings": findings,
        "metadata_used": {"source_scan": True},
    }


def business_pack_report(
    *,
    pack: str,
    available_models: list[str] | None = None,
    installed_modules: list[str] | None = None,
) -> dict[str, Any]:
    """Build a read-only business-pack discovery report."""
    pack_key = pack.strip().lower()
    if pack_key not in BUSINESS_PACKS:
        return {
            "success": False,
            "tool": "business_pack_report",
            "error": f"Unknown pack {pack!r}.",
            "available_packs": sorted(BUSINESS_PACKS),
        }
    definition = BUSINESS_PACKS[pack_key]
    model_set = set(available_models or [])
    module_set = set(installed_modules or [])
    expected_models = list(definition["models"])
    expected_modules = list(definition["modules"])
    present_models = [model for model in expected_models if model in model_set]
    missing_models = [model for model in expected_models if model not in model_set]
    present_modules = [module for module in expected_modules if module in module_set]

    has_live_evidence = bool(model_set or module_set)
    return {
        "success": True,
        "tool": "business_pack_report",
        "pack": pack_key,
        "expected_modules": expected_modules,
        "installed_modules": present_modules,
        "expected_models": expected_models,
        "available_models": present_models,
        "missing_models": missing_models if has_live_evidence else [],
        "safe_reports": definition["safe_reports"],
        "recommended_next_calls": [
            {"tool": "list_models", "arguments": {"query": model.split(".")[0]}}
            for model in expected_models[:3]
        ],
        "metadata_used": {
            "models": bool(model_set),
            "modules": bool(module_set),
            "source": "live_or_input" if has_live_evidence else "static_pack",
        },
    }


def _normalize_scan_paths(addons_paths: list[str] | None) -> list[str]:
    if addons_paths:
        return [path for path in addons_paths if path]
    env_value = os.environ.get("ODOO_ADDONS_PATHS", "")
    return [path for path in env_value.split(os.pathsep) if path]


def _read_manifest(file_path: Path) -> dict[str, Any] | None:
    try:
        raw = file_path.read_text(encoding="utf-8")
        parsed = ast.literal_eval(raw)
    except (OSError, SyntaxError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    module_dir = file_path.parent
    return {
        "name": str(parsed.get("name", module_dir.name)),
        "module": module_dir.name,
        "version": str(parsed.get("version", "")),
        "depends": list(parsed.get("depends", []))
        if isinstance(parsed.get("depends", []), list)
        else [],
        "installable": parsed.get("installable", True),
        "path": str(file_path),
        "custom": not str(module_dir.name).startswith(("base", "web", "mail")),
    }


def _scan_python_file(file_path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        raw = file_path.read_text(encoding="utf-8")
        tree = ast.parse(raw)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [
            {
                "code": "python_parse_error",
                "severity": "warning",
                "evidence": f"{file_path}: {exc}",
                "recommendation": "Inspect this file manually before upgrade.",
            }
        ]

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            inherit_names = [_expr_name(base) for base in node.bases]
            if any(name.endswith(("models.Model", "Model", "TransientModel")) for name in inherit_names):
                findings.append(
                    {
                        "code": "custom_model_class",
                        "severity": "info",
                        "evidence": f"{file_path}:{node.lineno} class {node.name}",
                        "recommendation": "Review model fields, constraints, computes, and overrides.",
                    }
                )
        elif isinstance(node, ast.FunctionDef):
            if node.name in {"create", "write", "unlink"} or node.name.startswith("action_"):
                findings.append(
                    {
                        "code": "custom_method",
                        "severity": "warning"
                        if node.name in {"create", "write", "unlink"}
                        else "info",
                        "evidence": f"{file_path}:{node.lineno} def {node.name}",
                        "recommendation": "Review side effects and JSON-2 named-argument compatibility.",
                    }
                )
    if re.search(r"\.sudo\(\)", raw):
        findings.append(
            {
                "code": "sudo_usage",
                "severity": "warning",
                "evidence": str(file_path),
                "recommendation": "Review privilege escalation and record-rule assumptions.",
            }
        )
    return findings


def _scan_xml_file(file_path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        tree = ElementTree.parse(file_path)
    except (OSError, ElementTree.ParseError) as exc:
        return [
            {
                "code": "xml_parse_error",
                "severity": "warning",
                "evidence": f"{file_path}: {exc}",
                "recommendation": "Inspect this XML file manually before upgrade.",
            }
        ]
    for record in tree.findall(".//record"):
        model = record.attrib.get("model", "")
        if model in {"ir.cron", "base.automation", "ir.actions.server"}:
            findings.append(
                {
                    "code": "automated_action",
                    "severity": "warning",
                    "evidence": f"{file_path}: record model={model}",
                    "recommendation": "Verify automated actions and server actions on staging.",
                }
            )
        elif model.startswith("ir.ui.view"):
            findings.append(
                {
                    "code": "custom_view",
                    "severity": "info",
                    "evidence": f"{file_path}: record model={model}",
                    "recommendation": "Review view inheritance after Odoo upgrades.",
                }
            )
    return findings


def _expr_name(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        value = _expr_name(expr.value)
        return f"{value}.{expr.attr}" if value else expr.attr
    return ""


def token_age_seconds(created_at: float | None) -> float | None:
    """Return token age in seconds for callers that include a timestamp."""
    if created_at is None:
        return None
    return max(0.0, time.time() - created_at)
