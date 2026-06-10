# LangGraph + odoo-mcp

Uses [`langchain-mcp-adapters`](https://github.com/langchain-ai/langchain-mcp-adapters)
(`MultiServerMCPClient`) to load the odoo-mcp tools and
`langchain.agents.create_agent` to drive them.

> Stale-tutorial warning: pin `langchain-mcp-adapters >= 0.2.2`.
> Versions 0.1.2/0.1.3 were yanked, and older `create_react_agent`
> examples no longer reflect the current API.

## Run

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

# terminal 1 — the MCP server (set ODOO_* env vars first)
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

# terminal 2
python odoo_agent.py
```

Expected output: a short list of draft sale orders (or an access diagnosis
if the credential cannot read them).

## stdio variant

Replace the server entry with:

```python
"odoo": {
    "transport": "stdio",
    "command": "uvx",
    "args": ["odoo-mcp"],
    "env": {
        "ODOO_URL": "...", "ODOO_DB": "...",
        "ODOO_USERNAME": "...", "ODOO_PASSWORD": "...",
    },
},
```

Anti-pattern: do **not** use stdio when deploying on LangGraph Server /
LangGraph Cloud — the platform may spawn multiple workers, each forking its
own odoo-mcp subprocess. Run one Streamable HTTP server instead.

## Multi-instance

With a multi-instance `odoo_config.json`, mention the instance in the
prompt ("using the `client_a` instance, …") — every odoo-mcp tool accepts
an `instance` argument the agent will fill in.

---
Last verified: 2026-06-10 against `langchain-mcp-adapters>=0.2.2` API (`MultiServerMCPClient` + `create_agent`).
