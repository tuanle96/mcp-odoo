# odoo-mcp Client & Framework Examples

Copy-paste-runnable integrations for the most common MCP clients and agent
frameworks. Every example talks to the same `odoo-mcp` server — pick the
transport that matches your client, replace the connection values, run.

## Transport matrix

| Client / framework | stdio | Streamable HTTP | Notes |
| --- | :-: | :-: | --- |
| [Cursor](./cursor/) | ✅ | ✅ | `.cursor/mcp.json` + a rules file |
| Claude Code | ✅ | ✅ | snippet below |
| Codex CLI | ✅ | — | snippet below |
| [OpenAI Agents SDK](./openai-agents/) | ✅ | ✅ | native `MCPServerStreamableHttp`; SSE is deprecated |
| [LangGraph](./langgraph/) | ✅ | ✅ | `langchain-mcp-adapters >= 0.2.2` |
| [CrewAI](./crewai/) | ✅ | ✅ | native `mcps=[...]` on `Agent` |
| [n8n](./n8n/) | ❌ (cloud) | ✅ | official MCP Client Tool node; HTTP only on n8n cloud |

Start the HTTP transport when your client needs it:

```bash
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

stdio needs no flags — clients launch `uvx odoo-mcp` themselves.

## Multiple Odoo instances

When the server is configured with several named instances (see
[Multiple Odoo instances](../README.md#multiple-odoo-instances)), every
Odoo-facing tool accepts an optional `instance` argument. You do not need
client-side headers — just tell the agent which instance to use:

> "Using the `client_a` instance, list 5 draft sale orders."

The agent passes `instance="client_a"` in tool calls; `list_instances`
shows what is available.

## Claude Code

`.mcp.json` in your project root:

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
        "ODOO_PASSWORD": "your-api-key"
      }
    }
  }
}
```

## Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.odoo]
command = "uvx"
args = ["odoo-mcp"]

[mcp_servers.odoo.env]
ODOO_URL = "https://mycompany.odoo.com"
ODOO_DB = "mycompany"
ODOO_USERNAME = "agent@mycompany.com"
ODOO_PASSWORD = "your-api-key"
```

## Security note

These examples are starting points, not production templates. Before
deploying anything write-capable, read the [Security](../SECURITY.md)
policy and the safe-write workflow in the main README — writes stay
disabled unless `ODOO_MCP_ENABLE_WRITES=1` is set.
