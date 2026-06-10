"""OpenAI Agents SDK + odoo-mcp over Streamable HTTP.

Prerequisites:
  1. pip install -r requirements.txt
  2. export OPENAI_API_KEY=...
  3. Start the MCP server in another terminal:
       odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

Run:
  python odoo_agent.py
"""

import asyncio

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp


async def main() -> None:
    async with MCPServerStreamableHttp(
        name="odoo",
        params={"url": "http://localhost:8000/mcp"},
        cache_tools_list=True,
    ) as server:
        agent = Agent(
            name="OdooBot",
            instructions=(
                "You answer questions about an Odoo database using the odoo MCP "
                "tools. Inspect schemas with get_model_fields before guessing "
                "field names. If several Odoo instances are configured, pass the "
                "instance argument on every tool call."
            ),
            mcp_servers=[server],
        )
        result = await Runner.run(
            agent,
            "Show the top 5 res.partner customers, then diagnose whether the "
            "current user can read sale.order.",
        )
        print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
