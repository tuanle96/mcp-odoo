#!/usr/bin/env python3
"""
Benchmark odoo-mcp tool latency via MCP stdio protocol.

Measures p50 / p95 / mean for the primary read-only tools against a live
Odoo instance.  Connection credentials are read from the same environment
variables used by the server itself:

  ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, ODOO_TRANSPORT (optional)

Usage:
  python scripts/benchmark_tools.py [--iterations N] [--json results.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _env() -> dict[str, str]:
    """Build environment for the MCP server subprocess."""
    required = {"ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"}
    missing = required - set(os.environ)
    if missing:
        raise SystemExit(
            f"Missing environment variables: {', '.join(sorted(missing))}\n"
            "Set ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD before running."
        )
    env = os.environ.copy()
    env.setdefault("ODOO_TRANSPORT", "xmlrpc")
    env.setdefault("ODOO_TIMEOUT", "30")
    env.setdefault("ODOO_VERIFY_SSL", "1")
    # Ensure the installed package (or source) is on the path
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _parse_result(raw: Any) -> dict[str, Any]:
    content = raw.content[0] if raw.content else None
    if isinstance(content, TextContent):
        try:
            return json.loads(content.text)
        except json.JSONDecodeError:
            return {"raw": content.text}
    return {}


def _stats(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"p50": 0.0, "p95": 0.0, "mean": 0.0, "n": 0}
    sorted_s = sorted(samples_ms)
    n = len(sorted_s)
    p50 = sorted_s[int(n * 0.50)]
    p95 = sorted_s[min(int(n * 0.95), n - 1)]
    mean = statistics.mean(sorted_s)
    return {"p50": round(p50, 1), "p95": round(p95, 1), "mean": round(mean, 1), "n": n}


# ---------------------------------------------------------------------------
# benchmark scenarios
# ---------------------------------------------------------------------------


async def _run_benchmark(
    session: ClientSession, iterations: int, record_id: int
) -> dict[str, dict[str, float]]:
    """Execute each tool scenario N times and return latency stats."""

    async def measure(tool: str, args: dict[str, Any]) -> list[float]:
        samples: list[float] = []
        # warmup: 1 call, discarded
        await session.call_tool(tool, arguments=args)
        for _ in range(iterations):
            t0 = time.perf_counter()
            await session.call_tool(tool, arguments=args)
            samples.append((time.perf_counter() - t0) * 1000)
        return samples

    results: dict[str, dict[str, float]] = {}

    # 1. search_records (simple domain)
    samples = await measure(
        "search_records",
        {"model": "res.partner", "domain": [["id", ">", 0]], "limit": 10},
    )
    results["search_records"] = _stats(samples)

    # 2. read_record
    samples = await measure(
        "read_record",
        {"model": "res.partner", "record_id": record_id},
    )
    results["read_record"] = _stats(samples)

    # 3. get_model_fields — cold cache (first call hits server)
    # We probe a secondary model to avoid the warm-cache path from step 1/2
    samples = await measure(
        "get_model_fields",
        {"model": "res.users"},
    )
    results["get_model_fields_warm"] = _stats(samples)

    # 4. get_model_fields — re-run on same model so schema cache warms
    samples = await measure(
        "get_model_fields",
        {"model": "res.partner"},
    )
    results["get_model_fields_partner_warm"] = _stats(samples)

    # 5. aggregate_records
    samples = await measure(
        "aggregate_records",
        {
            "model": "res.partner",
            "group_by": ["is_company"],
            "measures": ["id:count"],
        },
    )
    results["aggregate_records"] = _stats(samples)

    # 6. list_models
    samples = await measure(
        "list_models",
        {"limit": 50},
    )
    results["list_models"] = _stats(samples)

    # 7. diagnose_access
    samples = await measure(
        "diagnose_access",
        {"model": "res.partner", "operation": "read", "expected_count": 1},
    )
    results["diagnose_access"] = _stats(samples)

    return results


# ---------------------------------------------------------------------------
# output formatting
# ---------------------------------------------------------------------------

COLUMN_WIDTHS = (35, 8, 8, 8, 6)
HEADERS = ("Tool", "p50 ms", "p95 ms", "mean ms", "n")


def _print_table(results: dict[str, dict[str, float]]) -> None:
    def row(*cols: Any) -> str:
        parts = [str(c).ljust(w) for c, w in zip(cols, COLUMN_WIDTHS)]
        return "  ".join(parts)

    sep = "  ".join("-" * w for w in COLUMN_WIDTHS)
    print()
    print(row(*HEADERS))
    print(sep)
    for tool, stats in results.items():
        print(row(tool, stats["p50"], stats["p95"], stats["mean"], stats["n"]))
    print()


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


async def _main(iterations: int, json_out: str | None) -> None:
    env = _env()
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "odoo_mcp"],
        env=env,
    )

    print(f"Connecting to Odoo at {env['ODOO_URL']} / db={env['ODOO_DB']} ...")
    print(f"Transport: {env.get('ODOO_TRANSPORT', 'xmlrpc')}")
    print(f"Iterations per tool: {iterations} (plus 1 warmup)")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Discover a valid record_id to use in read_record
            probe = await session.call_tool(
                "search_records",
                arguments={
                    "model": "res.partner",
                    "domain": [["id", ">", 0]],
                    "fields": ["id"],
                    "limit": 1,
                },
            )
            probe_data = _parse_result(probe)
            records = probe_data.get("result", [])
            if not records:
                raise SystemExit("No res.partner records found — is the DB initialised?")
            record_id: int = records[0]["id"]
            print(f"Using res.partner record_id={record_id} for read_record benchmark.")

            print("\nRunning benchmark ...\n")
            results = await _run_benchmark(session, iterations, record_id)

    _print_table(results)

    # Summary metadata
    output = {
        "meta": {
            "odoo_url": env["ODOO_URL"],
            "odoo_db": env["ODOO_DB"],
            "transport": env.get("ODOO_TRANSPORT", "xmlrpc"),
            "iterations": iterations,
            "record_id": record_id,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "results": results,
    }

    if json_out:
        out_path = Path(json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2))
        print(f"JSON results written to: {out_path}")

    # Print JSON to stdout regardless so callers can capture it
    print(json.dumps(output, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark odoo-mcp tool latency against a live Odoo instance."
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=20,
        metavar="N",
        help="Number of timed calls per tool after warmup (default: 20).",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        metavar="PATH",
        help="Write full results as JSON to this file path.",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.iterations, args.json_out))


if __name__ == "__main__":
    main()
