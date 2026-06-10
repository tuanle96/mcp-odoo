# CrewAI + odoo-mcp

[`odoo_crew.py`](./odoo_crew.py) wires odoo-mcp into a CrewAI agent with the
**native** `mcps=[...]` parameter (available since late 2025).
[`agents.yaml`](./agents.yaml) is the declarative variant.

> Stale-tutorial warning: do **not** wrap MCP tools through
> `langchain-mcp-adapters` for CrewAI — that is 2024 advice. CrewAI speaks
> MCP natively now.

## Run

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

# terminal 1 — the MCP server (set ODOO_* env vars first)
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

# terminal 2
python odoo_crew.py
```

Expected output: the crew run log ending in a markdown table of stale
draft invoices.

## Fallback: MCPServerAdapter

If your pinned CrewAI version predates native `mcps=[...]`, the older
adapter still works:

```python
from crewai_tools import MCPServerAdapter

server_params = {"url": "http://localhost:8000/mcp", "transport": "streamable-http"}
with MCPServerAdapter(server_params) as tools:
    analyst = Agent(role="Odoo Analyst", goal="...", backstory="...", tools=tools)
```

## Gotchas

- CrewAI can swallow MCP connection failures silently — keep
  `cache_tools_list=True` and check the logs if the agent claims it has
  no tools.
- Multi-instance odoo-mcp: tell the agent which instance to use in the
  task description; tools accept an `instance` argument.

---
Last verified: 2026-06-10 against CrewAI native MCP wiring (`mcps=[...]`, late-2025 API); fallback adapter documented for older pins.
