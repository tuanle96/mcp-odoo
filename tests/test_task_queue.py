import threading
import time

from odoo_mcp.task_queue import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    AsyncTaskManager,
)


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_submit_and_succeed():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)
    submitted = manager.submit("demo", lambda: {"answer": 42})
    assert submitted["success"] is True
    task_id = submitted["task_id"]
    assert wait_for(
        lambda: manager.status(task_id)["status"] == STATUS_SUCCEEDED
    )
    status = manager.status(task_id)
    assert status["result"] == {"answer": 42}
    assert status["error"] is None


def test_failure_captures_error_type():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)

    def boom():
        raise ValueError("nope")

    task_id = manager.submit("boom", boom)["task_id"]
    assert wait_for(lambda: manager.status(task_id)["status"] == STATUS_FAILED)
    status = manager.status(task_id)
    assert "ValueError: nope" in status["error"]
    assert "result" not in status


def test_unknown_task_id():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)
    assert manager.status("missing")["success"] is False
    assert manager.cancel("missing")["success"] is False


def test_cancel_pending_task_never_runs():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)
    release = threading.Event()
    ran = []

    def blocker():
        release.wait(5)
        return {}

    def tracked():
        ran.append(True)
        return {}

    blocker_id = manager.submit("blocker", blocker)["task_id"]
    pending_id = manager.submit("pending", tracked)["task_id"]
    cancelled = manager.cancel(pending_id)
    assert cancelled["success"] is True
    release.set()
    assert wait_for(
        lambda: manager.status(blocker_id)["status"] == STATUS_SUCCEEDED
    )
    assert manager.status(pending_id)["status"] == STATUS_CANCELLED
    assert ran == []


def test_cancel_running_task_discards_result():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return {"late": True}

    task_id = manager.submit("slow", slow)["task_id"]
    assert started.wait(5)
    assert manager.cancel(task_id)["success"] is True
    release.set()
    status = manager.status(task_id)
    assert status["status"] == STATUS_CANCELLED
    assert wait_for(
        lambda: "result" not in manager.status(task_id)
    )


def test_double_cancel_refused():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)
    release = threading.Event()
    blocker_id = manager.submit("blocker", lambda: release.wait(5) or {})["task_id"]
    pending_id = manager.submit("pending", lambda: {})["task_id"]
    assert manager.cancel(pending_id)["success"] is True
    assert manager.cancel(pending_id)["success"] is False
    release.set()
    assert wait_for(
        lambda: manager.status(blocker_id)["status"] == STATUS_SUCCEEDED
    )


def test_live_task_cap_refuses_submission():
    manager = AsyncTaskManager(max_workers=1, max_tasks=2)
    release = threading.Event()
    first = manager.submit("a", lambda: release.wait(5) or {})
    second = manager.submit("b", lambda: release.wait(5) or {})
    assert first["success"] and second["success"]
    third = manager.submit("c", lambda: {})
    assert third["success"] is False
    assert "Too many live tasks" in third["error"]
    release.set()


def test_result_ttl_purges_finished_tasks(monkeypatch):
    manager = AsyncTaskManager(max_workers=1, max_tasks=10, result_ttl_seconds=60)
    task_id = manager.submit("demo", lambda: {"ok": True})["task_id"]
    assert wait_for(
        lambda: manager.status(task_id)["status"] == STATUS_SUCCEEDED
    )
    finished_at = manager.status(task_id)["finished_at"]
    monkeypatch.setattr(
        "odoo_mcp.task_queue.time.time", lambda: finished_at + 61
    )
    assert manager.status(task_id)["success"] is False


def test_list_tasks_newest_first():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)
    first = manager.submit("first", lambda: {})["task_id"]
    second = manager.submit("second", lambda: {})["task_id"]
    assert wait_for(
        lambda: all(
            manager.status(t)["status"] == STATUS_SUCCEEDED for t in (first, second)
        )
    )
    listed = manager.list_tasks()
    assert [item["name"] for item in listed][:2] == ["second", "first"]
    assert all("result" not in item for item in listed)


def test_env_knobs(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_ASYNC_MAX_WORKERS", "7")
    monkeypatch.setenv("ODOO_MCP_ASYNC_MAX_TASKS", "junk")
    manager = AsyncTaskManager()
    assert manager.max_workers == 7
    assert manager.max_tasks == 50


def test_pending_status_shape():
    manager = AsyncTaskManager(max_workers=1, max_tasks=10)
    release = threading.Event()
    manager.submit("blocker", lambda: release.wait(5) or {})
    pending = manager.submit("queued", lambda: {})
    assert pending["status"] in (STATUS_PENDING,)
    release.set()
