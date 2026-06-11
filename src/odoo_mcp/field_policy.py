"""Read-path field-level access control policy (single enforcement choke point).

Opt-in, per-instance, per-model field allow/deny rules applied to every path
that returns record data: read tools, aggregate validation, the knowledge
indexer, ``odoo://`` resources, and ``get_model_fields`` metadata.

Design mirrors :mod:`write_policy`: env var -> JSON file -> validated,
cached structure. No policy file means no behavior change (zero-friction
upgrade). A malformed policy fails closed (raises at load, aborting startup)
rather than silently running unprotected.

Policy shape (a ``field_acl`` key in the shared policy file, or a dedicated
file via ``ODOO_MCP_FIELD_POLICY_FILE``)::

    {
      "field_acl": {
        "default":    { "res.partner": { "deny": ["credit_limit", "comment"] },
                        "*":           { "deny": ["message_ids"] } },
        "subsidiary": { "hr.employee": { "allow": ["name", "work_email"] } }
      }
    }

Each model entry has exactly one of ``deny`` (blacklist) or ``allow``
(exclusive whitelist). ``*`` is a per-instance model wildcard whose rules
merge with the specific model's. ``id`` is never redactable.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from .write_policy import policy_file_path

FIELD_POLICY_FILE_ENV = "ODOO_MCP_FIELD_POLICY_FILE"
FIELD_ACL_KEY = "field_acl"
ALWAYS_KEPT = frozenset({"id"})


class FieldPolicyError(ValueError):
    """Raised when a field policy file is present but malformed (fail closed)."""


@dataclass(frozen=True)
class ModelFieldRule:
    mode: str  # "deny" or "allow"
    fields: FrozenSet[str]


class FieldPolicy:
    """Validated, per-(instance, model) field rules with merge + redaction."""

    def __init__(self, by_instance: Dict[str, Dict[str, ModelFieldRule]]) -> None:
        self._by_instance = by_instance

    def active(self) -> bool:
        return bool(self._by_instance)

    def instances(self) -> List[str]:
        return sorted(self._by_instance)

    def _effective(
        self, instance: str, model: str
    ) -> Optional[Tuple[Optional[FrozenSet[str]], FrozenSet[str]]]:
        """Merge the instance's ``*`` and specific-model rules.

        Returns ``(allow, deny)`` where ``allow`` is ``None`` (no whitelist)
        or the intersection of whitelists, and ``deny`` is the union of
        blacklists. ``None`` means no policy applies (pass-through).
        """
        models = self._by_instance.get(instance)
        if not models:
            return None
        star = models.get("*")
        specific = models.get(model)
        if star is None and specific is None:
            return None
        allow: Optional[set[str]] = None
        deny: set[str] = set()
        for rule in (star, specific):
            if rule is None:
                continue
            if rule.mode == "deny":
                deny |= rule.fields
            else:
                allow = set(rule.fields) if allow is None else (allow & rule.fields)
        return (frozenset(allow) if allow is not None else None, frozenset(deny))

    def filter_fields(
        self, instance: str, model: str, field_names: Iterable[str]
    ) -> Tuple[List[str], List[str]]:
        """Split field names into (kept, redacted) for one (instance, model)."""
        effective = self._effective(instance, model)
        names = list(field_names)
        if effective is None:
            return names, []
        allow, deny = effective
        kept: List[str] = []
        redacted: List[str] = []
        for name in names:
            if name in ALWAYS_KEPT:
                kept.append(name)
            elif allow is not None and name not in allow:
                redacted.append(name)
            elif name in deny:
                redacted.append(name)
            else:
                kept.append(name)
        return kept, redacted

    def redact_record(
        self, instance: str, model: str, record: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Return a copy of ``record`` with denied keys removed + their names."""
        effective = self._effective(instance, model)
        if effective is None:
            return record, []
        kept, redacted = self.filter_fields(instance, model, record.keys())
        kept_set = set(kept)
        filtered = {k: v for k, v in record.items() if k in kept_set}
        return filtered, redacted

    def redact_records(
        self, instance: str, model: str, records: Iterable[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Redact a list of records; returns (records, sorted redacted names)."""
        out: List[Dict[str, Any]] = []
        redacted: set[str] = set()
        for record in records:
            filtered, names = self.redact_record(instance, model, record)
            out.append(filtered)
            redacted.update(names)
        return out, sorted(redacted)

    def restricted_fields(
        self, instance: str, model: str, field_names: Iterable[str]
    ) -> List[str]:
        """Field names that would be redacted (for get_model_fields marking)."""
        _, redacted = self.filter_fields(instance, model, field_names)
        return redacted

    def check_aggregate(
        self, instance: str, model: str, fields: Iterable[str]
    ) -> Optional[str]:
        """Return an error string if any aggregate/groupby field is denied."""
        _, redacted = self.filter_fields(instance, model, fields)
        if redacted:
            return (
                "Field policy denies access to "
                f"{sorted(redacted)} on {model}; aggregation on restricted "
                "fields is blocked to prevent inference."
            )
        return None


def _parse_field_policy(data: Dict[str, Any]) -> FieldPolicy:
    acl = data.get(FIELD_ACL_KEY, {})
    if not isinstance(acl, dict):
        raise FieldPolicyError(f"'{FIELD_ACL_KEY}' must be an object")
    by_instance: Dict[str, Dict[str, ModelFieldRule]] = {}
    for instance, models in acl.items():
        if not isinstance(models, dict):
            raise FieldPolicyError(f"field_acl['{instance}'] must be an object")
        rules: Dict[str, ModelFieldRule] = {}
        for model, spec in models.items():
            if not isinstance(spec, dict):
                raise FieldPolicyError(
                    f"field_acl['{instance}']['{model}'] must be an object"
                )
            has_deny = "deny" in spec
            has_allow = "allow" in spec
            if has_deny == has_allow:
                raise FieldPolicyError(
                    f"field_acl['{instance}']['{model}'] needs exactly one of "
                    "'deny' or 'allow'"
                )
            mode = "deny" if has_deny else "allow"
            raw = spec[mode]
            if not isinstance(raw, list) or not all(
                isinstance(item, str) for item in raw
            ):
                raise FieldPolicyError(
                    f"field_acl['{instance}']['{model}']['{mode}'] must be a "
                    "list of field-name strings"
                )
            rules[model] = ModelFieldRule(
                mode=mode,
                fields=frozenset(item.strip() for item in raw if item.strip()),
            )
        by_instance[instance] = rules
    return FieldPolicy(by_instance)


def field_policy_file_path() -> Optional[str]:
    """Dedicated field-policy file, else the shared write-policy file."""
    explicit = os.environ.get(FIELD_POLICY_FILE_ENV, "").strip()
    if explicit:
        return explicit
    return policy_file_path()


def load_field_policy() -> FieldPolicy:
    """Load and validate the field policy; raise FieldPolicyError if malformed."""
    path = field_policy_file_path()
    if path is None:
        return FieldPolicy({})
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FieldPolicyError(f"Cannot read field policy file {path}: {exc}")
    if not isinstance(data, dict):
        raise FieldPolicyError(f"Field policy file {path} must contain an object")
    return _parse_field_policy(data)


_policy: Optional[FieldPolicy] = None
_policy_lock = threading.Lock()


def get_field_policy() -> FieldPolicy:
    """Process-wide field policy, loaded lazily and cached."""
    global _policy
    with _policy_lock:
        if _policy is None:
            _policy = load_field_policy()
        return _policy


def reset_field_policy() -> None:
    """Drop the cached policy (intended for tests and lifespan reload)."""
    global _policy
    with _policy_lock:
        _policy = None


def field_policy_posture() -> Dict[str, Any]:
    """Non-secret health posture: whether field ACL is active and scope counts."""
    try:
        policy = get_field_policy()
    except FieldPolicyError as exc:
        return {"active": False, "error": str(exc)}
    return {
        "active": policy.active(),
        "instances_with_rules": len(policy.instances()),
    }
