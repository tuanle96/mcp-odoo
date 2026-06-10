# n8n + odoo-mcp

[`workflow-odoo-agent.json`](./workflow-odoo-agent.json) is an importable
workflow: `Manual Trigger → AI Agent` with an OpenAI chat model and the
**official** MCP Client Tool node pointed at odoo-mcp over Streamable HTTP.

> Use the built-in `@n8n/n8n-nodes-langchain.mcpClientTool` node (shipped
> since mid-2025). The community `n8n-nodes-mcp` package is outdated.
> n8n **cloud** does not support stdio MCP servers — HTTP is required.

## Prerequisites

- n8n ≥ 1.60 (self-hosted Docker or cloud)
- odoo-mcp running with the HTTP transport (set `ODOO_*` env vars first):

  ```bash
  odoo-mcp --transport streamable-http --host 0.0.0.0 --port 8000 --path /mcp --allow-remote-http
  ```

  `--allow-remote-http` is needed when n8n runs in Docker and reaches the
  server through `host.docker.internal` — keep the port firewalled to your
  Docker network.

## Import

1. n8n → **Workflows → Import from file** → select the JSON.
2. Open the **OpenAI Chat Model** node and pick your own credential.
3. If n8n does **not** run in Docker, change the MCP endpoint URL from
   `http://host.docker.internal:8000/mcp` to `http://localhost:8000/mcp`.
4. Execute the workflow. The agent lists the odoo-mcp tools, calls
   `search_records`, and falls back to `diagnose_access` on permission
   errors.

## Multi-instance

With a multi-instance `odoo_config.json`, edit the AI Agent prompt:
"Using the `client_a` instance, list 5 draft sale orders." The agent passes
`instance="client_a"` in tool arguments.

---
Last verified: 2026-06-10 against the n8n built-in MCP Client Tool node docs (import re-verification recommended on n8n upgrades — the node UX is still maturing).
