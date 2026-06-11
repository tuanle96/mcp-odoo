from odoo_mcp import rate_limit
from odoo_mcp.rate_limit import (
    SlidingWindowRateTracker,
    check_rate,
    rate_limit_mode,
    rate_report,
    reset_rate_tracker,
)


def test_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("ODOO_MCP_RATE_LIMIT_MODE", raising=False)
    assert rate_limit_mode() == "off"


def test_mode_invalid_falls_back_to_off(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "aggressive")
    assert rate_limit_mode() == "off"


def test_mode_parsing(monkeypatch):
    for value in ("warn", "BLOCK", " off "):
        monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", value)
        assert rate_limit_mode() == value.strip().lower()


def test_tracker_counts_within_window():
    tracker = SlidingWindowRateTracker(window_seconds=60, max_calls=3)
    for expected in (1, 2, 3):
        verdict = tracker.record("default", "search_records")
        assert verdict["over_budget"] is False
        assert verdict["calls_in_window"] == expected
    verdict = tracker.record("default", "search_records")
    assert verdict["over_budget"] is True
    assert verdict["calls_in_window"] == 3


def test_tracker_keys_are_isolated():
    tracker = SlidingWindowRateTracker(window_seconds=60, max_calls=1)
    assert tracker.record("a", "tool")["over_budget"] is False
    assert tracker.record("b", "tool")["over_budget"] is False
    assert tracker.record("a", "other")["over_budget"] is False
    assert tracker.record("a", "tool")["over_budget"] is True


def test_window_expiry(monkeypatch):
    now = {"value": 1000.0}
    monkeypatch.setattr("odoo_mcp.rate_limit.time.time", lambda: now["value"])
    tracker = SlidingWindowRateTracker(window_seconds=10, max_calls=1)
    assert tracker.record("i", "t")["over_budget"] is False
    assert tracker.record("i", "t")["over_budget"] is True
    now["value"] += 11
    assert tracker.record("i", "t")["over_budget"] is False


def test_env_knob_parsing(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_WINDOW", "junk")
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MAX_CALLS", "-5")
    tracker = SlidingWindowRateTracker()
    assert tracker.window_seconds == rate_limit.DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    assert tracker.max_calls == 1


def test_check_rate_off_mode_skips_tracking(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "off")
    reset_rate_tracker()
    assert check_rate("default", "search_records") is None
    assert rate_report() == {"mode": "off"}


def test_check_rate_warn_mode_never_blocks(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "warn")
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MAX_CALLS", "1")
    reset_rate_tracker()
    try:
        assert check_rate("default", "t") is None
        assert check_rate("default", "t") is None
        report = rate_report()
        assert report["mode"] == "warn"
        assert report["over_budget_totals"].get("default:t", 0) >= 1
    finally:
        reset_rate_tracker()


def test_warn_mode_counts_executed_over_budget_calls(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "warn")
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MAX_CALLS", "1")
    reset_rate_tracker()
    try:
        for _ in range(3):
            assert check_rate("default", "t") is None
        report = rate_report()
        # warn mode executes every call, so the window reflects real volume
        assert report["busiest"][0]["calls_in_window"] == 3
        assert report["over_budget_totals"]["default:t"] == 2
    finally:
        reset_rate_tracker()


def test_check_rate_block_mode_refuses(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "block")
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MAX_CALLS", "2")
    reset_rate_tracker()
    try:
        assert check_rate("default", "t") is None
        assert check_rate("default", "t") is None
        refusal = check_rate("default", "t")
        assert refusal is not None
        assert refusal["success"] is False
        assert refusal["rate_limited"] is True
        assert "Rate limit exceeded" in refusal["error"]
    finally:
        reset_rate_tracker()


def test_report_lists_busiest_keys(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "warn")
    reset_rate_tracker()
    try:
        for _ in range(3):
            check_rate("default", "hot")
        check_rate("default", "cold")
        report = rate_report()
        busiest = report["busiest"]
        assert busiest[0]["key"] == "default:hot"
        assert busiest[0]["calls_in_window"] == 3
    finally:
        reset_rate_tracker()


def test_tracked_key_cap():
    tracker = SlidingWindowRateTracker(window_seconds=600, max_calls=100)
    for index in range(rate_limit.MAX_TRACKED_KEYS + 10):
        tracker.record("inst", f"tool{index}")
    assert len(tracker._events) <= rate_limit.MAX_TRACKED_KEYS
