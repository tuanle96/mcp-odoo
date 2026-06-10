"""OpenAI Agents SDK + odoo-mcp via HostedMCPTool.

The MODEL side (OpenAI Responses API) connects to your MCP server, so the
server URL must be reachable from the internet — use this for deployed
odoo-mcp instances, not localhost.

Constraint: HostedMCPTool only works with OpenAI models on the Responses
API. For local development prefer odoo_agent.py.

Run:
  python hosted_odoo_agent.py
"""

import asyncio

from agents import Agent, HostedMCPTool, Runner


async def main() -> None:
    agent = Agent(
        name="OdooBot",
        instructions=(
            "You answer questions about an Odoo database using the odoo MCP "
            "tools. Inspect schemas before guessing field names."
        ),
        tools=[
            HostedMCPTool(
                tool_config={
                    "type": "mcp",
                    "server_label": "odoo",
                    "server_url": "https://odoo-mcp.example.com/mcp",
                    "require_approval": "never",
                }
            )
        ],
    )
    result = await Runner.run(agent, "List 5 draft sale orders.")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
