"""Interactive setup wizard for ``odoo-mcp --setup``.

Collects connection details, tests them against the live Odoo, writes a
config file the server discovers automatically, and prints ready-to-paste
client snippets (Claude Code, Cursor, Claude Desktop).
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "odoo" / "config.json"


def prompt_value(
    label: str,
    default: str | None = None,
    *,
    secret: bool = False,
    input_func: Any = None,
) -> str:
    """Prompt until a non-empty value (or the default) is supplied."""
    reader = input_func or (getpass.getpass if secret else input)
    suffix = f" [{default}]" if default else ""
    while True:
        raw = str(reader(f"{label}{suffix}: ")).strip()
        if not raw and default is not None:
            return default
        if raw:
            return raw
        print("  A value is required.", file=sys.stderr)


def collect_connection_details(input_func: Any = None) -> dict[str, Any]:
    """Ask for the four connection values plus transport."""
    print("Odoo MCP setup — answer a few questions to generate a config.\n")
    url = prompt_value("Odoo URL (e.g. https://mycompany.odoo.com)", input_func=input_func)
    db = prompt_value("Database name", input_func=input_func)
    username = prompt_value("Username (login email)", input_func=input_func)
    password = prompt_value(
        "API key or password (input hidden)", secret=True, input_func=input_func
    )
    from .odoo_client import normalize_transport

    while True:
        raw_transport = prompt_value(
            "Transport — xmlrpc (Odoo 16+) or json2 (Odoo 19+)",
            default="xmlrpc",
            input_func=input_func,
        )
        try:
            transport = normalize_transport(raw_transport)
            break
        except ValueError:
            print(
                f"  Unknown transport {raw_transport!r}; enter xmlrpc or json2.",
                file=sys.stderr,
            )
    details: dict[str, Any] = {
        "url": url,
        "db": db,
        "username": username,
        "password": password,
    }
    if transport == "json2":
        details["transport"] = "json2"
        details["api_key"] = password
    return details


def test_connection(details: dict[str, Any]) -> tuple[bool, str]:
    """Authenticate against Odoo; returns (ok, human-readable message)."""
    from .odoo_client import OdooClient

    try:
        client = OdooClient(
            url=details["url"],
            db=details["db"],
            username=details["username"],
            password=details["password"],
            transport=str(details.get("transport") or "xmlrpc"),
            api_key=details.get("api_key"),
        )
        info = client.get_server_version()
        version = ""
        if isinstance(info, dict):
            version = str(info.get("server_version") or "")
        return True, f"Connected (Odoo {version or 'version unknown'})."
    except Exception as exc:
        return False, str(exc)


def write_config(details: dict[str, Any], path: Path) -> Path:
    """Write the config file, created with owner-only permissions."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(details, indent=2, sort_keys=True) + "\n"
    # Create with 0600 from the start — the file holds credentials, so it
    # must never exist world-readable, not even between write and chmod.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return path


def client_snippets(config_path: Path) -> str:
    """Ready-to-paste client configuration snippets."""
    env_json = json.dumps(
        {"command": "uvx", "args": ["odoo-mcp"],
         "env": {"ODOO_CONFIG_FILE": str(config_path)}},
        indent=2,
    )
    indented = "\n".join("      " + line for line in env_json.splitlines())
    return f"""
Add the server to your client:

Claude Code:
  claude mcp add odoo --env ODOO_CONFIG_FILE={config_path} -- uvx odoo-mcp

Cursor / Claude Desktop (mcp.json / claude_desktop_config.json):
  {{
    "mcpServers": {{
      "odoo":
{indented}
    }}
  }}

Writes stay disabled until you opt in with ODOO_MCP_ENABLE_WRITES=1.
Keep {config_path} out of version control — it contains credentials.
"""


def run_setup(input_func: Any = None) -> int:
    """Drive the wizard end to end. Returns a process exit code."""
    try:
        details = collect_connection_details(input_func=input_func)
        print("\nTesting connection...", file=sys.stderr)
        ok, message = test_connection(details)
        if ok:
            print(f"✓ {message}")
        else:
            print(f"✗ Connection failed: {message}", file=sys.stderr)
            answer = prompt_value(
                "Save the config anyway? (y/n)", default="n", input_func=input_func
            ).lower()
            if not answer.startswith("y"):
                print("Aborted — nothing written.", file=sys.stderr)
                return 1
        raw_path = prompt_value(
            "Config file path",
            default=str(DEFAULT_CONFIG_PATH),
            input_func=input_func,
        )
        path = write_config(details, Path(raw_path))
        print(f"✓ Wrote {path}")
        print(client_snippets(path))
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled — nothing written.", file=sys.stderr)
        return 1
