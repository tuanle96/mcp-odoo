#!/usr/bin/env python3
"""Talk to an odoo-mcp server running in request identity mode, as one or two users.

Plain ``curl`` cannot hold an MCP session, so this small client does the
initialize → call_tool dance over Streamable HTTP and sends the identity
headers LibreChat would send:

    X-User-Email, X-Odoo-Api-Key, X-LibreChat-User-Id (optional)

Credentials come from environment variables or an interactive prompt — never
from command-line arguments, never from committed files, and they are never
printed.

Examples::

    # server posture only (no identity needed)
    uv run python scripts/identity_client.py --health

    # one user: key read from ODOO_MCP_USER_API_KEY or prompted
    ODOO_MCP_USER_EMAIL=anna@example.ch \\
        uv run python scripts/identity_client.py --model res.partner

    # two users, same tool, same model, side by side
    A_EMAIL=anna@example.ch A_API_KEY=... B_EMAIL=bob@example.ch B_API_KEY=... \\
        uv run python scripts/identity_client.py --compare --model res.partner

    # prove the server fails closed without an identity
    uv run python scripts/identity_client.py --no-identity --model res.partner
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8010/mcp")


@dataclass(frozen=True)
class Who:
    label: str
    email: str
    api_key: str
    user_id: str | None = None

    def headers(self) -> dict[str, str]:
        headers = {"X-User-Email": self.email, "X-Odoo-Api-Key": self.api_key}
        if self.user_id:
            headers["X-LibreChat-User-Id"] = self.user_id
        return headers


def load_identity(prefix: str, label: str) -> Who:
    """Read ``{prefix}_EMAIL`` / ``{prefix}_API_KEY`` (prompting for a missing key)."""
    email = os.environ.get(f"{prefix}_EMAIL", "").strip()
    if not email:
        if not sys.stdin.isatty():
            raise SystemExit(f"{prefix}_EMAIL is not set")
        email = input(f"{label} Odoo login (email): ").strip()
    key = os.environ.get(f"{prefix}_API_KEY", "").strip()
    if not key:
        if not sys.stdin.isatty():
            raise SystemExit(f"{prefix}_API_KEY is not set")
        key = getpass.getpass(f"{label} Odoo API key for {email} (hidden): ").strip()
    return Who(label, email, key, os.environ.get(f"{prefix}_USER_ID") or None)


def _decode(result: Any) -> Any:
    """Turn a CallToolResult into the tool's JSON envelope."""
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if isinstance(structured, dict):
        # The SDK wraps non-object returns as {"result": ...}; tool envelopes are
        # objects of their own (and may legitimately carry a null "result").
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
    return {"raw": str(result)}


async def call_tool(
    url: str, headers: dict[str, str] | None, tool: str, arguments: dict[str, Any]
) -> Any:
    async with httpx2.AsyncClient(headers=headers or {}, timeout=120) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if tool == "__list__":
                    tools = await session.list_tools()
                    return sorted(t.name for t in tools.tools)
                return _decode(await session.call_tool(tool, arguments=arguments))


def summarize(label: str, payload: Any) -> None:
    if isinstance(payload, list):
        print(f"[{label}] {len(payload)} tools: {', '.join(payload)}")
        return
    if not isinstance(payload, dict):
        print(f"[{label}] {payload}")
        return
    if payload.get("success") is False:
        print(f"[{label}] REFUSED: {payload.get('error')}")
        return
    rows = payload.get("result")
    if isinstance(rows, list):
        names = [str(r.get("name", r.get("id"))) for r in rows if isinstance(r, dict)]
        print(f"[{label}] {payload.get('count', len(rows))} record(s): {names}")
        redacted = payload.get("redacted_fields")
        if redacted:
            print(f"[{label}] redacted by AI data policy: {redacted}")
        return
    print(f"[{label}] {json.dumps(payload, indent=2, default=str)[:2000]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP endpoint (default {DEFAULT_URL})")
    parser.add_argument("--tool", default="search_records")
    parser.add_argument("--model", default="res.partner")
    parser.add_argument("--fields", default="name", help="comma-separated field list")
    parser.add_argument("--domain", default=None, help="JSON search domain")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--args", default=None, help="raw JSON arguments (overrides model/fields/...)")
    parser.add_argument("--health", action="store_true", help="call health_check and print the identity posture")
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--compare", action="store_true", help="run as user A and user B (A_*/B_* env vars)")
    parser.add_argument("--no-identity", action="store_true", help="send no identity headers (expect refusal)")
    parser.add_argument("--json", action="store_true", help="print the raw JSON envelope")
    args = parser.parse_args(argv)

    if args.args:
        arguments = json.loads(args.args)
    else:
        arguments = {"model": args.model, "fields": [f for f in args.fields.split(",") if f], "limit": args.limit}
        if args.domain:
            arguments["domain"] = json.loads(args.domain)
        if args.instance:
            arguments["instance"] = args.instance

    if args.health:
        payload = asyncio.run(call_tool(args.url, None, "health_check", {}))
        posture = payload.get("identity") if isinstance(payload, dict) else None
        print(json.dumps(posture if posture and not args.json else payload, indent=2, default=str))
        return 0 if posture and posture.get("ok") else 1

    if args.list_tools:
        summarize("tools", asyncio.run(call_tool(args.url, None, "__list__", {})))
        return 0

    if args.no_identity:
        payload = asyncio.run(call_tool(args.url, None, args.tool, arguments))
        summarize("no identity", payload)
        refused = isinstance(payload, dict) and payload.get("success") is False
        print("fail-closed:", "OK" if refused else "BROKEN - the server answered without an identity!")
        return 0 if refused else 2

    users = (
        [load_identity("A", "User A"), load_identity("B", "User B")]
        if args.compare
        else [load_identity("ODOO_MCP_USER", "User")]
    )
    exit_code = 0
    for who in users:
        payload = asyncio.run(call_tool(args.url, who.headers(), args.tool, arguments))
        label = f"{who.label} {who.email}"
        if args.json:
            print(f"[{label}]", json.dumps(payload, indent=2, default=str))
        else:
            summarize(label, payload)
        if isinstance(payload, dict) and payload.get("success") is False:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
