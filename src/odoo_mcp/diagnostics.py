"""Pure diagnostic helpers for Odoo MCP tools.

This module intentionally has no network, config, or Odoo client side effects.
It builds preview/report payloads that server adapters can expose safely.
"""

from __future__ import annotations

import json
import re
from typing import Any

JSON2_POSITIONAL_ARG_MAP: dict[str, tuple[str, ...]] = {
    "search": ("domain", "offset", "limit", "order"),
    "search_count": ("domain", "limit"),
    "search_read": ("domain", "fields", "offset", "limit", "order"),
    "read": ("ids", "fields", "load"),
    "write": ("ids", "vals"),
    "unlink": ("ids",),
    "create": ("vals_list",),
    "name_search": ("name", "domain", "operator", "limit"),
    "fields_get": ("allfields", "attributes"),
}

READ_ONLY_METHODS = {
    "search",
    "search_count",
    "search_read",
    "read",
    "fields_get",
    "name_get",
    "name_search",
    "context_get",
}
DESTRUCTIVE_METHODS = {"create", "write", "unlink"}
MODEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
ODOO20_RPC_REMOVAL = "Odoo 20 fall 2026"
SIDE_EFFECT_METHOD_PATTERNS = (
    re.compile(r"^action_"),
    re.compile(r"^button_"),
    re.compile(r"(^|_)send($|_)"),
    re.compile(r"(^|_)post($|_)"),
    re.compile(r"(^|_)validate($|_)"),
)


def normalize_args(args: list[Any] | tuple[Any, ...] | None) -> list[Any]:
    """Return a JSON-serializable positional argument list."""
    return list(args or [])


def normalize_kwargs(kwargs: dict[str, Any] | None) -> dict[str, Any]:
    """Return a shallow copy of keyword arguments."""
    return dict(kwargs or {})


def classify_method_safety(method: str) -> dict[str, Any]:
    """Classify likely method side effects from the method name."""
    if method in DESTRUCTIVE_METHODS:
        return {
            "safety": "destructive",
            "destructive_method": True,
            "confidence": "high",
        }
    if method in READ_ONLY_METHODS or method.startswith(("get_", "_get_")):
        return {
            "safety": "read_only",
            "destructive_method": False,
            "confidence": "high" if method in READ_ONLY_METHODS else "medium",
        }
    if method == "message_post" or any(
        pattern.search(method) for pattern in SIDE_EFFECT_METHOD_PATTERNS
    ):
        return {
            "safety": "side_effect",
            "destructive_method": False,
            "confidence": "medium",
        }
    return {
        "safety": "unknown",
        "destructive_method": False,
        "confidence": "low",
    }


def build_json2_body(
    model: str,
    method: str,
    args: list[Any] | tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Map XML-RPC-style positional args plus kwargs to a JSON-2 named body."""
    positional = normalize_args(args)
    body = normalize_kwargs(kwargs)
    warnings: list[dict[str, str]] = []
    if not positional:
        return body, warnings

    arg_names = JSON2_POSITIONAL_ARG_MAP.get(method)
    if arg_names is None:
        warnings.append(
            {
                "code": "json2_positional_unsupported",
                "message": (
                    f"JSON-2 requires named arguments for {model}.{method}; "
                    "custom positional arguments cannot be mapped safely."
                ),
            }
        )
        return body, warnings

    if len(positional) > len(arg_names):
        warnings.append(
            {
                "code": "json2_too_many_positional_args",
                "message": (
                    f"{model}.{method} accepts at most {len(arg_names)} mapped "
                    f"positional arguments for JSON-2 preview; got {len(positional)}."
                ),
            }
        )

    for name, value in zip(arg_names, positional):
        if name in body:
            warnings.append(
                {
                    "code": "json2_duplicate_argument",
                    "message": (
                        f"{model}.{method} received {name!r} both positionally "
                        "and as a keyword; keeping the keyword value."
                    ),
                }
            )
            continue
        body[name] = value

    return body, warnings


def normalize_base_url(base_url: str | None) -> str | None:
    """Normalize a preview base URL without reading any local config."""
    if not base_url:
        return None
    if not re.match(r"^https?://", base_url):
        base_url = f"https://{base_url}"
    return base_url.rstrip("/")


def sanitize_odoo_error(
    error: str | dict[str, Any] | None,
    *,
    include_debug: bool = False,
) -> dict[str, Any] | None:
    """Normalize an Odoo-shaped error and redact debug details by default."""
    if error is None:
        return None

    payload: dict[str, Any]
    if isinstance(error, dict):
        payload = dict(error)
    else:
        payload = _parse_error_string(error)

    debug = payload.get("debug")
    return {
        "name": payload.get("name"),
        "message": payload.get(
            "message", str(error) if not isinstance(error, dict) else None
        ),
        "arguments": payload.get("arguments", []),
        "context": payload.get("context", {}),
        "debug": debug if include_debug and debug is not None else "[redacted]",
    }


def _parse_error_string(error: str) -> dict[str, Any]:
    """Best-effort extraction of an Odoo JSON error object from a string."""
    start = error.find("{")
    end = error.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(error[start : end + 1])
            if isinstance(parsed, dict):
                nested_error = parsed.get("error")
                if isinstance(nested_error, dict):
                    return nested_error
                return parsed
        except json.JSONDecodeError:
            pass
    return {"message": error, "arguments": [], "context": {}}


def generate_json2_payload_report(
    *,
    model: str,
    method: str,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    base_url: str | None = None,
    database: str | None = None,
    include_database_header: bool = True,
) -> dict[str, Any]:
    """Build a JSON-2 request preview without credentials or network access."""
    normalized_url = normalize_base_url(base_url)
    path = f"/json/2/{model}/{method}"
    body, warnings = build_json2_body(model, method, args, kwargs)
    safety = classify_method_safety(method)
    if safety["destructive_method"]:
        warnings.append(
            {
                "code": "destructive_method",
                "message": f"{model}.{method} may modify or delete Odoo data.",
            }
        )
    elif safety["safety"] in {"side_effect", "unknown"}:
        code = (
            "side_effect_method"
            if safety["safety"] == "side_effect"
            else "unknown_side_effects"
        )
        warnings.append(
            {
                "code": code,
                "message": (
                    f"{model}.{method} is not a known read-only ORM method; "
                    "review server-side implementation before executing it."
                ),
            }
        )

    headers: dict[str, Any] = {
        "Authorization": "bearer <api-key>",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers["X-Odoo-Database"] = (
        database if include_database_header and database else None
    )

    return {
        "success": not any(
            w["code"] == "json2_positional_unsupported" for w in warnings
        ),
        "tool": "generate_json2_payload",
        "model": model,
        "method": method,
        "endpoint": {
            "path": path,
            "url": f"{normalized_url}{path}" if normalized_url else None,
        },
        "headers": headers,
        "body": body,
        "warnings": warnings,
        "transaction": {
            "per_call": True,
            "warning": (
                "Each JSON-2 HTTP request is its own Odoo transaction; "
                "chain multi-step business operations server-side when atomicity matters."
            ),
        },
        "classification": safety,
        "metadata_used": {"client_instantiated": False},
    }


def diagnose_odoo_call_report(
    *,
    model: str,
    method: str,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    transport: str = "auto",
    target_version: str | None = None,
    observed_error: str | dict[str, Any] | None = None,
    include_debug: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain likely issues and corrected payload shape for an Odoo call."""
    issues: list[dict[str, str]] = []
    if not MODEL_NAME_RE.fullmatch(model):
        issues.append(
            {
                "code": "invalid_model_name",
                "severity": "error",
                "message": "Use an Odoo technical model name like 'res.partner'.",
            }
        )

    payload_report = generate_json2_payload_report(
        model=model,
        method=method,
        args=args,
        kwargs=kwargs,
        database=None,
        include_database_header=False,
    )
    for warning in payload_report["warnings"]:
        issues.append(
            {
                "code": warning["code"],
                "severity": (
                    "error"
                    if warning["code"] == "json2_positional_unsupported"
                    else "warning"
                ),
                "message": warning["message"],
            }
        )

    safety = classify_method_safety(method)
    json2_ready = not any(issue["code"].startswith("json2_") for issue in issues)
    compatibility = _transport_compatibility(transport, json2_ready)
    if (
        target_version
        and _major_version(target_version) >= 20
        and transport == "xmlrpc"
    ):
        issues.append(
            {
                "code": "deprecated_rpc_transport",
                "severity": "error",
                "message": (
                    "XML-RPC/JSON-RPC are scheduled for removal in Odoo 20; "
                    "migrate this call to JSON-2."
                ),
            }
        )

    return {
        "success": not any(issue["severity"] == "error" for issue in issues),
        "tool": "diagnose_odoo_call",
        "model": model,
        "method": method,
        "classification": {
            **safety,
            "transport_compatibility": compatibility,
            "json2_ready": json2_ready,
        },
        "issues": issues,
        "suggested_payload": {
            "args": normalize_args(args),
            "kwargs": normalize_kwargs(kwargs),
            "json2": payload_report if json2_ready else None,
        },
        "observed_error": sanitize_odoo_error(
            observed_error, include_debug=include_debug
        ),
        "metadata_used": {
            "fields_get": bool(metadata),
            "source": "input" if metadata else "none",
        },
        "next_actions": _diagnostic_next_actions(safety, json2_ready, bool(metadata)),
    }


def inspect_model_relationships_report(
    *,
    model: str,
    fields_metadata: dict[str, Any] | None,
    metadata_source: str,
    metadata_error: str | None = None,
    include_readonly: bool = True,
    include_computed: bool = True,
) -> dict[str, Any]:
    """Summarize relationship and write/create hints from fields_get metadata."""
    if not fields_metadata:
        return {
            "success": False,
            "tool": "inspect_model_relationships",
            "model": model,
            "error": metadata_error or "No field metadata available.",
            "summary": {"field_count": 0, "relationship_count": 0, "required_count": 0},
            "relationships": {"many2one": [], "one2many": [], "many2many": []},
            "required_fields": [],
            "create_hints": [],
            "write_hints": [],
            "metadata_used": {
                "fields_get": False,
                "source": metadata_source,
                "error": metadata_error,
            },
        }

    relationships: dict[str, list[dict[str, Any]]] = {
        "many2one": [],
        "one2many": [],
        "many2many": [],
    }
    required_fields: list[dict[str, Any]] = []
    create_hints: list[dict[str, str]] = []
    write_hints: list[dict[str, str]] = []

    for field_name, raw_meta in sorted(fields_metadata.items()):
        if not isinstance(raw_meta, dict):
            continue
        meta = raw_meta
        field_type = str(meta.get("type", ""))
        readonly = bool(meta.get("readonly", False))
        required = bool(meta.get("required", False))
        computed = bool(meta.get("compute") or meta.get("computed"))
        relation = meta.get("relation")
        if readonly and not include_readonly:
            continue
        if computed and not include_computed:
            continue

        if field_type in relationships:
            relationships[field_type].append(
                {
                    "name": field_name,
                    "relation": relation,
                    "required": required,
                    "readonly": readonly,
                    "string": meta.get("string"),
                }
            )
        if required:
            required_fields.append(
                {"name": field_name, "type": field_type, "relation": relation}
            )
            if not readonly and not computed:
                create_hints.append(
                    {
                        "field": field_name,
                        "hint": "Required on create unless Odoo provides a default.",
                    }
                )
        if readonly:
            write_hints.append(
                {"field": field_name, "hint": "Readonly in fields_get; do not write."}
            )
        elif field_type == "many2one":
            write_hints.append(
                {"field": field_name, "hint": "Write the related record ID."}
            )
        elif field_type in {"one2many", "many2many"}:
            write_hints.append(
                {
                    "field": field_name,
                    "hint": "Use Odoo relational command lists for create/write.",
                }
            )

    relationship_count = sum(len(items) for items in relationships.values())
    return {
        "success": True,
        "tool": "inspect_model_relationships",
        "model": model,
        "summary": {
            "field_count": len(fields_metadata),
            "relationship_count": relationship_count,
            "required_count": len(required_fields),
        },
        "relationships": relationships,
        "required_fields": required_fields,
        "create_hints": create_hints,
        "write_hints": write_hints,
        "metadata_used": {
            "fields_get": True,
            "source": metadata_source,
            "error": metadata_error,
        },
    }


def upgrade_risk_report(
    *,
    source_version: str | None = None,
    target_version: str | None = None,
    modules: list[dict[str, Any]] | None = None,
    methods: list[dict[str, Any]] | None = None,
    source_findings: list[dict[str, Any]] | None = None,
    observed_errors: list[str | dict[str, Any]] | None = None,
    include_debug: bool = False,
) -> dict[str, Any]:
    """Build an input-driven Odoo upgrade risk report."""
    risks: list[dict[str, str]] = []
    target_major = _major_version(target_version)
    if target_major >= 20:
        risks.append(
            {
                "code": "xmlrpc_jsonrpc_removal",
                "severity": "error",
                "evidence": f"Target version {target_version} reaches {ODOO20_RPC_REMOVAL}.",
                "recommendation": "Move integrations to External JSON-2 with named arguments.",
            }
        )
    elif target_major == 19 or source_version:
        risks.append(
            {
                "code": "json2_migration",
                "severity": "warning",
                "evidence": "Odoo 19 introduces External JSON-2 as the replacement API.",
                "recommendation": "Prefer JSON-2 payload previews and avoid new XML-RPC-only integrations.",
            }
        )

    destructive_methods: list[dict[str, Any]] = []
    for method_fact in methods or []:
        method = str(method_fact.get("method", ""))
        model = str(method_fact.get("model", ""))
        safety = classify_method_safety(method)
        if safety["destructive_method"]:
            destructive_methods.append(
                {
                    "model": model,
                    "method": method,
                    "source": method_fact.get("source", "input"),
                }
            )
            risks.append(
                {
                    "code": "destructive_method_review",
                    "severity": "warning",
                    "evidence": f"{model}.{method} can modify Odoo data.",
                    "recommendation": "Validate access rules, required fields, and transaction boundaries.",
                }
            )
        elif safety["safety"] == "unknown":
            risks.append(
                {
                    "code": "unknown_custom_method",
                    "severity": "warning",
                    "evidence": f"{model}.{method} side effects are unknown.",
                    "recommendation": "Inspect custom module source before migrating or invoking.",
                }
            )

    for module in modules or []:
        module_name = str(module.get("name", module.get("module", "unknown")))
        if module.get("custom") or module_name.startswith(("x_", "studio_")):
            risks.append(
                {
                    "code": "custom_module_upgrade",
                    "severity": "warning",
                    "evidence": f"{module_name} appears custom or Studio-like.",
                    "recommendation": "Test views, fields, reports, actions, and access rules on staging.",
                }
            )

    for finding in source_findings or []:
        risks.append(
            {
                "code": str(finding.get("code", "source_finding")),
                "severity": str(finding.get("severity", "warning")),
                "evidence": str(finding.get("evidence", finding)),
                "recommendation": str(
                    finding.get(
                        "recommendation", "Review this source finding before upgrade."
                    )
                ),
            }
        )

    odoo_errors = [
        sanitize_odoo_error(error, include_debug=include_debug)
        for error in observed_errors or []
    ]
    risk = _max_risk(risks)
    return {
        "success": True,
        "tool": "upgrade_risk_report",
        "source_version": source_version,
        "target_version": target_version,
        "summary": {
            "risk": risk,
            "blocked": any(r["severity"] == "error" for r in risks),
        },
        "risks": risks,
        "transport": {
            "xmlrpc_jsonrpc_deprecation": ODOO20_RPC_REMOVAL,
            "json2_required": target_major >= 20,
        },
        "destructive_methods": destructive_methods,
        "odoo_errors": odoo_errors,
        "metadata_used": {
            "fields_get": False,
            "source_scan": bool(source_findings),
            "source": "input" if modules or methods or source_findings else "none",
        },
        "next_actions": [
            "Run generate_json2_payload for each integration call.",
            "Inspect custom modules, Studio fields, automated actions, reports, and views on staging.",
        ],
    }


def fit_gap_report(
    *,
    requirements: list[str | dict[str, Any]],
    available_models: list[str] | None = None,
    available_fields: dict[str, Any] | None = None,
    installed_modules: list[str | dict[str, Any]] | None = None,
    business_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify requirements into Odoo implementation-fit buckets."""
    items: list[dict[str, Any]] = []
    rollup = {"fit": 0, "partial": 0, "gap": 0, "unknown": 0}
    for raw_requirement in requirements:
        requirement = (
            str(raw_requirement.get("requirement", raw_requirement))
            if isinstance(raw_requirement, dict)
            else str(raw_requirement)
        )
        classification, confidence, evidence = _classify_requirement(
            requirement, available_models or [], installed_modules or []
        )
        rollup[_rollup_bucket(classification)] += 1
        items.append(
            {
                "requirement": requirement,
                "classification": classification,
                "confidence": confidence,
                "evidence": evidence,
                "recommended_next_calls": _recommended_fit_gap_calls(
                    requirement, classification
                ),
            }
        )

    return {
        "success": True,
        "tool": "fit_gap_report",
        "summary": rollup,
        "classification_counts": _classification_counts(items),
        "items": items,
        "metadata_used": {
            "fields_get": bool(available_fields),
            "modules": bool(installed_modules),
            "source": (
                "input"
                if available_models or available_fields or installed_modules
                else "none"
            ),
        },
        "assumptions": [
            "Classification is heuristic unless backed by provided model/module evidence.",
            "Validate fit/gap results with safe model and field inspection before implementation.",
        ],
        "business_context": business_context or {},
    }


def _transport_compatibility(transport: str, json2_ready: bool) -> str:
    normalized = transport.strip().lower()
    if normalized == "auto":
        return "both" if json2_ready else "xmlrpc"
    if normalized == "json2":
        return "json2" if json2_ready else "unknown"
    if normalized == "xmlrpc":
        return "xmlrpc"
    return "unknown"


def _major_version(version: str | None) -> int:
    if not version:
        return 0
    match = re.match(r"^(\d+)", version)
    return int(match.group(1)) if match else 0


def _diagnostic_next_actions(
    safety: dict[str, Any], json2_ready: bool, metadata_used: bool
) -> list[str]:
    actions: list[str] = []
    if safety["destructive_method"]:
        actions.append("Inspect required fields and access rules before executing.")
    if safety["safety"] in {"side_effect", "unknown"}:
        actions.append("Inspect custom method source before executing.")
    if not json2_ready:
        actions.append("Pass keyword arguments that match the Odoo method signature.")
    if not metadata_used:
        actions.append("Call inspect_model_relationships for field-level hints.")
    return actions


def _max_risk(risks: list[dict[str, str]]) -> str:
    severities = {risk["severity"] for risk in risks}
    if "error" in severities:
        return "high"
    if "warning" in severities:
        return "medium"
    return "low"


def _classify_requirement(
    requirement: str,
    available_models: list[str],
    installed_modules: list[str | dict[str, Any]],
) -> tuple[str, str, list[str]]:
    text = requirement.lower()
    model_text = " ".join(available_models).lower()
    module_text = " ".join(
        (
            str(module.get("name", module.get("module", "")))
            if isinstance(module, dict)
            else str(module)
        )
        for module in installed_modules
    ).lower()
    evidence: list[str] = []

    if any(
        term in text for term in ["bypass access", "direct database", "modify core"]
    ):
        return (
            "avoid",
            "medium",
            ["Requirement suggests bypassing Odoo safety boundaries."],
        )
    if any(
        term in text for term in ["studio", "custom field", "new field", "form view"]
    ):
        return "studio", "medium", ["Looks like field/view customization."]
    if any(
        term in text for term in ["custom", "integration", "api", "workflow", "complex"]
    ):
        return "custom_module", "medium", ["Likely requires Python/business logic."]
    if any(
        term in text
        for term in ["configure", "sequence", "email template", "tax", "approval"]
    ):
        return (
            "configuration",
            "medium",
            ["Likely solvable through Odoo configuration."],
        )
    standard_terms = [
        "contact",
        "partner",
        "invoice",
        "sale",
        "purchase",
        "inventory",
        "crm",
    ]
    if any(term in text for term in standard_terms):
        if model_text or module_text:
            evidence.append(
                "Provided model/module evidence suggests standard Odoo coverage."
            )
        else:
            evidence.append("Matches common standard Odoo app terminology.")
        return "standard", "medium", evidence
    return (
        "unknown",
        "low",
        ["Not enough model/module evidence to classify confidently."],
    )


def _rollup_bucket(classification: str) -> str:
    if classification in {"standard", "configuration"}:
        return "fit"
    if classification == "studio":
        return "partial"
    if classification in {"custom_module", "avoid"}:
        return "gap"
    return "unknown"


def _recommended_fit_gap_calls(
    requirement: str, classification: str
) -> list[dict[str, Any]]:
    calls = [
        {
            "tool": "list_models",
            "arguments": {
                "query": requirement.split()[0] if requirement.split() else None
            },
        }
    ]
    if classification in {"studio", "custom_module", "unknown"}:
        calls.append(
            {
                "tool": "inspect_model_relationships",
                "arguments": {"model": "res.partner", "use_live_metadata": True},
            }
        )
    return calls


def _classification_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        "standard": 0,
        "configuration": 0,
        "studio": 0,
        "custom_module": 0,
        "avoid": 0,
        "unknown": 0,
    }
    for item in items:
        classification = str(item.get("classification", "unknown"))
        counts[classification] = counts.get(classification, 0) + 1
    return counts
