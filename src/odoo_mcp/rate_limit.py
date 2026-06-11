"""Sliding-window call-rate tracking for tool invocations.

Complements the existing N+1 read detection: that catches *pathological
shapes* (per-record read loops); this catches *raw volume* (an agent
hammering any tool in a tight loop), per instance and per tool.

Modes (``ODOO_MCP_RATE_LIMIT_MODE``):
- ``off``   — no tracking (default; preserves existing behaviour).
- ``warn``  — track and surface counters via :func:`rate_report`, never block.
- ``block`` — refuse calls over the window budget with a structured error.

Budget knobs: ``ODOO_MCP_RATE_LIMIT_MAX_CALLS`` calls per
``ODOO_MCP_RATE_LIMIT_WINDOW`` seconds, keyed by ``instance:tool``.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_RATE_LIMIT_MAX_CALLS = 120
MAX_TRACKED_KEYS = 512

_VALID_MODES = ("off", "warn", "block")


def rate_limit_mode() -> str:
    """Return the configured mode, defaulting to off on bad values."""
    raw = os.environ.get("ODOO_MCP_RATE_LIMIT_MODE", "off").strip().lower()
    return raw if raw in _VALID_MODES else "off"


def _rate_limit_settings() -> tuple[float, int]:
    raw_window = os.environ.get("ODOO_MCP_RATE_LIMIT_WINDOW", "").strip()
    raw_max = os.environ.get("ODOO_MCP_RATE_LIMIT_MAX_CALLS", "").strip()
    try:
        window = float(raw_window) if raw_window else DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    except ValueError:
        window = DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    try:
        max_calls = int(raw_max) if raw_max else DEFAULT_RATE_LIMIT_MAX_CALLS
    except ValueError:
        max_calls = DEFAULT_RATE_LIMIT_MAX_CALLS
    return max(1.0, window), max(1, max_calls)


class SlidingWindowRateTracker:
    """Per-key sliding window counters with a bounded key set."""

    def __init__(
        self,
        window_seconds: Optional[float] = None,
        max_calls: Optional[int] = None,
    ) -> None:
        default_window, default_max = _rate_limit_settings()
        self.window_seconds = window_seconds or default_window
        self.max_calls = max_calls or default_max
        self._events: Dict[str, Deque[float]] = {}
        self._over_budget_totals: Dict[str, int] = {}
        self._lock = threading.Lock()

    def _trim_locked(self, key: str, now: float) -> None:
        events = self._events.get(key)
        if events is None:
            return
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            del self._events[key]

    def record(
        self, instance: str, tool: str, count_over_budget: bool = False
    ) -> Dict[str, Any]:
        """Record one call; report whether it fits the window budget.

        ``count_over_budget=True`` (warn mode) appends the event even when
        over budget so counters reflect real executed volume; block mode
        leaves refused calls out so they do not extend the blocked window.
        """
        key = f"{instance}:{tool}"
        now = time.time()
        with self._lock:
            self._trim_locked(key, now)
            events = self._events.setdefault(key, deque())
            over_budget = len(events) >= self.max_calls
            if over_budget:
                self._over_budget_totals[key] = (
                    self._over_budget_totals.get(key, 0) + 1
                )
            if not over_budget or count_over_budget:
                events.append(now)
            # Bound the tracked key set: drop the stalest key wholesale.
            if len(self._events) > MAX_TRACKED_KEYS:
                stalest = min(self._events, key=lambda k: self._events[k][-1])
                del self._events[stalest]
            return {
                "key": key,
                "over_budget": over_budget,
                "calls_in_window": len(events),
                "max_calls": self.max_calls,
                "window_seconds": self.window_seconds,
            }

    def report(self, top: int = 10) -> Dict[str, Any]:
        """Counters for health_check: busiest keys plus refusal totals."""
        now = time.time()
        with self._lock:
            for key in list(self._events):
                self._trim_locked(key, now)
            busiest = sorted(
                ((key, len(events)) for key, events in self._events.items()),
                key=lambda item: item[1],
                reverse=True,
            )[: max(1, top)]
            return {
                "mode": rate_limit_mode(),
                "window_seconds": self.window_seconds,
                "max_calls": self.max_calls,
                "busiest": [
                    {"key": key, "calls_in_window": count} for key, count in busiest
                ],
                "over_budget_totals": dict(self._over_budget_totals),
            }


_tracker: Optional[SlidingWindowRateTracker] = None
_tracker_lock = threading.Lock()


def get_rate_tracker() -> SlidingWindowRateTracker:
    """Process-wide tracker, built lazily so env knobs apply at first use."""
    global _tracker
    with _tracker_lock:
        if _tracker is None:
            _tracker = SlidingWindowRateTracker()
        return _tracker


def reset_rate_tracker() -> None:
    """Drop the process tracker (intended for tests)."""
    global _tracker
    with _tracker_lock:
        _tracker = None


def check_rate(instance: str, tool: str) -> Optional[Dict[str, Any]]:
    """Record a call; return a structured refusal when blocking applies.

    Returns ``None`` when the call may proceed (mode off, warn, or within
    budget). In ``warn`` mode the over-budget signal is only surfaced via
    :func:`rate_report`.
    """
    mode = rate_limit_mode()
    if mode == "off":
        return None
    verdict = get_rate_tracker().record(
        instance, tool, count_over_budget=(mode == "warn")
    )
    if mode == "block" and verdict["over_budget"]:
        return {
            "success": False,
            "error": (
                f"Rate limit exceeded for {verdict['key']}: "
                f"{verdict['max_calls']} calls per "
                f"{verdict['window_seconds']:.0f}s. Retry later or raise "
                "ODOO_MCP_RATE_LIMIT_MAX_CALLS."
            ),
            "rate_limited": True,
        }
    return None


def rate_report() -> Dict[str, Any]:
    """Rate posture for health_check; cheap when tracking is off."""
    if rate_limit_mode() == "off":
        return {"mode": "off"}
    return get_rate_tracker().report()
