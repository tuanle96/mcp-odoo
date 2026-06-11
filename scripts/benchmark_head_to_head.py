#!/usr/bin/env python3
"""
Head-to-head latency benchmark: mcp-odoo vs mcp-server-odoo (ivnvxd).

Tests both servers against the SAME live Odoo instance, same operations, same
dataset.  Output: aligned table + optional JSON (--json).

Environment: ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD

If the competitor server cannot start, script continues with mcp-odoo-only
results and prints a WARNING.  No numbers are fabricated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]

# Operations for mcp-odoo (op_name, tool, args)
OUR_OPS: list[tuple[str, str, dict[str, Any]]] = [
    ("list_models",      "list_models",      {"limit": 50}),
    ("search_records",   "search_records",   {"model": "res.partner", "domain": [["id", ">", 0]], "limit": 10}),
    ("read_record",      "read_record",      {"model": "res.partner", "record_id": 0}),  # patched at runtime
    ("aggregate_records","aggregate_records",{"model": "res.partner", "group_by": ["is_company"], "measures": ["id:count"]}),
    ("get_model_fields", "get_model_fields", {"model": "res.partner"}),
]

# Equivalent operations for mcp-server-odoo v0.6.0 (ivnvxd)
# Confirmed tool surface: search_records, get_record, list_models, aggregate_records,
#   create_record, update_record, delete_record, post_message, list_resource_templates
# No get_model_fields equivalent.
THEIR_OPS: list[tuple[str, str | None, dict[str, Any]]] = [
    ("list_models",      "list_models",      {}),
    ("search_records",   "search_records",   {"model": "res.partner", "domain": [["id", ">", 0]], "limit": 10}),
    ("read_record",      "get_record",       {"model": "res.partner", "record_id": 0}),  # patched
    ("aggregate_records","aggregate_records",{"model": "res.partner", "groupby": ["is_company"], "aggregates": ["id:count"]}),
    ("get_model_fields", None,               {}),  # not exposed
]


def _env_our() -> dict[str, str]:
    required = {"ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"}
    missing = required - set(os.environ)
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(sorted(missing))}")
    env = os.environ.copy()
    env.setdefault("ODOO_TRANSPORT", "xmlrpc")
    env.setdefault("ODOO_TIMEOUT", "30")
    env.setdefault("ODOO_VERIFY_SSL", "1")
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _env_their() -> dict[str, str]:
    # mcp-server-odoo uses ODOO_USER (not ODOO_USERNAME)
    # ODOO_YOLO=read: vanilla XML-RPC access without requiring Odoo-side MCP module
    env = os.environ.copy()
    env["ODOO_USER"] = os.environ.get("ODOO_USERNAME", "admin")
    env.setdefault("ODOO_YOLO", "read")
    return env


def _parse_text(raw: Any) -> dict[str, Any]:
    content = raw.content[0] if raw.content else None
    if isinstance(content, TextContent):
        try:
            return json.loads(content.text)
        except (json.JSONDecodeError, TypeError):
            return {"raw": content.text}
    return {}


def _stats(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "n": 0}
    s = sorted(samples_ms)
    n = len(s)
    return {
        "p50":  round(s[int(n * 0.50)], 1),
        "p95":  round(s[min(int(n * 0.95), n - 1)], 1),
        "p99":  round(s[min(int(n * 0.99), n - 1)], 1),
        "mean": round(statistics.mean(s), 1),
        "n":    n,
    }


async def _measure(session: ClientSession, tool: str, args: dict[str, Any], n: int) -> list[float]:
    try:
        await session.call_tool(tool, arguments=args)  # warmup
    except Exception:
        pass
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            await session.call_tool(tool, arguments=args)
        except Exception:
            pass
        samples.append((time.perf_counter() - t0) * 1000)
    return samples


async def bench_our(iterations: int) -> tuple[dict[str, Any], int]:
    env = _env_our()
    params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_mcp"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            probe = await s.call_tool("search_records",
                                      arguments={"model": "res.partner", "domain": [["id", ">", 0]],
                                                 "fields": ["id"], "limit": 1})
            records = _parse_text(probe).get("result", [])
            record_id: int = records[0]["id"] if records else 1
            results: dict[str, Any] = {}
            for op_name, tool, args in OUR_OPS:
                a = {**args, "record_id": record_id} if tool == "read_record" else args
                results[op_name] = _stats(await _measure(s, tool, a, iterations))
    return results, record_id


def _their_cmd() -> list[str] | None:
    uvx = shutil.which("uvx")
    if uvx:
        return [uvx, "mcp-server-odoo"]
    cmd = shutil.which("mcp-server-odoo")
    return [cmd] if cmd else None


async def bench_their(iterations: int, record_id: int) -> dict[str, Any]:
    cmd = _their_cmd()
    if cmd is None:
        print("WARNING: mcp-server-odoo not found (uvx/PATH). Skipping.", file=sys.stderr)
        return {"_status": "not_installed"}
    env = _env_their()
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
    results: dict[str, Any] = {}
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                available = {t.name for t in (await s.list_tools()).tools}
                for op_name, tool, args in THEIR_OPS:
                    if tool is None or tool not in available:
                        results[op_name] = "N/A — tool not exposed"
                        continue
                    a = {**args, "record_id": record_id} if tool == "get_record" else args
                    results[op_name] = _stats(await _measure(s, tool, a, iterations))
    except Exception as exc:
        print(f"WARNING: competitor server error: {exc}", file=sys.stderr)
        results["_status"] = f"error: {exc}"
    return results


async def cold_starts() -> dict[str, float]:
    out: dict[str, float] = {}
    our_params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_mcp"], env=_env_our())
    try:
        t0 = time.perf_counter()
        async with stdio_client(our_params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await s.call_tool("list_models", arguments={"limit": 10})
        out["mcp_odoo_cold_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    except Exception as exc:
        out["mcp_odoo_cold_ms"] = -1.0
        print(f"WARNING: mcp-odoo cold-start failed: {exc}", file=sys.stderr)

    cmd = _their_cmd()
    if cmd:
        their_params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=_env_their())
        try:
            t0 = time.perf_counter()
            async with stdio_client(their_params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    await s.call_tool("list_models", arguments={})
            out["mcp_server_odoo_cold_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as exc:
            out["mcp_server_odoo_cold_ms"] = -1.0
            print(f"WARNING: competitor cold-start failed: {exc}", file=sys.stderr)
    else:
        out["mcp_server_odoo_cold_ms"] = -1.0
    return out


COLS = (28, 10, 10, 10, 10, 8)
HDR  = ("Operation", "p50 ms", "p95 ms", "p99 ms", "mean ms", "n")


def _row(*cells: Any) -> str:
    return "  ".join(str(c).ljust(w) for c, w in zip(cells, COLS))


def _print_table(label: str, results: dict[str, Any]) -> None:
    status = results.get("_status")
    if status:
        print(f"\n  {label}: {status}")
        return
    print(f"\n  {label}")
    print("  " + _row(*HDR))
    print("  " + "  ".join("-" * w for w in COLS))
    for op, data in results.items():
        if op.startswith("_"):
            continue
        if isinstance(data, str):
            print("  " + _row(op, data, "", "", "", ""))
        else:
            print("  " + _row(op, data["p50"], data["p95"], data["p99"], data["mean"], data["n"]))


def _print_ratio(our: dict[str, Any], their: dict[str, Any]) -> None:
    if their.get("_status"):
        return
    print("\n  p50 ratio (mcp-odoo / mcp-server-odoo)  <1 = mcp-odoo faster")
    print("  " + "-" * 60)
    for op, _, _ in OUR_OPS:
        ov, tv = our.get(op), their.get(op)
        if isinstance(ov, dict) and isinstance(tv, dict) and tv["p50"]:
            ratio = ov["p50"] / tv["p50"]
            tag = "faster" if ratio < 1.0 else ("slower" if ratio > 1.0 else "equal")
            print(f"  {op:<28}  {ratio:.2f}x  ({tag})")
        else:
            print(f"  {op:<28}  N/A (competitor lacks tool)")


async def _main(iterations: int, json_out: str | None, skip_cold: bool) -> None:
    missing = {"ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"} - set(os.environ)
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(sorted(missing))}")
    odoo_url, odoo_db = os.environ["ODOO_URL"], os.environ["ODOO_DB"]
    print(f"Odoo: {odoo_url}  db={odoo_db}")
    print(f"Iterations per op: {iterations} (+ 1 warmup each)")

    print("\n--- mcp-odoo ---")
    our, record_id = await bench_our(iterations)
    print(f"    record_id={record_id}")

    print("\n--- mcp-server-odoo v0.6.0 (ivnvxd, ODOO_YOLO=read) ---")
    their = await bench_their(iterations, record_id)

    cold: dict[str, float] = {}
    if not skip_cold:
        print("\n--- cold-start (process spawn → first response) ---")
        cold = await cold_starts()

    print("\n" + "=" * 72)
    print("  HEAD-TO-HEAD RESULTS")
    print("=" * 72)
    _print_table("mcp-odoo (this project)", our)
    _print_table("mcp-server-odoo v0.6.0 (ivnvxd)", their)
    _print_ratio(our, their)

    if cold:
        print("\n  Cold-start ms")
        print("  " + "-" * 40)
        for k, v in cold.items():
            print(f"  {k.replace('_cold_ms', ''):<32} {v:.1f} ms" if v >= 0 else f"  {k:<32} failed")

    if their.get("_status", "ok") != "ok":
        print(f"\nNOTE: Competitor incomplete — {their['_status']}")
        print("      Issue #68: https://github.com/ivnvxd/mcp-server-odoo/issues/68")
        print("      Issue #70: https://github.com/ivnvxd/mcp-server-odoo/issues/70")

    output = {
        "meta": {
            "odoo_url": odoo_url, "odoo_db": odoo_db,
            "transport_our": os.environ.get("ODOO_TRANSPORT", "xmlrpc"),
            "iterations": iterations, "record_id": record_id,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "competitor_version": "v0.6.0",
            "competitor_issue_68": "https://github.com/ivnvxd/mcp-server-odoo/issues/68",
            "competitor_issue_70": "https://github.com/ivnvxd/mcp-server-odoo/issues/70",
        },
        "mcp_odoo": our,
        "mcp_server_odoo": their,
        "cold_starts_ms": cold,
    }
    if json_out:
        out_path = Path(json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2))
        print(f"\nJSON written to: {out_path}")
    print(json.dumps(output, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description="Head-to-head: mcp-odoo vs mcp-server-odoo.")
    p.add_argument("--iterations", type=int, default=15, metavar="N",
                   help="Timed calls per op after warmup (default: 15).")
    p.add_argument("--json", dest="json_out", metavar="PATH", help="Write JSON results here.")
    p.add_argument("--skip-cold", action="store_true", help="Skip cold-start measurement.")
    args = p.parse_args()
    asyncio.run(_main(args.iterations, args.json_out, args.skip_cold))


if __name__ == "__main__":
    main()
