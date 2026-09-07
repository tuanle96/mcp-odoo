#!/usr/bin/env python3
"""Two-user access-control proof for request identity mode (Algorithma fork).

Creates two restricted internal users with their own API keys plus a record
rule that lets each of them see only "their" partners, starts odoo-mcp in
``ODOO_MCP_IDENTITY_MODE=request`` over Streamable HTTP and proves, through
the MCP tools:

    same MCP + same tool + same model + different credentials
        -> different Odoo-visible records

plus: no identity -> refused; wrong key -> sanitized refusal; an approval
validated by A cannot be executed by B; ``execute_method`` keeps its
side-effect guards; the audit log and the server log carry the principal but
never a key.

Two backends:

``--backend compose`` (default) boots a disposable Odoo through the same
Docker Compose harness as ``odoo_compose_smoke.py`` and tears it down.

``--backend container`` targets an *existing* Odoo container (e.g. the demo
tenant on the dev VM). The fixture is created with ``odoo shell`` inside that
container. It is deliberately harmless for real users: the record rule is
global but only restricts logins ending in ``@identity-smoke.test``; test
partners carry a name prefix; everything is idempotent and ``--cleanup``
removes it again.

    uv run --python 3.12 --with-editable . scripts/algorithma_identity_smoke.py
    uv run python scripts/algorithma_identity_smoke.py --backend container \\
        --container demo-odoo --odoo-url http://127.0.0.1:8071 --db demo \\
        --docker-sudo --mcp-port 8010 --save-keys ~/.algorithma-vnext/demo-smoke-keys.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
import odoo_compose_smoke as harness  # noqa: E402  (sibling script, no package)

ROOT = Path(__file__).resolve().parents[1]
SMOKE_LOGIN_SUFFIX = "@identity-smoke.test"
RULE_NAME = "Algorithma identity smoke: partners by salesperson (smoke users only)"
CROSS_USER_TOOLS = (
    "index_knowledge,search_knowledge,knowledge_stats,"
    "submit_async_task,get_async_task,cancel_async_task,list_async_tasks"
)


@dataclass(frozen=True)
class Names:
    """Fixture naming, prefixed so the data is obviously test data in a real DB."""

    prefix: str

    @property
    def alpha(self) -> str:
        return f"{self.prefix}Alpha Kunde"

    @property
    def beta(self) -> str:
        return f"{self.prefix}Beta Kunde"

    @property
    def gamma(self) -> str:
        return f"{self.prefix}Gamma Kunde"

    @property
    def new(self) -> str:
        return f"{self.prefix}Anna Neu"

    @property
    def marker(self) -> str:
        """Substring shared by every fixture partner (for ``ilike`` domains)."""
        return self.prefix.strip() or "Kunde"

    @property
    def users(self) -> dict[str, dict[str, str]]:
        return {
            "A": {"login": "anna" + SMOKE_LOGIN_SUFFIX, "name": f"{self.prefix}Anna Smoke"},
            "B": {"login": "bob" + SMOKE_LOGIN_SUFFIX, "name": f"{self.prefix}Bob Smoke"},
        }


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


# --- Odoo fixture (users, keys, rule, partners) ------------------------------


def fixture_script(names: Names) -> str:
    """Python for ``odoo shell``: idempotent users, keys, rule, partners."""
    partners = {names.alpha: "A", names.beta: "B", names.gamma: None}
    return f"""
from datetime import datetime, timedelta
import json
Users = env["res.users"].sudo()
Partners = env["res.partner"].sudo()
Rules = env["ir.rule"].sudo()
group = env.ref("base.group_user")
# Contact creation is a separate Odoo right (Extra Rights / Contact Creation);
# the smoke users need it for the write proof. The record rule below still
# limits them to partners where they are the salesperson.
creator = env.ref("base.group_partner_manager", raise_if_not_found=False)
groups = [group.id] + ([creator.id] if creator else [])
company = env.ref("base.main_company")
group_field = "groups_id" if "groups_id" in Users._fields else "group_ids"
out = {{}}
for tag, spec in {names.users!r}.items():
    user = Users.with_context(active_test=False).search([("login", "=", spec["login"])], limit=1)
    vals = {{
        "name": spec["name"], "login": spec["login"], "active": True,
        "company_id": company.id, "company_ids": [(6, 0, [company.id])],
        group_field: [(6, 0, groups)],
    }}
    if user:
        user.write(vals)
    else:
        user = Users.create(dict(vals, password="unused-" + tag))
    # Old smoke keys are removed so each run has exactly one live key per user.
    env["res.users.apikeys"].sudo().search([("user_id", "=", user.id), ("name", "=", "algorithma identity smoke")]).unlink()
    key = env["res.users.apikeys"].sudo().with_user(user)._generate(
        "rpc", "algorithma identity smoke", datetime.now() + timedelta(days=7)
    )
    out[tag] = {{"uid": user.id, "login": spec["login"], "key": key}}
model = env["ir.model"].sudo()._get("res.partner")
# Global rule: restricts ONLY logins ending in {SMOKE_LOGIN_SUFFIX}; a no-op for everyone else.
domain = "[('user_id', '=', user.id)] if user.login.endswith({SMOKE_LOGIN_SUFFIX!r}) else [(1, '=', 1)]"
rule = Rules.search([("name", "=", {RULE_NAME!r})], limit=1)
rule_vals = {{
    "name": {RULE_NAME!r}, "model_id": model.id, "domain_force": domain,
    "groups": [(5, 0, 0)], "global": True,
    "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": True,
}}
if rule:
    rule.write(rule_vals)
else:
    rule = Rules.create(rule_vals)
partners = {{}}
for name, owner in {partners!r}.items():
    existing = Partners.with_context(active_test=False).search([("name", "=", name)], limit=1)
    vals = {{"name": name, "active": True, "user_id": out[owner]["uid"] if owner else False}}
    if existing:
        existing.write(vals)
        record = existing
    else:
        record = Partners.create(vals)
    partners[name] = record.id
# A previous run's write-test record must not pre-exist.
Partners.with_context(active_test=False).search([("name", "=", {names.new!r})]).unlink()
out["partners"] = partners
env.cr.commit()
# A plain shell commit does not notify the running Odoo server; without this
# it keeps stale per-user ACL/rule caches (e.g. an old "may not create").
env.registry.signal_changes()
print("FIXTURE=" + json.dumps(out))
"""


def cleanup_script(names: Names) -> str:
    """Python for ``odoo shell``: remove everything the fixture created."""
    logins = [spec["login"] for spec in names.users.values()]
    partner_names = [names.alpha, names.beta, names.gamma, names.new]
    return f"""
Users = env["res.users"].sudo().with_context(active_test=False)
Partners = env["res.partner"].sudo().with_context(active_test=False)
removed = {{}}
partners = Partners.search([("name", "in", {partner_names!r})])
removed["partners"] = len(partners)
partners.unlink()
users = Users.search([("login", "in", {logins!r})])
env["res.users.apikeys"].sudo().search([("user_id", "in", users.ids)]).unlink()
users.write({{"active": False}})
removed["users_deactivated"] = users.ids
rule = env["ir.rule"].sudo().search([("name", "=", {RULE_NAME!r})])
removed["rules"] = len(rule)
rule.unlink()
env.cr.commit()
env.registry.signal_changes()
print("CLEANUP=" + str(removed))
"""


def odoo_shell_cmd(args: argparse.Namespace, target: harness.VersionTarget | None) -> list[str]:
    if args.backend == "compose":
        assert target is not None
        return harness.compose_cmd(
            target, "run", "--rm", "-T", "odoo", "odoo", "shell", "-d", target.database,
            "--db_host=db", "--db_port=5432", "--db_user", harness.DB_USER,
            "--db_password", harness.DB_PASSWORD,
        )
    cmd = ["sudo", "-n"] if args.docker_sudo else []
    cmd += ["docker", "exec", "-i", args.container, "odoo", "shell", "-c", args.odoo_conf, "-d", args.db, "--no-http"]
    return cmd


def run_shell(args: argparse.Namespace, target: harness.VersionTarget | None, env: dict[str, str], script: str, marker: str) -> str:
    completed = harness.run(odoo_shell_cmd(args, target), env=env, timeout=300, input_text=script, capture_output=True)
    for line in completed.stdout.splitlines():
        if line.startswith(marker):
            return line.removeprefix(marker)
    tail = "\n".join((completed.stderr or "").splitlines()[-15:])
    raise AssertionError(f"odoo shell did not print {marker}<...>; stderr tail:\n{tail}")


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
                return harness.decode_tool_json(await session.call_tool(tool, arguments=arguments), tool)


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
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""), flush=True)


def record_of(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("record", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return {}


async def exercise(url: str, fixture: dict[str, Any], audit_path: Path, results: dict[str, Any], names: Names) -> None:
    anna, bob = fixture["A"], fixture["B"]
    partner_domain = [["name", "ilike", names.marker]]
    search = {"model": "res.partner", "domain": partner_domain, "fields": ["name"], "limit": 10}

    # 1. no identity -> refused, and nothing else happened
    refused = await call(url, identity_headers(None), "search_records", search)
    check(results, "no identity fails closed", refused.get("success") is False and "identity" in refused.get("error", "").lower(), refused.get("error"))

    # 2./3. same tool, same model, different users -> different records
    as_a = await call(url, identity_headers(anna), "search_records", search)
    as_b = await call(url, identity_headers(bob), "search_records", search)
    names_a = sorted(r["name"] for r in as_a.get("result", []))
    names_b = sorted(r["name"] for r in as_b.get("result", []))
    check(results, "user A sees only A's records", bool(as_a.get("success")) and names_a == [names.alpha], names_a or as_a.get("error"))
    check(results, "user B sees only B's records", bool(as_b.get("success")) and names_b == [names.beta], names_b or as_b.get("error"))

    # 4. wrong key -> sanitized refusal
    wrong = await call(url, identity_headers(anna, key="not-the-key-" + "x" * 30), "search_records", search)
    check(results, "invalid key is refused without echoing it", wrong.get("success") is False and "not-the-key" not in json.dumps(wrong) and "rejected" in wrong.get("error", ""), wrong.get("error"))

    # 5. generic read tools as A / B
    # list_models reads ir.model; whether a plain user may do that is Odoo's
    # call (some databases restrict it to Access Rights admins). Either a
    # successful listing or a clean Odoo access refusal proves the call ran
    # as A - what must never happen is a bot-account answer.
    models = await call(url, identity_headers(anna), "list_models", {"query": "res.partner", "limit": 5})
    check(results, "list_models as A (result or Odoo ACL refusal)", models.get("success") is True or "not allowed" in models.get("error", ""), models.get("error"))
    fields = await call(url, identity_headers(anna), "get_model_fields", {"model": "res.partner", "field_names": ["name", "user_id"]})
    check(results, "get_model_fields as A", fields.get("success") is True, fields.get("error"))
    alpha_id = fixture["partners"][names.alpha]
    read_a = await call(url, identity_headers(anna), "read_record", {"model": "res.partner", "record_id": alpha_id, "fields": ["name"]})
    read_b = await call(url, identity_headers(bob), "read_record", {"model": "res.partner", "record_id": alpha_id, "fields": ["name"]})
    check(results, "read_record: A reads Alpha", read_a.get("success") is True and record_of(read_a).get("name") == names.alpha, read_a.get("error") or read_a)
    check(results, "read_record: B cannot read Alpha", read_b.get("success") is False or not record_of(read_b), read_b.get("error") or record_of(read_b))
    agg_a = await call(url, identity_headers(anna), "aggregate_records", {"model": "res.partner", "group_by": ["user_id"], "domain": partner_domain})
    groups = next((agg_a[key] for key in ("result", "groups", "rows") if isinstance(agg_a.get(key), list)), [])
    check(results, "aggregate_records as A returns one group", agg_a.get("success") is True and len(groups) == 1, agg_a.get("error") or agg_a)
    health = await call(url, identity_headers(None), "health_check", {})
    posture = health.get("identity", {})
    check(results, "health_check posture: request mode, no shared credentials", posture.get("request_identity_required") is True and posture.get("configured_shared_credentials_used") is False and posture.get("ok") is True, posture.get("warnings"))

    # 6. gated write: approval bound to A
    validated = await call(url, identity_headers(anna), "validate_write", {"model": "res.partner", "operation": "create", "values": {"name": names.new, "user_id": anna["uid"]}})
    approval = validated.get("approval")
    check(results, "validate_write as A stores an approval", validated.get("success") is True and validated.get("approval_status", {}).get("stored") is True and bool(approval) and approval.get("principal") == anna["login"], validated.get("error") or validated.get("approval_status"))
    if approval:
        hijack = await call(url, identity_headers(bob), "execute_approved_write", {"approval": approval, "confirm": True})
        check(results, "B cannot execute A's approval", hijack.get("success") is False and "different user" in hijack.get("error", ""), hijack.get("error"))
        executed = await call(url, identity_headers(anna), "execute_approved_write", {"approval": approval, "confirm": True})
        check(results, "A executes own approval", executed.get("success") is True and isinstance(executed.get("result"), int), executed.get("error"))
        after_a = await call(url, identity_headers(anna), "search_records", {"model": "res.partner", "domain": [["name", "=", names.new]], "fields": ["name", "create_uid"]})
        after_b = await call(url, identity_headers(bob), "search_records", {"model": "res.partner", "domain": [["name", "=", names.new]], "fields": ["name"]})
        created = after_a.get("result", [])
        created_by = created[0].get("create_uid") if created else None
        created_uid = created_by[0] if isinstance(created_by, list) else created_by
        check(results, "record was created AS A (create_uid) and is visible to A only", len(created) == 1 and created_uid == anna["uid"] and after_b.get("result") == [], (created, after_b.get("result")))
        replay = await call(url, identity_headers(anna), "execute_approved_write", {"approval": approval, "confirm": True})
        check(results, "approval is single-use", replay.get("success") is False)

    # 7. execute_method keeps its guards, runs as the caller
    count_a = await call(url, identity_headers(anna), "execute_method", {"model": "res.partner", "method": "search_count", "args": [partner_domain]})
    count_b = await call(url, identity_headers(bob), "execute_method", {"model": "res.partner", "method": "search_count", "args": [partner_domain]})
    expected_a = 2 if approval else 1  # Alpha (+ Anna Neu after the write test)
    check(results, "execute_method search_count runs under each user's ACL", count_a.get("success") is True and count_b.get("success") is True and count_a.get("result") == expected_a and count_b.get("result") == 1, (count_a.get("result"), count_b.get("result")))
    blocked = await call(url, identity_headers(anna), "execute_method", {"model": "res.partner", "method": "write", "args": [[alpha_id], {"name": "x"}]})
    check(results, "execute_method blocks direct write", blocked.get("success") is False and "blocks" in blocked.get("error", ""), blocked.get("error"))

    # 8. audit: principal present, key absent
    text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    entries = [json.loads(line) for line in text.splitlines() if line.strip()]
    principals = {e.get("principal") for e in entries if e.get("event") in {"validate", "execute"}}
    check(results, "audit log names the principal", anna["login"] in principals and bob["login"] in principals, sorted(p for p in principals if p))
    check(results, "audit log never contains an API key", anna["key"] not in text and bob["key"] not in text)


def save_keys(path: Path, fixture: dict[str, Any], url: str, odoo_url: str, db: str) -> None:
    """Write the disposable keys 0600 for later manual runs of identity_client.py."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "Disposable identity-smoke users; remove with --cleanup. Never commit this file.",
        "mcp_url": url,
        "odoo_url": odoo_url,
        "db": db,
        "A": {"email": fixture["A"]["login"], "api_key": fixture["A"]["key"]},
        "B": {"email": fixture["B"]["login"], "api_key": fixture["B"]["key"]},
    }
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", choices=("compose", "container"), default="compose")
    parser.add_argument("--version", default="18.0", help="compose: Odoo image version")
    parser.add_argument("--port", type=int, default=18369, help="compose: host port for Odoo")
    parser.add_argument("--container", default="demo-odoo", help="container: running Odoo container name")
    parser.add_argument("--odoo-conf", default="/etc/odoo/odoo.conf", help="container: odoo.conf inside the container")
    parser.add_argument("--odoo-url", default="http://127.0.0.1:8071", help="container: Odoo URL as seen from this host")
    parser.add_argument("--db", default="demo", help="container: database name")
    parser.add_argument("--docker-sudo", action="store_true", help="container: prefix docker with sudo -n")
    parser.add_argument("--name-prefix", default=None, help="fixture name prefix (default: 'ZZ Identity-Smoke ' for container, none for compose)")
    parser.add_argument("--mcp-port", type=int, default=19369)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--keep-stack", action="store_true", help="compose: do not tear down")
    parser.add_argument("--save-keys", default=None, help="write the test users' keys (0600) to this path")
    parser.add_argument("--cleanup", action="store_true", help="remove the fixture from the target database and exit")
    args = parser.parse_args()

    prefix = args.name_prefix if args.name_prefix is not None else ("ZZ Identity-Smoke " if args.backend == "container" else "")
    names = Names(prefix)
    target: harness.VersionTarget | None = None
    env = os.environ.copy()
    if args.backend == "compose":
        slug = "".join(ch for ch in args.version if ch.isalnum())
        target = harness.VersionTarget(version=args.version, project=f"algorithma-identity-smoke-{slug}", port=args.port, mcp_port=args.mcp_port, database=f"identity_smoke_{slug}")
        env = harness.compose_env(target)
        odoo_url, db = f"http://127.0.0.1:{target.port}", target.database
    else:
        odoo_url, db = args.odoo_url, args.db

    if args.cleanup:
        print(run_shell(args, target, env, cleanup_script(names), "CLEANUP="))
        return 0

    results: dict[str, Any] = {}
    process: subprocess.Popen[str] | None = None
    fixture: dict[str, Any] | None = None
    audit_path = Path(tempfile.mkdtemp(prefix="algorithma-identity-")) / "audit.jsonl"
    started = time.monotonic()
    url = f"http://127.0.0.1:{args.mcp_port}/mcp"
    try:
        if args.backend == "compose":
            assert target is not None
            harness.run(harness.compose_cmd(target, "down", "-v", "--remove-orphans"), env=env, check=False)
            harness.run(harness.compose_cmd(target, "up", "-d", "db"), env=env, timeout=300)
            harness.init_database(target, env)
            harness.run(harness.compose_cmd(target, "up", "-d", "odoo"), env=env, timeout=300)
            harness.wait_for_http(target.port, args.timeout)
            harness.wait_for_xmlrpc(target, args.timeout)
        fixture = json.loads(run_shell(args, target, env, fixture_script(names), "FIXTURE="))
        print(f"fixture ready in {db}: A={fixture['A']['login']} (key sha256 {fingerprint(fixture['A']['key'])}), "
              f"B={fixture['B']['login']} (key sha256 {fingerprint(fixture['B']['key'])}), partners={fixture['partners']}", flush=True)
        if args.save_keys:
            save_keys(Path(args.save_keys).expanduser(), fixture, url, odoo_url, db)
            print(f"keys saved (0600) to {args.save_keys}", flush=True)

        server_env = os.environ.copy()
        for secret in ("ODOO_USERNAME", "ODOO_PASSWORD", "ODOO_API_KEY", "ODOO_CONFIG_FILE"):
            server_env.pop(secret, None)
        server_env.update({
            "ODOO_MCP_IDENTITY_MODE": "request",
            "ODOO_URL": odoo_url,
            "ODOO_DB": db,
            "ODOO_TRANSPORT": "xmlrpc",
            "ODOO_TIMEOUT": "30",
            "ODOO_MCP_ENABLE_WRITES": "1",
            "ODOO_MCP_AUDIT_LOG": str(audit_path),
            "ODOO_MCP_RATE_LIMIT_MODE": "off",
            "ODOO_MCP_TOOLS_EXCLUDE": CROSS_USER_TOOLS,
            "PYTHONPATH": str(ROOT / "src"),
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "odoo_mcp", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(args.mcp_port)],
            cwd=ROOT, env=server_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        asyncio.run(wait_for_mcp(url, process, 60))
        print(f"MCP server ready at {url} (request identity mode, Odoo {odoo_url} db {db})", flush=True)
        asyncio.run(exercise(url, fixture, audit_path, results, names))
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
            check(results, "server log never contains an API key", not any(secret in server_log for secret in secrets))
            if any(not item["ok"] for item in results.values()):
                print("--- last MCP server log lines ---")
                print("\n".join(server_log.splitlines()[-40:]))
        if args.backend == "compose" and not args.keep_stack and target is not None:
            harness.run(harness.compose_cmd(target, "down", "-v", "--remove-orphans"), env=env, check=False)

    failed = [name for name, item in results.items() if not item["ok"]]
    summary = {
        "backend": args.backend,
        "odoo": {"url": odoo_url, "db": db},
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "checks": len(results),
        "failed": failed,
        "results": results,
    }
    summary_path = Path(tempfile.gettempdir()) / "algorithma-identity-smoke-last.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"--- summary ({len(results)} checks, {summary['elapsed_seconds']}s, details in {summary_path})")
    for name, item in results.items():
        line = f"  [{'PASS' if item['ok'] else 'FAIL'}] {name}"
        if not item["ok"]:
            line += f" — {json.dumps(item['detail'], default=str)[:300]}"
        print(line)
    print("IDENTITY SMOKE:", "PASS" if not failed else f"FAIL ({len(failed)} check(s))")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
