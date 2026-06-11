# odoo-mcp — installation guide for AI agents

This file is written for AI agents (Claude Code, Cline, Cursor, Codex, and
similar) installing the `odoo-mcp` MCP server on behalf of a user. It is
self-contained — you do not need to read the rest of the repository.

`odoo-mcp` turns any Odoo 16+ database into a Model Context Protocol server
using only existing credentials. No Odoo-side module, no admin access.
Human-oriented documentation: https://github.com/tuanle96/mcp-odoo

## Prerequisites

- Python 3.10+ with `uv`/`uvx` (preferred) or `pip`; or Docker
  (`ghcr.io/tuanle96/mcp-odoo:latest`).
- Credentials for an Odoo 16+ instance: URL, database name, username, and a
  password or API key. Ask the user for these if you do not have them.

## Security rules for agents

1. Treat the Odoo credentials as secrets: never echo, print, or log them in
   chat, command output, or commit history.
2. Do NOT set `ODOO_MCP_ENABLE_WRITES` unless the user explicitly asks to
   enable writes. The server is read-only by default and that is the safe
   posture.
3. Write config files with owner-only permissions where possible, and never
   commit them to version control.

## Configuration contract

Required environment variables (stdio transport):

| Variable | Value |
| --- | --- |
| `ODOO_URL` | Odoo base URL, e.g. `https://mycompany.odoo.com` |
| `ODOO_DB` | Database name |
| `ODOO_USERNAME` | Login user |
| `ODOO_PASSWORD` | Password or API key |

Optional:

- `ODOO_TRANSPORT` — `xmlrpc` (default, Odoo 16+) or `json2` (Odoo 19+,
  set `ODOO_API_KEY` as well).
- `ODOO_CONFIG_FILE=/path/to/config.json` — alternative to env vars; a JSON
  file with the same four keys (`url`, `db`, `username`, `password`), or a
  multi-instance map (see "Multiple Odoo instances" in the README).

## Installation steps

### Claude Code

```bash
claude mcp add odoo \
  --env ODOO_URL=https://mycompany.odoo.com \
  --env ODOO_DB=mycompany \
  --env ODOO_USERNAME=agent@mycompany.com \
  --env ODOO_PASSWORD=the-users-api-key \
  -- uvx odoo-mcp
```

Or write `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uvx",
      "args": ["odoo-mcp"],
      "env": {
        "ODOO_URL": "https://mycompany.odoo.com",
        "ODOO_DB": "mycompany",
        "ODOO_USERNAME": "agent@mycompany.com",
        "ODOO_PASSWORD": "the-users-api-key"
      }
    }
  }
}
```

### Cline (VS Code extension)

Add to Cline's MCP settings (`cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uvx",
      "args": ["odoo-mcp"],
      "env": {
        "ODOO_URL": "https://mycompany.odoo.com",
        "ODOO_DB": "mycompany",
        "ODOO_USERNAME": "agent@mycompany.com",
        "ODOO_PASSWORD": "the-users-api-key"
      }
    }
  }
}
```

### Cursor

Write `.cursor/mcp.json` with the same `mcpServers` JSON shape as Claude Code
above. A fuller example with agent rules lives in
[`examples/cursor/`](./examples/cursor/).

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.odoo]
command = "uvx"
args = ["odoo-mcp"]

[mcp_servers.odoo.env]
ODOO_URL = "https://mycompany.odoo.com"
ODOO_DB = "mycompany"
ODOO_USERNAME = "agent@mycompany.com"
ODOO_PASSWORD = "the-users-api-key"
```

### Claude Desktop

Add the same `mcpServers` JSON shape to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows). GUI apps may not
inherit shell `PATH` — use an absolute path to `uvx` or `python` if launch
fails.

### Any other stdio MCP client

Use `command: "uvx"`, `args: ["odoo-mcp"]`, and the four `ODOO_*` env vars.
If `uvx` is unavailable: `pip install odoo-mcp`, then `command: "odoo-mcp"`
or `python -m odoo_mcp`.

## Verification

Run these in order; stop and troubleshoot on the first failure:

1. `uvx odoo-mcp --health` — must exit 0 and print a JSON posture line.
2. Call the `health_check` MCP tool through the client — confirm the Odoo
   connection is reachable and note the reported tool count.
3. Call the `get_odoo_profile` MCP tool — confirm it returns the server
   version and database name the user expects.

## Enabling writes (only on explicit user request)

Set `ODOO_MCP_ENABLE_WRITES=1` in the server env. Writes still require the
gated workflow — `preview_write` → `validate_write` →
`execute_approved_write` with `confirm=true` — so nothing is mutated without
an approval token. Details: "Safe Write Model" in the README.

## Troubleshooting

- Error-message-to-root-cause map: [`docs/troubleshooting.md`](./docs/troubleshooting.md)
- More client configs (Windsurf, VS Code, Zed, Continue.dev, Streamable HTTP,
  Docker): [`docs/client-configs.md`](./docs/client-configs.md)
- Multi-database setups: "Multiple Odoo instances" in the README.
