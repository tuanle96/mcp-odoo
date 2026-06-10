# OpenAI Agents SDK + odoo-mcp

Two variants:

| File | Connection | When to use |
| --- | --- | --- |
| [`odoo_agent.py`](./odoo_agent.py) | Your process → local MCP server (`MCPServerStreamableHttp`) | Local development, private networks |
| [`hosted_odoo_agent.py`](./hosted_odoo_agent.py) | OpenAI's model side → your public MCP server (`HostedMCPTool`) | Deployed servers; OpenAI models on the Responses API only |

## Run the local variant

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

# terminal 1 — the MCP server (set ODOO_* env vars first)
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

# terminal 2
python odoo_agent.py
```

Expected output: a short list of partners followed by an access diagnosis
for `sale.order`.

## Notes

- SSE transport is deprecated in the Agents SDK — use Streamable HTTP.
- `cache_tools_list=True` avoids re-listing the 26 tools on every run.
- Running **multiple** MCP servers on one agent? Set
  `include_server_in_tool_names=True` to avoid tool-name collisions.
- stdio variant: replace `MCPServerStreamableHttp` with `MCPServerStdio`
  and `params={"command": "uvx", "args": ["odoo-mcp"], "env": {...}}`.
- Multi-instance odoo-mcp: the agent passes `instance="name"` in tool
  arguments — just say which instance you mean in the prompt.

---
Last verified: 2026-06-10 — ran end-to-end against `openai-agents==0.17.4`, odoo-mcp Streamable HTTP, a live Odoo 19 (Docker), and DeepSeek (`OPENAI_BASE_URL` + `OPENAI_MODEL=deepseek-chat`). HostedMCPTool variant is config-checked only (needs a public server URL).
