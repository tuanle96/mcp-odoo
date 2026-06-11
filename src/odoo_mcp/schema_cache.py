"""Bounded TTL/LRU cache backing the per-instance schema caches."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict

DEFAULT_SCHEMA_CACHE_MAX_ENTRIES = 256
DEFAULT_SCHEMA_CACHE_TTL_SECONDS = 10 * 60


def _schema_cache_settings() -> tuple[int, float]:
    """Read schema cache bounds from env with safe defaults."""
    raw_max = os.environ.get("ODOO_MCP_SCHEMA_CACHE_MAX", "").strip()
    raw_ttl = os.environ.get("ODOO_MCP_SCHEMA_CACHE_TTL", "").strip()
    try:
        max_entries = int(raw_max) if raw_max else DEFAULT_SCHEMA_CACHE_MAX_ENTRIES
    except ValueError:
        max_entries = DEFAULT_SCHEMA_CACHE_MAX_ENTRIES
    try:
        ttl = float(raw_ttl) if raw_ttl else DEFAULT_SCHEMA_CACHE_TTL_SECONDS
    except ValueError:
        ttl = DEFAULT_SCHEMA_CACHE_TTL_SECONDS
    return max(1, max_entries), max(1.0, ttl)


class BoundedTTLCache:
    """Dict-compatible cache with per-entry TTL and LRU size bound.

    Replaces the previously unbounded schema cache so long-lived servers
    fronting many instances cannot grow without limit.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_SCHEMA_CACHE_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_SCHEMA_CACHE_TTL_SECONDS,
    ) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: Dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _purge_locked(self, key: str) -> Any:
        expires_at, value = self._entries[key]
        if time.time() >= expires_at:
            del self._entries[key]
            raise KeyError(key)
        # Refresh LRU position.
        del self._entries[key]
        self._entries[key] = (expires_at, value)
        return value

    def __contains__(self, key: object) -> bool:
        try:
            self[str(key)]
            return True
        except KeyError:
            return False

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            if key not in self._entries:
                raise KeyError(key)
            return self._purge_locked(key)

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = (time.time() + self.ttl_seconds, value)
            while len(self._entries) > self.max_entries:
                oldest = next(iter(self._entries))
                del self._entries[oldest]

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _build_schema_cache() -> BoundedTTLCache:
    max_entries, ttl = _schema_cache_settings()
    return BoundedTTLCache(max_entries=max_entries, ttl_seconds=ttl)
