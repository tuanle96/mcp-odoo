"""Write-enablement flags and the reviewed side-effect method policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tool_helpers import truthy_env

POLICY_FILE_ENV = "ODOO_MCP_POLICY_FILE"
DEFAULT_POLICY_FILENAME = "odoo_mcp_policy.json"


def writes_enabled() -> bool:
    """Return whether destructive approved writes are enabled for this process."""
    return truthy_env("ODOO_MCP_ENABLE_WRITES")


def chatter_direct_enabled() -> bool:
    """Return True when chatter_post may bypass approval-token gating."""
    return truthy_env("MCP_CHATTER_DIRECT")


def policy_file_path() -> Optional[str]:
    """Return the side-effect policy file path, or None when not configured."""
    explicit = os.environ.get(POLICY_FILE_ENV, "").strip()
    if explicit:
        return explicit
    if os.path.exists(DEFAULT_POLICY_FILENAME):
        return DEFAULT_POLICY_FILENAME
    return None


def load_side_effect_policy() -> Dict[str, Any]:
    """Load reviewed side-effect methods from the version-controllable policy file.

    Entries may be plain strings ("sale.order.action_confirm") or objects with
    a "method" key plus free-form review metadata (reviewed_by, date, reason).
    A broken policy file contributes no methods (fail closed) and surfaces its
    error in the runtime posture.
    """
    path = policy_file_path()
    if path is None:
        return {"path": None, "methods": [], "error": None}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"path": path, "methods": [], "error": str(exc)}
    methods: List[str] = []
    for entry in data.get("allowed_side_effect_methods", []) or []:
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            name = str(entry.get("method", "")).strip()
        else:
            name = ""
        if name:
            methods.append(name)
    return {"path": path, "methods": methods, "error": None}


def allowed_side_effect_methods() -> List[str]:
    """Return exact model.method names reviewed for side effects (env + policy file)."""
    raw_value = os.environ.get("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "")
    from_env = [item.strip() for item in raw_value.split(",") if item.strip()]
    from_file = load_side_effect_policy()["methods"]
    merged: List[str] = []
    for name in [*from_env, *from_file]:
        if name not in merged:
            merged.append(name)
    return merged


def side_effect_method_allowed(model: str, method: str) -> bool:
    """Check exact side-effect allowlist entries."""
    return f"{model}.{method}" in set(allowed_side_effect_methods())
