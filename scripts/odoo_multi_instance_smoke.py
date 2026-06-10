#!/usr/bin/env python3
"""
Boot one disposable Odoo Docker Compose stack with three databases and run a
live multi-instance MCP smoke test:

- 3 instances at once (same Odoo server, three isolated databases)
- 2 accounts on the same instance (admin + dedicated bot user)
- per-instance write flow (validate -> execute) with marker records
- cross-instance isolation, default-instance routing, and token replay rejection
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xmlrpc.client
from pathlib import Path
from typing import Any, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.integration.yml"
PROJECT = "mcp-odoo-multismoke"
DEFAULT_VERSION = "18.0"
DEFAULT_PORT = 18269
DB_USER = "odoo"
DB_PASSWORD = "odoo"
ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "admin"
BOT_LOGIN = "mcp.multi.bot@example.test"
BOT_PASSWORD = "mcp-multi-bot-secret"
INSTANCE_DBS = {
    "acme": "mcp_multi_acme",
    "globex": "mcp_multi_globex",
    "initech": "mcp_multi_initech",
}
WRITE_INSTANCES = tuple(INSTANCE_DBS)  # write smoke runs on all three
MARKER_PREFIX = "MCP MULTI MARKER"


def run(
    args: list[str],
    *,
    env: dict[str, str],
    check: bool = True,
    timeout: int = 600,
    attempts: int = 1,
    retry_delay_seconds: int = 10,
    input_text: str | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        prefix = f"+ [{attempt}/{attempts}] " if attempts > 1 else "+ "
        print(prefix + " ".join(args), flush=True)
        try:
            return subprocess.run(
                args,
                cwd=ROOT,
                env=env,
                check=check,
                timeout=timeout,
                text=True,
                input=input_text,
                capture_output=capture_output,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            for stream in (getattr(exc, "stdout", None), getattr(exc, "stderr", None)):
                if stream:
                    print(str(stream), file=sys.stderr, flush=True)
            if attempt == attempts or not check:
                raise
            print(f"Command failed: {exc!r}; retrying", file=sys.stderr, flush=True)
            time.sleep(retry_delay_seconds)
    raise AssertionError(f"unreachable retry state: {last_error!r}")


def compose_env(version: str, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ODOO_VERSION": version,
            "ODOO_PORT": str(port),
            "COMPOSE_PROJECT_NAME": PROJECT,
        }
    )
    return env


def compose_cmd(*args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--project-name",
        PROJECT,
        *args,
    ]


def init_database(database: str, env: dict[str, str]) -> None:
    run(
        compose_cmd(
            "run",
            "--rm",
            "odoo",
            "--stop-after-init",
            "-d",
            database,
            "-i",
            "base,mail",
            "--without-demo=all",
            "--db_host=db",
            "--db_port=5432",
            "--db_user",
            DB_USER,
            "--db_password",
            DB_PASSWORD,
        ),
        env=env,
        timeout=900,
    )


def create_bot_user(database: str, env: dict[str, str]) -> int:
    """Create a second internal account on one instance for the 2-account smoke."""
    script = f"""
login = {json.dumps(BOT_LOGIN)}
password = {json.dumps(BOT_PASSWORD)}
Users = env["res.users"].sudo()
user = Users.search([("login", "=", login)], limit=1)
group = env.ref("base.group_user")
company = env.ref("base.main_company")
group_field = "groups_id" if "groups_id" in Users._fields else "group_ids"
vals = {{
    "name": "MCP Multi Bot",
    "login": login,
    "password": password,
    "active": True,
    "company_id": company.id,
    "company_ids": [(6, 0, [company.id])],
    group_field: [(6, 0, [group.id])],
}}
if user:
    user.write(vals)
else:
    user = Users.create(vals)
env.cr.commit()
print("USERID=" + str(user.id))
"""
    completed = run(
        compose_cmd(
            "run",
            "--rm",
            "-T",
            "odoo",
            "odoo",
            "shell",
            "-d",
            database,
            "--db_host=db",
            "--db_port=5432",
            "--db_user",
            DB_USER,
            "--db_password",
            DB_PASSWORD,
        ),
        env=env,
        timeout=300,
        input_text=script,
        capture_output=True,
    )
    for line in completed.stdout.splitlines():
        if line.startswith("USERID="):
            return int(line.removeprefix("USERID=").strip())
    raise AssertionError("bot user creation did not print USERID=<id>")


def wait_for_http(port: int, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/web/login"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            last_error = exc
        time.sleep(2)
    raise TimeoutError(f"Odoo HTTP did not become ready at {url}: {last_error}")


def wait_for_xmlrpc(
    port: int, database: str, login: str, password: str, timeout_seconds: int
) -> int:
    deadline = time.monotonic() + timeout_seconds
    common = xmlrpc.client.ServerProxy(
        f"http://127.0.0.1:{port}/xmlrpc/2/common", allow_none=True
    )
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            uid = common.authenticate(database, login, password, {})
            if isinstance(uid, int) and uid > 0:
                return uid
        except (ConnectionError, OSError, xmlrpc.client.Error) as exc:
            last_error = exc
        time.sleep(2)
    raise TimeoutError(f"XML-RPC auth not ready for {database}/{login}: {last_error}")


def write_instances_config(port: int) -> str:
    """Write the multi-instance config: 3 databases + a 2nd account on acme."""
    url = f"http://127.0.0.1:{port}"
    config = {
        "default": "acme",
        "instances": {
            **{
                name: {
                    "url": url,
                    "db": database,
                    "username": ADMIN_LOGIN,
                    "password": ADMIN_PASSWORD,
                    "transport": "xmlrpc",
                }
                for name, database in INSTANCE_DBS.items()
            },
            "acme-bot": {
                "url": url,
                "db": INSTANCE_DBS["acme"],
                "username": BOT_LOGIN,
                "password": BOT_PASSWORD,
                "transport": "xmlrpc",
            },
        },
    }
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="mcp-multi-smoke-", delete=False
    )
    json.dump(config, handle, indent=2)
    handle.close()
    return handle.name


def mcp_server_env(config_path: str) -> dict[str, str]:
    """Inherit the shell env but strip every Odoo/MCP override so the file wins."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ODOO_", "MCP_"))
    }
    env.update(
        {
            "ODOO_CONFIG_FILE": config_path,
            "ODOO_MCP_ENABLE_WRITES": "1",
            "ODOO_TIMEOUT": "30",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    return env


def decode_tool_json(result: Any, tool_name: str) -> dict[str, Any]:
    content = result.content[0]
    if not isinstance(content, TextContent):
        raise AssertionError(f"MCP {tool_name} did not return text content")
    payload = json.loads(content.text)
    if not isinstance(payload, dict):
        raise AssertionError(f"MCP {tool_name} did not return a JSON object")
    return cast(dict[str, Any], payload)


async def call_json(
    session: ClientSession, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    return decode_tool_json(await session.call_tool(tool, arguments=arguments), tool)


async def smoke_discovery(session: ClientSession) -> dict[str, Any]:
    """list_instances + health_check posture, with credential redaction checks."""
    tools = await session.list_tools()
    tool_names = {tool.name for tool in tools.tools}
    if "list_instances" not in tool_names:
        raise AssertionError(f"list_instances tool missing: {sorted(tool_names)}")

    listing = await call_json(session, "list_instances", {})
    if not listing.get("success"):
        raise AssertionError(f"list_instances failed: {listing}")
    if listing.get("default") != "acme":
        raise AssertionError(f"wrong default instance: {listing}")
    names = sorted(item["name"] for item in listing.get("instances", []))
    expected_names = sorted([*INSTANCE_DBS, "acme-bot"])
    if names != expected_names:
        raise AssertionError(f"instance names mismatch: {names} != {expected_names}")
    serialized = json.dumps(listing)
    for secret in (ADMIN_PASSWORD, BOT_PASSWORD):
        if secret in serialized:
            raise AssertionError("list_instances leaked a credential")

    health = await call_json(session, "health_check", {})
    posture = health.get("runtime", {}).get("odoo_instances", {})
    if posture != {"instance_count": 4, "default_instance": "acme"}:
        raise AssertionError(f"health_check instance posture wrong: {posture}")
    return {
        "tool_count": len(tool_names),
        "instances": names,
        "default": listing["default"],
        "health_posture": posture,
    }


async def smoke_write_marker(session: ClientSession, instance: str) -> dict[str, Any]:
    """validate -> execute a create on one instance; returns the marker record."""
    marker = f"{MARKER_PREFIX} {instance}"
    validation = await call_json(
        session,
        "validate_write",
        {
            "model": "res.partner",
            "operation": "create",
            "values": {"name": marker},
            "instance": instance,
        },
    )
    if not validation.get("success"):
        raise AssertionError(f"validate_write({instance}) failed: {validation}")
    approval = validation["approval"]
    if approval.get("instance") != instance:
        raise AssertionError(f"approval not bound to {instance}: {approval}")

    executed = await call_json(
        session,
        "execute_approved_write",
        {"approval": approval, "confirm": True},
    )
    if not executed.get("success"):
        raise AssertionError(f"execute_approved_write({instance}) failed: {executed}")
    if executed.get("instance") != instance:
        raise AssertionError(f"write executed on wrong instance: {executed}")
    return {
        "instance": instance,
        "marker": marker,
        "record_id": executed.get("result"),
        "token": approval["token"],
    }


async def smoke_concurrent_reads(session: ClientSession) -> dict[str, Any]:
    """Read all three instances at once; each must see ONLY its own marker."""
    async def read_markers(instance: str) -> tuple[str, list[str]]:
        result = await call_json(
            session,
            "search_records",
            {
                "model": "res.partner",
                "domain": [["name", "like", MARKER_PREFIX]],
                "fields": ["id", "name"],
                "limit": 10,
                "instance": instance,
            },
        )
        if not result.get("success"):
            raise AssertionError(f"search_records({instance}) failed: {result}")
        return instance, sorted(row["name"] for row in result.get("result", []))

    pairs = await asyncio.gather(*(read_markers(name) for name in INSTANCE_DBS))
    seen = dict(pairs)
    for instance, marker_names in seen.items():
        expected = [f"{MARKER_PREFIX} {instance}"]
        if marker_names != expected:
            raise AssertionError(
                f"instance {instance} isolation broken: saw {marker_names}, "
                f"expected {expected}"
            )
    return {"concurrent": True, "markers_per_instance": seen}


async def smoke_default_routing(session: ClientSession) -> dict[str, Any]:
    """Omitting `instance` must route to the default (acme)."""
    result = await call_json(
        session,
        "search_records",
        {
            "model": "res.partner",
            "domain": [["name", "like", MARKER_PREFIX]],
            "fields": ["id", "name"],
            "limit": 10,
        },
    )
    names = sorted(row["name"] for row in result.get("result", []))
    if names != [f"{MARKER_PREFIX} acme"]:
        raise AssertionError(f"default routing did not hit acme: {names}")
    return {"default_instance_marker": names}


async def smoke_two_accounts(session: ClientSession) -> dict[str, Any]:
    """Two accounts against the SAME database must authenticate independently."""
    admin_profile = await call_json(
        session,
        "get_odoo_profile",
        {"include_modules": False, "instance": "acme"},
    )
    bot_profile = await call_json(
        session,
        "get_odoo_profile",
        {"include_modules": False, "instance": "acme-bot"},
    )
    for label, profile in (("acme", admin_profile), ("acme-bot", bot_profile)):
        if not profile.get("success"):
            raise AssertionError(f"get_odoo_profile({label}) failed: {profile}")
    admin_username = admin_profile["profile"].get("username")
    bot_username = bot_profile["profile"].get("username")
    same_db = (
        admin_profile["profile"].get("database")
        == bot_profile["profile"].get("database")
        == INSTANCE_DBS["acme"]
    )
    if not same_db:
        raise AssertionError(
            f"accounts not on the same database: {admin_profile} / {bot_profile}"
        )
    if admin_username != ADMIN_LOGIN or bot_username != BOT_LOGIN:
        raise AssertionError(
            f"account identities wrong: {admin_username!r} / {bot_username!r}"
        )

    # The second account must be able to read through its own session.
    bot_read = await call_json(
        session,
        "search_records",
        {
            "model": "res.partner",
            "domain": [["name", "like", MARKER_PREFIX]],
            "fields": ["id", "name"],
            "limit": 10,
            "instance": "acme-bot",
        },
    )
    if not bot_read.get("success") or bot_read.get("count", 0) != 1:
        raise AssertionError(f"bot account read failed: {bot_read}")
    return {
        "same_database": INSTANCE_DBS["acme"],
        "admin_username": admin_username,
        "bot_username": bot_username,
        "bot_visible_markers": bot_read.get("count"),
    }


async def smoke_token_replay_rejected(session: ClientSession) -> dict[str, Any]:
    """An approval validated on globex must not execute against initech."""
    validation = await call_json(
        session,
        "validate_write",
        {
            "model": "res.partner",
            "operation": "create",
            "values": {"name": f"{MARKER_PREFIX} replay-probe"},
            "instance": "globex",
        },
    )
    if not validation.get("success"):
        raise AssertionError(f"replay-probe validate failed: {validation}")
    tampered = dict(validation["approval"])
    tampered["instance"] = "initech"
    rejected = await call_json(
        session,
        "execute_approved_write",
        {"approval": tampered, "confirm": True},
    )
    if rejected.get("success"):
        raise AssertionError(f"cross-instance token replay was ACCEPTED: {rejected}")
    if "token" not in str(rejected.get("error", "")):
        raise AssertionError(f"replay rejected for the wrong reason: {rejected}")

    # The probe was never executed anywhere: neither initech nor globex has it.
    for instance in ("initech", "globex"):
        probe = await call_json(
            session,
            "search_records",
            {
                "model": "res.partner",
                "domain": [["name", "=", f"{MARKER_PREFIX} replay-probe"]],
                "fields": ["id"],
                "limit": 1,
                "instance": instance,
            },
        )
        if probe.get("count", 0) != 0:
            raise AssertionError(f"replay probe leaked into {instance}: {probe}")
    return {"replay_rejected": True, "error": rejected.get("error")}


async def smoke_unknown_instance(session: ClientSession) -> dict[str, Any]:
    result = await call_json(
        session,
        "search_records",
        {"model": "res.partner", "limit": 1, "instance": "ghost"},
    )
    error = str(result.get("error", ""))
    if result.get("success") or "ghost" not in error or "acme" not in error:
        raise AssertionError(f"unknown-instance error wrong: {result}")
    for secret in (ADMIN_PASSWORD, BOT_PASSWORD):
        if secret in error:
            raise AssertionError("unknown-instance error leaked a credential")
    return {"unknown_instance_error": error}


async def run_mcp_smoke(config_path: str) -> dict[str, Any]:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "odoo_mcp"],
        env=mcp_server_env(config_path),
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            report: dict[str, Any] = {}
            report["discovery"] = await smoke_discovery(session)
            report["writes"] = [
                await smoke_write_marker(session, name) for name in WRITE_INSTANCES
            ]
            tokens = {item["token"] for item in report["writes"]}
            if len(tokens) != len(WRITE_INSTANCES):
                raise AssertionError(f"approval tokens collided across instances: {tokens}")
            report["concurrent_reads"] = await smoke_concurrent_reads(session)
            report["default_routing"] = await smoke_default_routing(session)
            report["two_accounts_same_instance"] = await smoke_two_accounts(session)
            report["token_replay"] = await smoke_token_replay_rejected(session)
            report["unknown_instance"] = await smoke_unknown_instance(session)
            return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the live multi-instance MCP smoke test on Docker Compose."
    )
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--keep-stack", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not COMPOSE_FILE.exists():
        raise FileNotFoundError(COMPOSE_FILE)
    env = compose_env(args.version, args.port)
    run(compose_cmd("down", "-v", "--remove-orphans"), env=env, check=False)
    config_path: str | None = None
    try:
        run(compose_cmd("up", "-d", "db"), env=env, timeout=300, attempts=3)
        run(compose_cmd("pull", "odoo"), env=env, timeout=900, attempts=3)
        for database in INSTANCE_DBS.values():
            init_database(database, env)
        bot_uid = create_bot_user(INSTANCE_DBS["acme"], env)
        run(compose_cmd("up", "-d", "odoo"), env=env, timeout=300)
        wait_for_http(args.port, args.timeout)
        for database in INSTANCE_DBS.values():
            wait_for_xmlrpc(args.port, database, ADMIN_LOGIN, ADMIN_PASSWORD, args.timeout)
        wait_for_xmlrpc(
            args.port, INSTANCE_DBS["acme"], BOT_LOGIN, BOT_PASSWORD, args.timeout
        )
        config_path = write_instances_config(args.port)
        report = asyncio.run(run_mcp_smoke(config_path))
        report["bot_uid"] = bot_uid
        report["status"] = "passed"
        print("\n=== multi-instance smoke summary ===")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"\nFAILED: {exc!r}", file=sys.stderr, flush=True)
        return 1
    finally:
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass
        if not args.keep_stack:
            run(compose_cmd("down", "-v", "--remove-orphans"), env=env, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
