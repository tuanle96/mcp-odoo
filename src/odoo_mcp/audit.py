"""Append-only JSONL audit trail for write-path events.

Opt-in via ODOO_MCP_AUDIT_LOG=<path>. One line per event so operators can
answer "what did the agent change, when, and was it approved?" without
trusting chat transcripts. Tokens are stored as truncated SHA-256 digests,
never in clear text.

Audit failures never block the underlying operation (fail-open); they are
logged as warnings instead. Operators who need fail-closed semantics should
alert on the warning log line.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

AUDIT_LOG_ENV = "ODOO_MCP_AUDIT_LOG"
_write_lock = threading.Lock()


def audit_log_path() -> str | None:
    """Return the configured audit log path, or None when disabled."""
    path = os.environ.get(AUDIT_LOG_ENV, "").strip()
    return path or None


def audit_posture() -> dict[str, Any]:
    """Non-secret audit posture for health_check / runtime_security_report."""
    path = audit_log_path()
    return {"enabled": path is not None, "path": path, "env": AUDIT_LOG_ENV}


def _token_digest(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def record_write_event(
    event: str,
    *,
    outcome: str,
    model: str | None = None,
    operation: str | None = None,
    record_ids: list[int] | None = None,
    instance: str | None = None,
    token: str | None = None,
    detail: str | None = None,
    principal: str | None = None,
    client_user_id: str | None = None,
    identity: Optional[Mapping[str, Optional[str]]] = None,
) -> bool:
    """Append one audit line; returns True when a line was written.

    ``principal`` is the acting Odoo login and ``client_user_id`` the opaque
    front-end user id when the server runs in request identity mode; both are
    None in configured mode. ``identity`` is a convenience mapping with those
    two keys (see ``RequestIdentity.audit_fields``). Credentials are never
    accepted here by design.
    """
    path = audit_log_path()
    if path is None:
        return False
    if identity:
        principal = principal or identity.get("principal")
        client_user_id = client_user_id or identity.get("client_user_id")
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "outcome": outcome,
        "model": model,
        "operation": operation,
        "record_ids": list(record_ids or []),
        "instance": instance or "default",
        "principal": principal,
        "client_user_id": client_user_id,
        "token_sha256": _token_digest(token),
        "detail": detail,
    }
    try:
        line = json.dumps(entry, sort_keys=True, default=str)
        with _write_lock:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return True
    except OSError as exc:
        logger.warning("audit log write failed (%s): %s", path, exc)
        return False
