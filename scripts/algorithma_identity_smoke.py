#!/usr/bin/env python3
"""Two-user access-control smoke for request identity mode (Algorithma fork).

Boots a disposable Odoo (Docker Compose, same harness as
``odoo_compose_smoke.py``), creates two restricted internal users with their
own API keys and a record rule that lets each of them see only "their"
partners, then starts odoo-mcp in ``ODOO_MCP_IDENTITY_MODE=request`` over
Streamable HTTP and proves, through the MCP tools:

    same MCP + same tool + same model + different credentials
        -> different Odoo-visible records

plus: no identity -> refused; wrong key -> sanitized refusal; an approval
validated by A cannot be executed by B; ``execute_method`` keeps its
side-effect guards; the audit log carries the principal but never a key.

    uv run --python 3.12 --with-editable . scripts/algorithma_identity_smoke.py
    # options: --version 18.0 --port 18369 --mcp-port 19369 --timeout 360 --keep-stack
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
import odoo_compose_smoke as harness  # noqa: E402  (sibling script, no package)

ROOT = Path(__file__).resolve().parents[1]
RULE_NAME = "Algorithma identity smoke: partners by salesperson"
USER_A = {"login": "anna.smoke@algorithma.test", "name": "Anna Smoke"}
USER_B = {"login": "bob.smoke@algorithma.test", "name": "Bob Smoke"}
PARTNERS = {"Alpha Kunde": "A", "Beta Kunde": "B", "Gamma Kunde": None}


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


# --- Odoo fixture (users, keys, rule, partners) ------------------------------


def create_fixture(target: harness.VersionTarget, env: dict[str, str]) -> dict[str, Any]:
    script = f"""
from datetime import datetime, timedelta
import json
Users = env["res.users"].sudo()
Partners = env["res.partner"].sudo()
Rules = env["ir.rule"].sudo()
group = env.ref("base.group_user")
company = env.ref("base.main_company")
group_field = "groups_id" if "groups_id" in Users._fields else "group_ids"
out = {{}}
for tag, spec in (("A", {json.dumps(USER_A)}), ("B", {json.dumps(USER_B)})):
    user = Users.search([("login", "=", spec["login"])], limit=1)
    vals = {{
        "name": spec["name"], "login": spec["login"], "password": "unused-" + tag,
        "active": True, "company_id": company.id,
        "company_ids": [(6, 0, [company.id])], group_field: [(6, 0, [group.id])],
    }}
    user = user.write(vals) and user if user else Users.create(vals)
    key = env["res.users.apikeys"].sudo().with_user(user)._generate(
        "rpc", "algorithma identity smoke", datetime.now() + timedelta(days=1)
    )
    out[tag] = {{"uid": user.id, "login": spec["login"], "key": key}}
model = env["ir.model"].sudo()._get("res.partner")
rule = Rules.search([("name", "=", {json.dumps(RULE_NAME)})], limit=1)
rule_vals = {{
    "name": {json.dumps(RULE_NAME)}, "model_id": model.id,
    "domain_force": "[('user_id', '=', user.id)]",
    "groups": [(6, 0, [group.id])],
    "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": True,
}}
rule = rule.write(rule_vals) and rule if rule else Rules.create(rule_vals)
partners = {{}}
for name, owner in {json.dumps(PARTNERS)}.items():
    existing = Partners.search([("name", "=", name)], limit=1)
    vals = {{"name": name, "user_id": out[owner]["uid"] if owner else False}}
    record = existing.write(vals) and existing if existing else Partners.create(vals)
    partners[name] = record.id
out["partners"] = partners
env.cr.commit()
print("FIXTURE=" + json.dumps(out))
"""
    completed = harness.run(
        harness.compose_cmd(
            target, "run", "--rm", "-T", "odoo", "odoo", "shell", "-d", target.database,
            "--db_host=db", "--db_port=5432", "--db_user", harness.DB_USER,
            "--db_password", harness.DB_PASSWORD,
        ),
        env=env,
        timeout=300,
        input_text=script,
        capture_output=True,
    )
    for line in completed.stdout.splitlines():
        if line.startswith("FIXTURE="):
            return json.loads(line.removeprefix("FIXTURE="))
    raise AssertionError("fixture creation did not print FIXTURE=<json>")


# --- MCP client side -----------------------------------------------------------


def identity_headers(user: dict[str, Any] | None, *, key: str | None = None) -> dict[str, str]:
    if user is None:
        return {}
    return {
        "X-User-Email": user["login"],
        "X-Odoo-Api-Key": key if key is not None else user["key"],
        "X-LibreChat-User-Id": "lc-" + user["login"].split("@")[0],
    }


async def call(url: str, headers: dict[str, str], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx2.AsyncClient(headers=headers, timeout=120) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return harness.decode_tool_json(
                    await session.call_tool(tool, arguments=arguments), tool
                )


async def wait_for_mcp(url: str, process: subprocess.Popen[str], timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            async with httpx2.AsyncClient(timeout=10) as http:
                async with streamable_http_client(url, http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=5)
                raise AssertionError(f"MCP server exited early ({process.returncode}): {stderr[-2000:]}")
            await asyncio.sleep(1)
    raise TimeoutError(f"MCP server did not become ready: {last}")


def check(results: dict[str, Any], name: str, condition: bool, detail: Any = None) -> None:
    results[name] = {"ok": bool(condition), "detail": detail}
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


async def exercise(url: str, fixture: dict[str, Any], audit_path: Path, results: dict[str, Any]) -> None:
    anna, bob = fixture["A"], fixture["B"]
    partner_domain = [["name", "ilike", "Kunde"]]
    search = {"model": "res.partner", "domain": partner_domain, "fields": ["name"], "limit": 10}

    # 1. no identity -> refused, and nothing else happened
    refused = await call(url, identity_headers(None), "search_records", search)
    check(results, "no identity fails closed", refused.get("success") is False and "identity" in refused.get("error", "").lower(), refused.get("error"))

    # 2./3. same tool, same model, different users -> different records
    as_a = await call(url, identity_headers(anna), "search_records", search)
    as_b = await call(url, identity_headers(bob), "search_records", search)
    names_a = sorted(r["name"] for r in as_a.get("result", []))
    names_b = sorted(r["name"] for r in as_b.get("result", []))
    check(results, "user A sees only A's records", as_a.get("success") and names_a == ["Alpha Kunde"], names_a)
    check(results, "user B sees only B's records", as_b.get("success") and names_b == ["Beta Kunde"], names_b)

    # 4. wrong key -> sanitized refusal
    wrong = await call(url, identity_headers(anna, key="not-the-key-" + "x" * 30), "search_records", search)
    check(results, "invalid key is refused without echoing it", wrong.get("success") is False and "not-the-key" not in json.dumps(wrong) and "rejected" in wrong.get("error", ""), wrong.get("error"))

    # 5. generic read tools as A
    models = await call(url, identity_headers(anna), "list_models", {"query": "res.partner", "limit": 5})
    check(results, "list_models as A", models.get("success") is True)
    fields = await call(url, identity_headers(anna), "get_model_fields", {"model": "res.partner", "field_names": ["name", "user_id"]})
    check(results, "get_model_fields as A", fields.get("success") is True)
    alpha_id = fixture["partners"]["Alpha Kunde"]
    read_a = await call(url, identity_headers(anna), "read_record", {"model": "res.partner", "record_id": alpha_id, "fields": ["name"]})
    read_b = await call(url, identity_headers(bob), "read_record", {"model": "res.partner", "record_id": alpha_id, "fields": ["name"]})

    def record_of(payload: dict[str, Any]) -> dict[str, Any]:
        for key in ("record", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
        return {}

    check(results, "read_record: A reads Alpha", read_a.get("success") is True and record_of(read_a).get("name") == "Alpha Kunde", read_a.get("error") or read_a)
    check(results, "read_record: B cannot read Alpha", read_b.get("success") is False or not record_of(read_b), read_b.get("error") or record_of(read_b))
    agg_a = await call(url, identity_headers(anna), "aggregate_records", {"model": "res.partner", "group_by": ["user_id"], "domain": partner_domain})
    groups = next((agg_a[key] for key in ("result", "groups", "rows") if isinstance(agg_a.get(key), list)), [])
    check(results, "aggregate_records as A returns one group", agg_a.get("success") is True and len(groups) == 1, agg_a.get("error") or agg_a)
    health = await call(url, identity_headers(None), "health_check", {})
    posture = health.get("identity", {})
    check(results, "health_check posture: request mode, no shared credentials", posture.get("request_identity_required") is True and posture.get("configured_shared_credentials_used") is False and posture.get("ok") is True, posture.get("warnings"))

    # 6. gated write: approval bound to A
    validated = await call(url, identity_headers(anna), "validate_write", {"model": "res.partner", "operation": "create", "values": {"name": "Anna Neu", "user_id": anna["uid"]}})
    approval = validated.get("approval")
    check(results, "validate_write as A stores an approval", validated.get("success") is True and validated.get("approval_status", {}).get("stored") is True and approval and approval.get("principal") == anna["login"], validated.get("error") or validated.get("approval_status"))
    if approval:
        hijack = await call(url, identity_headers(bob), "execute_approved_write", {"approval": approval, "confirm": True})
        check(results, "B cannot execute A's approval", hijack.get("success") is False and "different user" in hijack.get("error", ""), hijack.get("error"))
        executed = await call(url, identity_headers(anna), "execute_approved_write", {"approval": approval, "confirm": True})
        check(results, "A executes own approval", executed.get("success") is True and isinstance(executed.get("result"), int), executed.get("error"))
        after_a = await call(url, identity_headers(anna), "search_records", {"model": "res.partner", "domain": [["name", "=", "Anna Neu"]], "fields": ["name", "create_uid"]})
        after_b = await call(url, identity_headers(bob), "search_records", {"model": "res.partner", "domain": [["name", "=", "Anna Neu"]], "fields": ["name"]})
        created = after_a.get("result", [])
        check(results, "record was created AS A (create_uid) and is visible to A only", len(created) == 1 and created[0].get("create_uid", [None])[0] == anna["uid"] and after_b.get("result") == [], (created, after_b.get("result")))
        replay = await call(url, identity_headers(anna), "execute_approved_write", {"approval": approval, "confirm": True})
        check(results, "approval is single-use", replay.get("success") is False)

    # 7. execute_method keeps its guards, runs as the caller
    count_a = await call(url, identity_headers(anna), "execute_method", {"model": "res.partner", "method": "search_count", "args": [partner_domain]})
    count_b = await call(url, identity_headers(bob), "execute_method", {"model": "res.partner", "method": "search_count", "args": [partner_domain]})
    check(results, "execute_method search_count differs per user's ACL", count_a.get("result") == 1 and count_b.get("result") == 1 and count_a.get("success") and count_b.get("success"), (count_a, count_b))
    blocked = await call(url, identity_headers(anna), "execute_method", {"model": "res.partner", "method": "write", "args": [[alpha_id], {"name": "x"}]})
    check(results, "execute_method blocks direct write", blocked.get("success") is False and "blocks" in blocked.get("error", ""))

    # 8. audit: principal present, key absent
    text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    entries = [json.loads(line) for line in text.splitlines() if line.strip()]
    principals = {e.get("principal") for e in entries if e.get("event") in {"validate", "execute"}}
    check(results, "audit log names the principal", anna["login"] in principals and bob["login"] in principals, sorted(p for p in principals if p))
    check(results, "audit log never contains an API key", anna["key"] not in text and bob["key"] not in text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="18.0")
    parser.add_argument("--port", type=int, default=18369)
    parser.add_argument("--mcp-port", type=int, default=19369)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--keep-stack", action="store_true")
    args = parser.parse_args()

    slug = "".join(ch for ch in args.version if ch.isalnum())
    target = harness.VersionTarget(
        version=args.version,
        project=f"algorithma-identity-smoke-{slug}",
        port=args.port,
        mcp_port=args.mcp_port,
        database=f"identity_smoke_{slug}",
    )
    env = harness.compose_env(target)
    results: dict[str, Any] = {}
    process: subprocess.Popen[str] | None = None
    fixture: dict[str, Any] | None = None
    audit_path = Path(tempfile.mkdtemp(prefix="algorithma-identity-")) / "audit.jsonl"
    started = time.monotonic()
    try:
        harness.run(harness.compose_cmd(target, "down", "-v", "--remove-orphans"), env=env, check=False)
        harness.run(harness.compose_cmd(target, "up", "-d", "db"), env=env, timeout=300)
        harness.init_database(target, env)
        harness.run(harness.compose_cmd(target, "up", "-d", "odoo"), env=env, timeout=300)
        harness.wait_for_http(target.port, args.timeout)
        harness.wait_for_xmlrpc(target, args.timeout)
        fixture = create_fixture(target, env)
        print(f"fixture ready: A={fixture['A']['login']} (key sha256 {fingerprint(fixture['A']['key'])}), "
              f"B={fixture['B']['login']} (key sha256 {fingerprint(fixture['B']['key'])})")

        server_env = os.environ.copy()
        for secret in ("ODOO_USERNAME", "ODOO_PASSWORD", "ODOO_API_KEY", "ODOO_CONFIG_FILE"):
            server_env.pop(secret, None)
        server_env.update({
            "ODOO_MCP_IDENTITY_MODE": "request",
            "ODOO_URL": f"http://127.0.0.1:{target.port}",
            "ODOO_DB": target.database,
            "ODOO_TRANSPORT": "xmlrpc",
            "ODOO_TIMEOUT": "30",
            "ODOO_MCP_ENABLE_WRITES": "1",
            "ODOO_MCP_AUDIT_LOG": str(audit_path),
            "ODOO_MCP_RATE_LIMIT_MODE": "off",
            "ODOO_MCP_TOOLS_EXCLUDE": "index_knowledge,search_knowledge,knowledge_stats,submit_async_task,get_async_task,cancel_async_task,list_async_tasks",
            "PYTHONPATH": str(ROOT / "src"),
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "odoo_mcp", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(target.mcp_port)],
            cwd=ROOT, env=server_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        url = f"http://127.0.0.1:{target.mcp_port}/mcp"
        asyncio.run(wait_for_mcp(url, process, 60))
        print(f"MCP server ready at {url} (request identity mode)")
        asyncio.run(exercise(url, fixture, audit_path, results))
    finally:
        server_log = ""
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                _, server_log = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                _, server_log = process.communicate(timeout=10)
        if process is not None and fixture is not None:
            secrets = [fixture[tag]["key"] for tag in ("A", "B")]
            leaked = any(secret in server_log for secret in secrets)
            check(results, "server log never contains an API key", not leaked)
            if any(not item["ok"] for item in results.values()):
                print("--- last MCP server log lines ---")
                print("\n".join(server_log.splitlines()[-40:]))
        if not args.keep_stack:
            harness.run(harness.compose_cmd(target, "down", "-v", "--remove-orphans"), env=env, check=False)

    failed = [name for name, item in results.items() if not item["ok"]]
    summary = {
        "odoo_version": args.version,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "checks": len(results),
        "failed": failed,
        "results": results,
    }
    print(json.dumps(summary, indent=2, default=str))
    print("IDENTITY SMOKE:", "PASS" if not failed else f"FAIL ({len(failed)} check(s))")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
