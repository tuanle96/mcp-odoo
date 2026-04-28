#!/usr/bin/env python3
"""
Boot disposable Odoo Docker Compose stacks and run live XML-RPC + MCP smoke tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xmlrpc.client
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import AnyUrl, TextContent

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.integration.yml"
DEFAULT_VERSIONS = ("16.0", "17.0", "18.0", "19.0")
DB_USER = "odoo"
DB_PASSWORD = "odoo"
TEST_DB_PREFIX = "mcp_smoke"
ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "admin"


@dataclass(frozen=True)
class VersionTarget:
    version: str
    project: str
    port: int
    mcp_port: int
    database: str


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
    last_error: subprocess.CalledProcessError | subprocess.TimeoutExpired | None = None
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
            stdout = getattr(exc, "stdout", None)
            stderr = getattr(exc, "stderr", None)
            if stdout:
                print(str(stdout), file=sys.stderr, flush=True)
            if stderr:
                print(str(stderr), file=sys.stderr, flush=True)
            if attempt == attempts or not check:
                raise
            print(
                f"Command failed: {exc!r}; retrying in {retry_delay_seconds}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(retry_delay_seconds)

    raise AssertionError(f"unreachable retry state: {last_error!r}")


def compose_env(target: VersionTarget) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ODOO_VERSION": target.version,
            "ODOO_PORT": str(target.port),
            "COMPOSE_PROJECT_NAME": target.project,
        }
    )
    return env


def compose_cmd(target: VersionTarget, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--project-name",
        target.project,
        *args,
    ]


def make_target(version: str, index: int) -> VersionTarget:
    version_slug = "".join(ch if ch.isalnum() else "" for ch in version)
    return VersionTarget(
        version=version,
        project=f"mcp-odoo-smoke-{version_slug}",
        port=18069 + index,
        mcp_port=19069 + index,
        database=f"{TEST_DB_PREFIX}_{version_slug}",
    )


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


def wait_for_xmlrpc(target: VersionTarget, timeout_seconds: int) -> int:
    deadline = time.monotonic() + timeout_seconds
    common = xmlrpc.client.ServerProxy(
        f"http://127.0.0.1:{target.port}/xmlrpc/2/common", allow_none=True
    )
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            uid = common.authenticate(target.database, ADMIN_LOGIN, ADMIN_PASSWORD, {})
            if isinstance(uid, int) and uid > 0:
                return uid
        except (ConnectionError, OSError, xmlrpc.client.Error) as exc:
            last_error = exc
        time.sleep(2)
    raise TimeoutError(
        f"XML-RPC auth did not become ready for {target.version}: {last_error}"
    )


def init_database(target: VersionTarget, env: dict[str, str]) -> None:
    run(
        compose_cmd(
            target,
            "run",
            "--rm",
            "odoo",
            "--stop-after-init",
            "-d",
            target.database,
            "-i",
            "base",
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


def direct_xmlrpc_smoke(target: VersionTarget, uid: int) -> dict[str, Any]:
    models = xmlrpc.client.ServerProxy(
        f"http://127.0.0.1:{target.port}/xmlrpc/2/object", allow_none=True
    )
    partners = models.execute_kw(
        target.database,
        uid,
        ADMIN_PASSWORD,
        "res.partner",
        "search_read",
        [[["id", ">", 0]]],
        {"fields": ["id", "name"], "limit": 1},
    )
    model_info = models.execute_kw(
        target.database,
        uid,
        ADMIN_PASSWORD,
        "ir.model",
        "search_read",
        [[["model", "=", "res.partner"]]],
        {"fields": ["name", "model"], "limit": 1},
    )
    if not isinstance(partners, list):
        raise AssertionError("res.partner search_read did not return a list")
    if not isinstance(model_info, list) or not model_info:
        raise AssertionError("ir.model lookup did not return a list")
    model_record = model_info[0]
    if not isinstance(model_record, dict):
        raise AssertionError("ir.model lookup did not return a dict record")
    model_record = cast(dict[str, Any], model_record)
    if model_record.get("model") != "res.partner":
        raise AssertionError("ir.model lookup did not return res.partner")
    return {
        "partner_count_sample": len(partners),
        "model_name": str(model_record["name"]),
    }


def generate_json2_api_key(target: VersionTarget, env: dict[str, str]) -> str:
    """Create a disposable Odoo API key for JSON-2 smoke testing."""
    script = """
admin = env.ref("base.user_admin")
key = env["res.users.apikeys"].sudo().with_user(admin)._generate(
    None,
    "mcp-odoo json2 smoke",
    None,
)
env.cr.commit()
print("APIKEY=" + key)
"""
    completed = run(
        compose_cmd(
            target,
            "run",
            "--rm",
            "-T",
            "odoo",
            "odoo",
            "shell",
            "-d",
            target.database,
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
        if line.startswith("APIKEY="):
            return line.removeprefix("APIKEY=").strip()
    raise AssertionError("API key generation did not print APIKEY=<key>")


def direct_json2_smoke(target: VersionTarget, api_key: str) -> dict[str, Any]:
    """Validate Odoo 19 External JSON-2 directly, without MCP in the middle."""
    endpoint = f"http://127.0.0.1:{target.port}/json/2/res.partner/search_read"
    body = json.dumps(
        {"domain": [["id", ">", 0]], "fields": ["id", "name"], "limit": 1}
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"JSON-2 HTTP {exc.code}: {error_body}") from exc

    if not isinstance(payload, list):
        raise AssertionError(f"JSON-2 search_read did not return a list: {payload!r}")
    return {"partner_count_sample": len(payload)}


def mcp_env(
    target: VersionTarget, *, transport: str = "xmlrpc", api_key: str | None = None
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ODOO_URL": f"http://127.0.0.1:{target.port}",
            "ODOO_DB": target.database,
            "ODOO_USERNAME": ADMIN_LOGIN,
            "ODOO_PASSWORD": ADMIN_PASSWORD,
            "ODOO_TRANSPORT": transport,
            "ODOO_TIMEOUT": "30",
            "ODOO_VERIFY_SSL": "1",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    if api_key:
        env["ODOO_API_KEY"] = api_key
    return env


def assert_tool_surface(tool_names: set[str]) -> None:
    expected_tools = {
        "execute_method",
        "list_models",
        "get_model_fields",
        "search_records",
        "read_record",
        "search_employee",
        "search_holidays",
        "diagnose_odoo_call",
        "inspect_model_relationships",
        "generate_json2_payload",
        "upgrade_risk_report",
        "fit_gap_report",
    }
    if not expected_tools <= tool_names:
        raise AssertionError(f"Missing MCP tools: {expected_tools - tool_names}")


def decode_tool_json(result: Any, tool_name: str) -> dict[str, Any]:
    content = result.content[0]
    if not isinstance(content, TextContent):
        raise AssertionError(f"MCP {tool_name} did not return text content")
    payload = json.loads(content.text)
    if not isinstance(payload, dict):
        raise AssertionError(f"MCP {tool_name} did not return a JSON object")
    return cast(dict[str, Any], payload)


def parse_inspector_json(stdout: str) -> dict[str, Any]:
    """Parse Inspector JSON even if npm prints non-JSON noise around it."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise AssertionError(f"Inspector did not print JSON: {stdout!r}") from None
        payload = json.loads(stdout[start : end + 1])
    if not isinstance(payload, dict):
        raise AssertionError(f"Inspector JSON was not an object: {payload!r}")
    return payload


async def mcp_stdio_smoke(
    target: VersionTarget, *, transport: str = "xmlrpc", api_key: str | None = None
) -> dict[str, Any]:
    env = mcp_env(target, transport=transport, api_key=api_key)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "odoo_mcp"],
        env=env,
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert_tool_surface(tool_names)

            resources = await session.list_resources()
            resource_uris = {str(resource.uri) for resource in resources.resources}
            if "odoo://models" not in resource_uris:
                raise AssertionError("Missing odoo://models resource")

            templates = await session.list_resource_templates()
            template_uris = {
                str(template.uriTemplate) for template in templates.resourceTemplates
            }
            expected_templates = {
                "odoo://model/{model_name}",
                "odoo://record/{model_name}/{record_id}",
                "odoo://search/{model_name}/{domain}",
            }
            if not expected_templates <= template_uris:
                raise AssertionError(
                    f"Missing MCP resource templates: {expected_templates - template_uris}"
                )

            models_resource = await session.read_resource(AnyUrl("odoo://models"))
            if not models_resource.contents:
                raise AssertionError("odoo://models returned no content")

            result = await session.call_tool(
                "execute_method",
                arguments={
                    "model": "res.partner",
                    "method": "search_read",
                    "args": [[["id", ">", 0]]],
                    "kwargs": {"fields": ["id", "name"], "limit": 1},
                },
            )
            payload = decode_tool_json(result, "execute_method")
            if not payload.get("success"):
                raise AssertionError(f"MCP execute_method failed: {payload}")
            payload_result = payload.get("result")
            if not isinstance(payload_result, list):
                raise AssertionError("MCP execute_method did not return list result")

            json2_preview = decode_tool_json(
                await session.call_tool(
                    "generate_json2_payload",
                    arguments={
                        "model": "res.partner",
                        "method": "search_read",
                        "args": [[["id", ">", 0]]],
                        "kwargs": {"fields": ["id", "name"], "limit": 1},
                        "database": target.database,
                    },
                ),
                "generate_json2_payload",
            )
            if json2_preview.get("body", {}).get("domain") != [["id", ">", 0]]:
                raise AssertionError(f"Bad JSON-2 preview body: {json2_preview}")
            if (
                json2_preview.get("headers", {}).get("X-Odoo-Database")
                != target.database
            ):
                raise AssertionError(f"Missing JSON-2 database header: {json2_preview}")

            diagnosis = decode_tool_json(
                await session.call_tool(
                    "diagnose_odoo_call",
                    arguments={
                        "model": "res.partner",
                        "method": "write",
                        "args": [[1], {"name": "Smoke"}],
                    },
                ),
                "diagnose_odoo_call",
            )
            if diagnosis.get("classification", {}).get("safety") != "destructive":
                raise AssertionError(f"write call was not marked destructive: {diagnosis}")

            relationships = decode_tool_json(
                await session.call_tool(
                    "inspect_model_relationships",
                    arguments={"model": "res.partner"},
                ),
                "inspect_model_relationships",
            )
            if "many2one" not in relationships.get("relationships", {}):
                raise AssertionError(f"Missing relationship groups: {relationships}")

            upgrade = decode_tool_json(
                await session.call_tool(
                    "upgrade_risk_report",
                    arguments={
                        "source_version": target.version,
                        "target_version": "20.0",
                        "methods": [{"model": "res.partner", "method": "write"}],
                    },
                ),
                "upgrade_risk_report",
            )
            if (
                upgrade.get("transport", {}).get("xmlrpc_jsonrpc_deprecation")
                != "Odoo 20 fall 2026"
            ):
                raise AssertionError(f"Bad upgrade risk transport info: {upgrade}")

            fit_gap = decode_tool_json(
                await session.call_tool(
                    "fit_gap_report",
                    arguments={"requirements": ["Track contacts"]},
                ),
                "fit_gap_report",
            )
            classifications = {
                item.get("classification") for item in fit_gap.get("items", [])
            }
            if not classifications <= {
                "standard",
                "configuration",
                "studio",
                "custom_module",
                "avoid",
                "unknown",
            }:
                raise AssertionError(f"Bad fit/gap classifications: {fit_gap}")
            return {
                "transport": transport,
                "tools": sorted(tool_names),
                "resource_count": len(resource_uris),
                "resource_template_count": len(template_uris),
                "mcp_partner_sample_count": len(payload_result),
                "diagnostic_tools_smoke": True,
            }


async def mcp_streamable_http_smoke(
    target: VersionTarget,
    *,
    transport: str = "xmlrpc",
    api_key: str | None = None,
    inspector_smoke: bool = False,
) -> dict[str, Any]:
    env = mcp_env(target, transport=transport, api_key=api_key)
    env.update(
        {
            "MCP_TRANSPORT": "streamable-http",
            "MCP_HTTP_HOST": "127.0.0.1",
            "MCP_HTTP_PORT": str(target.mcp_port),
            "MCP_HTTP_PATH": "/mcp",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "odoo_mcp",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(target.mcp_port),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        url = f"http://127.0.0.1:{target.mcp_port}/mcp"
        deadline = time.monotonic() + 60
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                async with streamable_http_client(url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        tool_names = {tool.name for tool in tools.tools}
                        assert_tool_surface(tool_names)
                        result = await session.call_tool(
                            "search_records",
                            arguments={
                                "model": "res.partner",
                                "domain": [["id", ">", 0]],
                                "fields": ["id", "name"],
                                "limit": 1,
                            },
                        )
                        content = result.content[0]
                        if not isinstance(content, TextContent):
                            raise AssertionError(
                                "MCP HTTP search_records did not return text content"
                            )
                        payload = json.loads(content.text)
                        if not payload.get("success"):
                            raise AssertionError(
                                f"MCP HTTP search_records failed: {payload}"
                            )
                        inspector_result = (
                            run_inspector_http_tools_list(target)
                            if inspector_smoke
                            else None
                        )
                        response: dict[str, Any] = {
                            "transport": "streamable-http",
                            "odoo_transport": transport,
                            "tools": sorted(tool_names),
                            "http_partner_sample_count": payload.get("count"),
                        }
                        if inspector_result:
                            response["inspector_http"] = inspector_result
                        return response
            except Exception as exc:
                last_error = exc
                if process.poll() is not None:
                    _, stderr = process.communicate(timeout=5)
                    raise AssertionError(
                        f"MCP HTTP server exited early with {process.returncode}: {stderr}"
                    ) from exc
                await asyncio.sleep(1)
        raise TimeoutError(f"MCP HTTP smoke did not become ready: {last_error}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=10)


def run_inspector_stdio_tools_list(
    target: VersionTarget, *, transport: str = "xmlrpc", api_key: str | None = None
) -> dict[str, Any]:
    env = mcp_env(target, transport=transport, api_key=api_key)
    completed = run(
        [
            "npx",
            "--yes",
            "@modelcontextprotocol/inspector",
            "--cli",
            "--method",
            "tools/list",
            "--",
            sys.executable,
            "-m",
            "odoo_mcp",
        ],
        env=env,
        timeout=120,
        capture_output=True,
    )
    payload = parse_inspector_json(completed.stdout)
    tool_names = {tool["name"] for tool in payload.get("tools", [])}
    assert_tool_surface(tool_names)
    return {"transport": "stdio", "tool_count": len(tool_names)}


def run_inspector_http_tools_list(target: VersionTarget) -> dict[str, Any]:
    completed = run(
        [
            "npx",
            "--yes",
            "@modelcontextprotocol/inspector",
            "--cli",
            f"http://127.0.0.1:{target.mcp_port}/mcp",
            "--method",
            "tools/list",
        ],
        env=os.environ.copy(),
        timeout=120,
        capture_output=True,
    )
    payload = parse_inspector_json(completed.stdout)
    tool_names = {tool["name"] for tool in payload.get("tools", [])}
    assert_tool_surface(tool_names)
    return {"transport": "streamable-http", "tool_count": len(tool_names)}


def smoke_one(
    target: VersionTarget,
    keep_stack: bool,
    timeout_seconds: int,
    inspector_smoke: bool,
    http_smoke: bool,
) -> dict[str, Any]:
    env = compose_env(target)
    run(compose_cmd(target, "down", "-v", "--remove-orphans"), env=env, check=False)
    try:
        run(compose_cmd(target, "up", "-d", "db"), env=env, timeout=300, attempts=3)
        run(compose_cmd(target, "pull", "odoo"), env=env, timeout=900, attempts=3)
        init_database(target, env)
        json2_api_key = (
            generate_json2_api_key(target, env)
            if target.version.startswith("19.")
            else None
        )
        run(compose_cmd(target, "up", "-d", "odoo"), env=env, timeout=300)
        wait_for_http(target.port, timeout_seconds)
        uid = wait_for_xmlrpc(target, timeout_seconds)
        direct = direct_xmlrpc_smoke(target, uid)
        direct_json2 = (
            direct_json2_smoke(target, json2_api_key) if json2_api_key else None
        )
        mcp_result = asyncio.run(mcp_stdio_smoke(target, transport="xmlrpc"))
        mcp_json2_result = (
            asyncio.run(
                mcp_stdio_smoke(target, transport="json2", api_key=json2_api_key)
            )
            if json2_api_key
            else None
        )
        mcp_http_result = (
            asyncio.run(
                mcp_streamable_http_smoke(
                    target,
                    transport="json2" if json2_api_key else "xmlrpc",
                    api_key=json2_api_key,
                    inspector_smoke=inspector_smoke,
                )
            )
            if http_smoke
            else None
        )
        inspector_stdio_result = (
            run_inspector_stdio_tools_list(
                target,
                transport="json2" if json2_api_key else "xmlrpc",
                api_key=json2_api_key,
            )
            if inspector_smoke
            else None
        )
        result = {
            "version": target.version,
            "project": target.project,
            "port": target.port,
            "mcp_port": target.mcp_port,
            "database": target.database,
            "uid": uid,
            "direct_xmlrpc": direct,
            "mcp_stdio": mcp_result,
            "status": "passed",
        }
        if direct_json2:
            result["direct_json2"] = direct_json2
        if mcp_json2_result:
            result["mcp_stdio_json2"] = mcp_json2_result
        if mcp_http_result:
            result["mcp_streamable_http"] = mcp_http_result
        if inspector_stdio_result:
            result["inspector_stdio"] = inspector_stdio_result
        return result
    finally:
        if not keep_stack:
            run(
                compose_cmd(target, "down", "-v", "--remove-orphans"),
                env=env,
                check=False,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live Docker Compose Odoo smoke tests across versions."
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        default=list(DEFAULT_VERSIONS),
        help="Odoo Docker tags to test, default: %(default)s",
    )
    parser.add_argument(
        "--keep-stack",
        action="store_true",
        help="Keep Compose services and volumes after the run for debugging.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="Readiness timeout per Odoo service in seconds.",
    )
    parser.add_argument(
        "--skip-http-smoke",
        action="store_true",
        help="Skip Streamable HTTP MCP smoke. By default Odoo 19.0 includes it.",
    )
    parser.add_argument(
        "--inspector-smoke",
        action="store_true",
        help="Run MCP Inspector CLI tools/list checks in addition to Python SDK smoke.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not COMPOSE_FILE.exists():
        raise FileNotFoundError(COMPOSE_FILE)

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for index, version in enumerate(args.versions):
        target = make_target(version, index)
        print(f"\n=== Odoo {version} real smoke ===", flush=True)
        try:
            http_smoke = version.startswith("19.") and not args.skip_http_smoke
            result = smoke_one(
                target,
                args.keep_stack,
                args.timeout,
                args.inspector_smoke,
                http_smoke,
            )
            results.append(result)
            print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        except Exception as exc:
            failures.append({"version": version, "error": repr(exc)})
            print(f"FAILED {version}: {exc!r}", file=sys.stderr, flush=True)
            if args.keep_stack:
                print(
                    f"Stack kept for debugging: COMPOSE_PROJECT_NAME={target.project}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                run(
                    compose_cmd(target, "down", "-v", "--remove-orphans"),
                    env=compose_env(target),
                    check=False,
                )

    summary = {"passed": results, "failed": failures}
    print("\n=== smoke summary ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
