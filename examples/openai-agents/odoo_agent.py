"""OpenAI Agents SDK + odoo-mcp over Streamable HTTP.

Prerequisites:
  1. pip install -r requirements.txt
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

from agents import Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from agents.mcp import MCPServerStreamableHttp
from openai import AsyncOpenAI


def build_model():
    """Model name for OpenAI, or a Chat Completions model for other providers."""
    base_url = os.environ.get("OPENAI_BASE_URL")
    model_name = os.environ.get("OPENAI_MODEL", "gpt-4.1")
    if not base_url:
        return model_name
    # Non-OpenAI provider: tracing uploads would 401 against api.openai.com.
    set_tracing_disabled(True)
    client = AsyncOpenAI(base_url=base_url, api_key=os.environ["OPENAI_API_KEY"])
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)


async def main() -> None:
    async with MCPServerStreamableHttp(
        name="odoo",
        params={"url": "http://localhost:8000/mcp"},
        cache_tools_list=True,
        client_session_timeout_seconds=60,
    ) as server:
        agent = Agent(
            name="OdooBot",
            model=build_model(),
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
            "List up to 5 res.partner contacts, then diagnose whether the "
            "current user can read sale.order.",
        )
        print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
