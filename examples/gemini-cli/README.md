# Gemini CLI + odoo-mcp

This example shows how to connect `@google/gemini-cli` to `odoo-mcp`, letting you query and manage Odoo data directly from a Gemini CLI session.

## Purpose

`@google/gemini-cli` supports the Model Context Protocol (MCP) as a native tool source. This adapter registers `odoo-mcp` as an MCP server so Gemini CLI can call Odoo tools (searching records, reading fields, creating/updating data, etc.) in response to natural-language prompts.

## Prerequisites

- Node.js 18+ (required by `@google/gemini-cli`)
- `@google/gemini-cli` (installed on demand via `npx`, or globally with `npm install -g @google/gemini-cli`)
- `uv` / `uvx` installed, so `odoo-mcp` can be run without a separate install step
- A reachable Odoo instance (local Docker stack or existing deployment) with API credentials

## Setup

1. Copy the contents of [`settings.json`](./settings.json) into your Gemini CLI settings file:
   - Project-local: `.gemini/settings.json` (applies only within the current project directory)
   - Global: `~/.gemini/settings.json` (applies to all Gemini CLI sessions)

2. If you already have an `mcpServers` block, merge the `odoo` (and/or `odoo-http`) entries into it rather than overwriting the file.
3. Update the placeholder values to match your Odoo instance:
   - `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD` for the stdio (`odoo`) entry
   - `httpUrl` and `Authorization` header for the streamable HTTP (`odoo-http`) entry, if you're running `odoo-mcp` as a standalone HTTP server instead of spawning it via `uvx`

> [!TIP]
> Only keep the entry (or entries) you intend to use — `odoo` and `odoo-http` are two transport options for the same server, not two different servers.

## Try it

Once configured, start a Gemini CLI session and try prompts such as:

> "Show me 5 customers with their emails."
>
> "List the available Odoo tools."

## Multi-instance

To connect to multiple Odoo instances (e.g. staging and production), add additional named entries under `mcpServers`, each with its own environment variables or `httpUrl`:

```json
{
  "mcpServers": {
    "odoo-staging": {
      "command": "uvx",
      "args": ["odoo-mcp"],
      "env": {
        "ODOO_URL": "https://staging.example.com",
        "ODOO_DB": "staging",
        "ODOO_USERNAME": "admin",
        "ODOO_PASSWORD": "..."
      }
    },
    "odoo-prod": {
      "command": "uvx",
      "args": ["odoo-mcp"],
      "env": {
        "ODOO_URL": "https://prod.example.com",
        "ODOO_DB": "prod",
        "ODOO_USERNAME": "admin",
        "ODOO_PASSWORD": "..."
      }
    }
  }
}
```

Gemini CLI will expose tools from each server under its own namespace, so you can address a specific instance by name in your prompts if needed.

## Verification

### Local Odoo stack

Boot a local Odoo instance for testing:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py --keep-stack --versions 18.0
```

### Manual checks

1. Merge the `settings.json` stdio block into `.gemini/settings.json` (or `~/.gemini/settings.json`).
2. Run `npx @google/gemini-cli`.
3. In the CLI session, run `/mcp` and confirm the `odoo` server is listed as connected.
4. Execute a test query, e.g. "Show me 5 customers with their emails." or ask it to list available tools.
5. Confirm the tool calls execute successfully against the live Odoo 18.0 Docker instance.
6. Tear down the compose stack when finished:

```bash
docker compose -f docker-compose.integration.yml --project-name mcp-odoo-smoke-180 down -v
```

---
Last verified: 2026-07-03 against `@google/gemini-cli` MCP implementation.
