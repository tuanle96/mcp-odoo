"""LangGraph + odoo-mcp via langchain-mcp-adapters.

Prerequisites:
  1. pip install -r requirements.txt   (langchain-mcp-adapters >= 0.2.2)
  2. export OPENAI_API_KEY=...
  3. Start the MCP server in another terminal:
       odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

Run:
  python odoo_agent.py

Any OpenAI-compatible provider works — e.g. DeepSeek:
  export OPENAI_BASE_URL=https://api.deepseek.com
  export OPENAI_MODEL=deepseek-chat
"""

import asyncio
import os

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient


async def main() -> None:
    client = MultiServerMCPClient(
        {
            "odoo": {
                "transport": "http",
                "url": "http://localhost:8000/mcp",
            },
        }
    )
    tools = await client.get_tools()
    model_name = os.environ.get("OPENAI_MODEL", "gpt-4.1")
    agent = create_agent(f"openai:{model_name}", tools)
    out = await agent.ainvoke(
        {
            "messages": (
                "List up to 5 sale orders in draft state. If sale.order is not "
                "readable, diagnose why with diagnose_access."
            )
        }
    )
    print(out["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
