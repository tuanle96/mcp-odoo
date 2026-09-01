"""Per-request Odoo identity (Algorithma fork of odoo-mcp).

Two concepts stay strictly separate:

    INSTANCE = WHERE  - which Odoo server + database. That is ERPipe's existing
                        instance configuration (url, db, transport, timeout, ...).
    IDENTITY = WHO    - which Odoo user acts on *this* request.

In ``ODOO_MCP_IDENTITY_MODE=request`` the WHO arrives as HTTP headers that the
chat front end (LibreChat) sets on every MCP call:

    X-User-Email          Odoo login of the human who is asking
    X-Odoo-Api-Key        that user's personal Odoo API key
    X-LibreChat-User-Id   optional opaque client-side user id (audit only)

The server authenticates *that* user against Odoo and executes every call with
the resulting uid + key, so Odoo's own ACLs and record rules remain the
authorization authority. There is deliberately no fallback to configured
service credentials in this mode: a request without a usable identity fails
closed.

This is a pure core module (see ``.importlinter``): it never imports the MCP
surface, and it never logs, prints, or serializes the API key.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from .schema_cache import BoundedTTLCache

IDENTITY_MODE_ENV = "ODOO_MCP_IDENTITY_MODE"
IDENTITY_MODE_CONFIGURED = "configured"
IDENTITY_MODE_REQUEST = "request"
IDENTITY_MODES = frozenset({IDENTITY_MODE_CONFIGURED, IDENTITY_MODE_REQUEST})

HEADER_USER_EMAIL = "x-user-email"
HEADER_ODOO_API_KEY = "x-odoo-api-key"
HEADER_LIBRECHAT_USER_ID = "x-librechat-user-id"
REQUIRED_IDENTITY_HEADERS = (HEADER_USER_EMAIL, HEADER_ODOO_API_KEY)
IDENTITY_HEADERS = (*REQUIRED_IDENTITY_HEADERS, HEADER_LIBRECHAT_USER_ID)

IDENTITY_CACHE_TTL_ENV = "ODOO_MCP_IDENTITY_CACHE_TTL"
IDENTITY_CACHE_MAX_ENV = "ODOO_MCP_IDENTITY_CACHE_MAX"
DEFAULT_IDENTITY_CACHE_TTL_SECONDS = 15 * 60
DEFAULT_IDENTITY_CACHE_MAX_ENTRIES = 256

MAX_LOGIN_LENGTH = 254
MAX_API_KEY_LENGTH = 512
MAX_CLIENT_USER_ID_LENGTH = 128

# Tools whose state is process-wide and keyed by instance only. In request
# mode they would let one user read what another user indexed or submitted, so
# health_check warns when they are still registered (exclude them with
# ODOO_MCP_TOOLS_EXCLUDE until they are made identity-aware).
CROSS_USER_STATE_TOOLS = frozenset(
    {
        "index_knowledge",
        "search_knowledge",
        "knowledge_stats",
        "submit_async_task",
        "get_async_task",
        "cancel_async_task",
        "list_async_tasks",
    }
)


class IdentityError(ValueError):
    """Base class for identity failures; messages are safe to return to callers."""


class MissingIdentityError(IdentityError):
    """No usable identity on the request (headers absent or transport has none)."""


class InvalidIdentityError(IdentityError):
    """Identity headers are present but malformed."""


class IdentityAuthenticationError(IdentityError):
    """Odoo rejected the supplied login/API key."""


class IdentityOdooUnreachableError(IdentityError):
    """The Odoo instance could not be reached while authenticating the identity."""


def identity_mode() -> str:
    """Return ``configured`` (ERPipe default) or ``request`` (per-user headers).

    Any other value fails closed with ``ValueError`` so a typo can never end
    up running with shared credentials by accident.
    """
    raw = os.environ.get(IDENTITY_MODE_ENV, "").strip().lower()
    if not raw:
        return IDENTITY_MODE_CONFIGURED
    if raw not in IDENTITY_MODES:
        raise ValueError(
            f"{IDENTITY_MODE_ENV} must be one of {sorted(IDENTITY_MODES)}, got {raw!r}."
        )
    return raw


def request_identity_mode() -> bool:
    """True when every Odoo call must run as the user named in the request."""
    return identity_mode() == IDENTITY_MODE_REQUEST


@dataclass(frozen=True)
class RequestIdentity:
    """The WHO of one MCP request. Immutable; the API key never appears in repr."""

    email: str
    api_key: str = field(repr=False)
    librechat_user_id: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"RequestIdentity(email={self.email!r}, "
            f"librechat_user_id={self.librechat_user_id!r}, api_key=<redacted>)"
        )

    @property
    def principal(self) -> str:
        """Non-secret name of the acting user (the Odoo login)."""
        return self.email

    def credential_digest(self) -> str:
        """Short SHA-256 digest of the key for diagnostics; never the key itself."""
        return hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:16]

    def cache_key(self, instance: str, db: str) -> str:
        """Cache key covering instance, database, login, and credential.

        A different key for the same login is a different entry, so a rotated
        or revoked credential can never be served from another user's session,
        and instance A's session is never reused for instance B.
        """
        material = "\0".join(
            ("odoo-mcp-identity", instance, db, self.email.lower(), self.api_key)
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def approval_binding(self, instance: str) -> str:
        """Server-side binding stored with a validated write approval."""
        material = "\0".join(
            ("odoo-mcp-approval", instance, self.email.lower(), self.api_key)
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def audit_fields(self) -> dict[str, Optional[str]]:
        """Identity fields for audit entries - never includes the credential."""
        return {"principal": self.email, "client_user_id": self.librechat_user_id}


def _lookup_header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    """Case-insensitive header lookup that works for plain dicts too."""
    value = headers.get(name)
    if value is None:
        wanted = name.lower()
        for key, candidate in headers.items():
            if str(key).lower() == wanted:
                value = candidate
                break
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    return str(value)


def _clean_header_value(
    value: Optional[str], *, label: str, max_length: int
) -> Optional[str]:
    """Strip and validate one header value; returns None when absent/blank."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise InvalidIdentityError(f"{label} header exceeds {max_length} characters.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise InvalidIdentityError(
            f"{label} header contains whitespace or control characters."
        )
    return text


def resolve_request_identity(headers: Optional[Mapping[str, Any]]) -> RequestIdentity:
    """Build the request identity from HTTP headers; fails closed when missing.

    ``headers`` is whatever the transport attached to the request (a Starlette
    ``Headers`` mapping on Streamable HTTP, ``None`` on stdio). Header names
    are matched case-insensitively per HTTP semantics.
    """
    if headers is None:
        raise MissingIdentityError(
            "request identity mode requires the X-User-Email and X-Odoo-Api-Key "
            "headers, but this transport carries no request headers (stdio?). "
            "Use streamable-http."
        )
    email = _clean_header_value(
        _lookup_header(headers, HEADER_USER_EMAIL),
        label="X-User-Email",
        max_length=MAX_LOGIN_LENGTH,
    )
    api_key = _clean_header_value(
        _lookup_header(headers, HEADER_ODOO_API_KEY),
        label="X-Odoo-Api-Key",
        max_length=MAX_API_KEY_LENGTH,
    )
    missing = [
        label
        for label, value in (("X-User-Email", email), ("X-Odoo-Api-Key", api_key))
        if value is None
    ]
    if missing:
        raise MissingIdentityError(
            "missing request identity header(s): " + ", ".join(missing)
        )
    assert email is not None and api_key is not None
    client_user_id = _clean_header_value(
        _lookup_header(headers, HEADER_LIBRECHAT_USER_ID),
        label="X-LibreChat-User-Id",
        max_length=MAX_CLIENT_USER_ID_LENGTH,
    )
    return RequestIdentity(
        email=email, api_key=api_key, librechat_user_id=client_user_id
    )


def _identity_cache_settings() -> tuple[int, float]:
    """Read identity cache bounds from env with safe defaults."""
    raw_max = os.environ.get(IDENTITY_CACHE_MAX_ENV, "").strip()
    raw_ttl = os.environ.get(IDENTITY_CACHE_TTL_ENV, "").strip()
    try:
        max_entries = int(raw_max) if raw_max else DEFAULT_IDENTITY_CACHE_MAX_ENTRIES
    except ValueError:
        max_entries = DEFAULT_IDENTITY_CACHE_MAX_ENTRIES
    try:
        ttl = float(raw_ttl) if raw_ttl else DEFAULT_IDENTITY_CACHE_TTL_SECONDS
    except ValueError:
        ttl = DEFAULT_IDENTITY_CACHE_TTL_SECONDS
    return max(1, max_entries), max(1.0, ttl)


class IdentityClientCache:
    """Bounded TTL/LRU cache of authenticated per-identity Odoo clients.

    Keys are ``RequestIdentity.cache_key`` digests (instance + database +
    login + credential); the raw API key is never a key and never logged.
    The TTL bounds how long an authenticated session object lives; every
    XML-RPC / JSON-2 call still carries the credential, so a revoked key stops
    working at the next Odoo call regardless of the cache.
    """

    def __init__(
        self,
        max_entries: Optional[int] = None,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        default_max, default_ttl = _identity_cache_settings()
        self.max_entries = max_entries or default_max
        self.ttl_seconds = ttl_seconds or default_ttl
        self._cache = BoundedTTLCache(
            max_entries=self.max_entries, ttl_seconds=self.ttl_seconds
        )
        self._lock = threading.Lock()

    @classmethod
    def settings(cls) -> dict[str, Any]:
        """Configured bounds (for health posture) without touching a cache."""
        max_entries, ttl = _identity_cache_settings()
        return {
            "max_entries": max_entries,
            "ttl_seconds": ttl,
            "env": [IDENTITY_CACHE_MAX_ENV, IDENTITY_CACHE_TTL_ENV],
        }

    def get(self, key: str) -> Any:
        return self._cache.get(key)

    def get_or_create(self, key: str, factory: Callable[[], Any]) -> Any:
        """Return the cached client or build one (authenticating) and cache it.

        The factory runs outside the lock because it performs network I/O;
        two concurrent first calls for the same identity may both authenticate,
        which is harmless (last writer wins, both clients are valid).
        """
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        client = factory()
        with self._lock:
            existing = self._cache.get(key)
            if existing is not None:
                return existing
            self._cache[key] = client
        return client

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def posture(self) -> dict[str, Any]:
        return {
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
            "size": len(self),
        }


def identity_posture(
    *,
    configured_credentials_present: bool,
    transport: Optional[str] = None,
    registered_tools: Optional[Iterable[str]] = None,
    cache: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Non-secret identity posture for health_check.

    Makes an insecure configuration obvious: ``warnings`` is non-empty (and
    ``ok`` false) when request mode runs over stdio, when shared credentials
    are still configured next to request mode, or when tools with cross-user
    process state remain registered.
    """
    mode_error: Optional[str] = None
    try:
        mode = identity_mode()
    except ValueError as exc:
        mode = "invalid"
        mode_error = str(exc)
    request = mode == IDENTITY_MODE_REQUEST
    warnings: list[str] = []
    if mode_error:
        warnings.append(mode_error)
    if request and transport == "stdio":
        warnings.append(
            "stdio carries no HTTP headers: every Odoo tool call fails closed in "
            "request mode; serve streamable-http instead."
        )
    if request and configured_credentials_present:
        warnings.append(
            "configured Odoo credentials (username/password/api_key) are present; "
            "they are ignored in request mode - remove them so a mode change can "
            "never silently re-enable a shared account."
        )
    if request and registered_tools is not None:
        leaky = sorted(set(registered_tools) & CROSS_USER_STATE_TOOLS)
        if leaky:
            warnings.append(
                "tools with process-wide state shared across users are still "
                "registered: " + ", ".join(leaky) + "; exclude them with "
                "ODOO_MCP_TOOLS_EXCLUDE in request mode."
            )
    return {
        "identity_mode": mode,
        "request_identity_required": request,
        "configured_shared_credentials_used": mode == IDENTITY_MODE_CONFIGURED,
        "configured_credentials_present": configured_credentials_present,
        "identity_headers": list(IDENTITY_HEADERS),
        "cache": dict(cache or {}),
        "warnings": warnings,
        "ok": not warnings,
    }
