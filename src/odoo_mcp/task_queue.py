"""Bounded background task manager backing the async task MCP tools.

Long-running operations (addon scans, upgrade risk reports, batch reads)
can be submitted as background tasks and polled later, so agents are not
blocked on a single synchronous RPC round-trip.

Design constraints:
- Thread-based (no asyncio requirement on callers), bounded worker pool.
- Results are retained in memory with a TTL and a hard entry cap, mirroring
  the schema cache philosophy: a long-lived server must not grow unbounded.
- Cancellation is cooperative: pending tasks are dropped before they start;
  running tasks finish but their results are marked cancelled.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

DEFAULT_ASYNC_MAX_WORKERS = 2
DEFAULT_ASYNC_MAX_TASKS = 50
DEFAULT_ASYNC_RESULT_TTL_SECONDS = 60 * 60

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, value)


@dataclass
class TaskRecord:
    """State of one submitted background task."""

    task_id: str
    name: str
    status: str = STATUS_PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    future: Optional[Future[Any]] = None

    def snapshot(self, include_result: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }
        if include_result and self.status == STATUS_SUCCEEDED:
            payload["result"] = self.result
        return payload


class AsyncTaskManager:
    """Thread-pool task manager with TTL'd, size-bounded result retention."""

    def __init__(
        self,
        max_workers: Optional[int] = None,
        max_tasks: Optional[int] = None,
        result_ttl_seconds: Optional[float] = None,
    ) -> None:
        self.max_workers = max_workers or _int_env(
            "ODOO_MCP_ASYNC_MAX_WORKERS", DEFAULT_ASYNC_MAX_WORKERS
        )
        self.max_tasks = max_tasks or _int_env(
            "ODOO_MCP_ASYNC_MAX_TASKS", DEFAULT_ASYNC_MAX_TASKS
        )
        self.result_ttl_seconds = float(
            result_ttl_seconds
            or _int_env("ODOO_MCP_ASYNC_RESULT_TTL", DEFAULT_ASYNC_RESULT_TTL_SECONDS)
        )
        self._executor: Optional[ThreadPoolExecutor] = None
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers, thread_name_prefix="odoo-mcp-task"
            )
        return self._executor

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [
            task_id
            for task_id, record in self._tasks.items()
            if record.finished_at is not None
            and now - record.finished_at > self.result_ttl_seconds
        ]
        for task_id in expired:
            del self._tasks[task_id]
        # Evict oldest finished tasks beyond the cap; never evict live tasks.
        while len(self._tasks) > self.max_tasks:
            finished = [
                (record.finished_at or record.created_at, task_id)
                for task_id, record in self._tasks.items()
                if record.status
                in (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED)
            ]
            if not finished:
                break
            finished.sort()
            del self._tasks[finished[0][1]]

    def submit(
        self, name: str, fn: Callable[[], Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Submit a callable; returns the task descriptor or a refusal."""
        with self._lock:
            self._purge_locked()
            live = sum(
                1
                for record in self._tasks.values()
                if record.status in (STATUS_PENDING, STATUS_RUNNING)
            )
            if live >= self.max_tasks:
                return {
                    "success": False,
                    "error": (
                        f"Too many live tasks ({live}); wait for completion or "
                        "cancel pending tasks first."
                    ),
                }
            task_id = uuid.uuid4().hex[:12]
            record = TaskRecord(task_id=task_id, name=name)
            self._tasks[task_id] = record

        def _run() -> None:
            with self._lock:
                if record.status == STATUS_CANCELLED:
                    return
                record.status = STATUS_RUNNING
                record.started_at = time.time()
            try:
                result = fn()
                with self._lock:
                    if record.status != STATUS_CANCELLED:
                        record.status = STATUS_SUCCEEDED
                        record.result = result
            except Exception as exc:  # noqa: BLE001 - surfaced to the poller
                with self._lock:
                    if record.status != STATUS_CANCELLED:
                        record.status = STATUS_FAILED
                        record.error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    record.finished_at = time.time()

        record.future = self._ensure_executor().submit(_run)
        return {"success": True, **record.snapshot(include_result=False)}

    def status(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            self._purge_locked()
            record = self._tasks.get(task_id)
            if record is None:
                return {
                    "success": False,
                    "error": f"Unknown or expired task_id: {task_id}",
                }
            return {"success": True, **record.snapshot()}

    def cancel(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return {
                    "success": False,
                    "error": f"Unknown or expired task_id: {task_id}",
                }
            if record.status in (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED):
                return {
                    "success": False,
                    "error": f"Task already {record.status}; nothing to cancel.",
                }
            cancelled_before_start = record.status == STATUS_PENDING
            record.status = STATUS_CANCELLED
            record.finished_at = time.time()
        if record.future is not None and cancelled_before_start:
            record.future.cancel()
        return {
            "success": True,
            "task_id": task_id,
            "status": STATUS_CANCELLED,
            "note": (
                "Cancelled before start."
                if cancelled_before_start
                else "Marked cancelled; the running worker finishes but its "
                "result is discarded."
            ),
        }

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._purge_locked()
            records = sorted(
                self._tasks.values(), key=lambda item: item.created_at, reverse=True
            )
            return [record.snapshot(include_result=False) for record in records]


_manager: Optional[AsyncTaskManager] = None
_manager_lock = threading.Lock()


def get_task_manager() -> AsyncTaskManager:
    """Process-wide manager, built lazily so env knobs apply at first use."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = AsyncTaskManager()
        return _manager


def reset_task_manager() -> None:
    """Drop the process manager (intended for tests)."""
    global _manager
    with _manager_lock:
        _manager = None
