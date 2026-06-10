"""CrewAI + odoo-mcp using the native mcps=[...] agent wiring.

Prerequisites:
  1. pip install -r requirements.txt
  2. export OPENAI_API_KEY=...   (or configure another CrewAI LLM)
  3. Start the MCP server in another terminal:
       odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

Run:
  python odoo_crew.py

If your pinned CrewAI version does not have native `mcps=[...]` yet, use
the MCPServerAdapter fallback shown in README.md.

Any litellm-supported model works — e.g. DeepSeek:
  export ODOO_MCP_EXAMPLE_LLM=deepseek/deepseek-chat
  export DEEPSEEK_API_KEY=...
"""

import os

from crewai import Agent, Crew, Task
from crewai.mcp import MCPServerHTTP

odoo = MCPServerHTTP(
    url="http://localhost:8000/mcp",
    streamable=True,
    cache_tools_list=True,
)

analyst = Agent(
    role="Odoo Analyst",
    goal="Audit account.move issues for the finance team",
    backstory=(
        "Senior Odoo consultant. Inspects schemas with get_model_fields "
        "before guessing field names and never writes without the gated "
        "preview/validate/execute workflow."
    ),
    mcps=[odoo],
    **(
        {"llm": os.environ["ODOO_MCP_EXAMPLE_LLM"]}
        if os.environ.get("ODOO_MCP_EXAMPLE_LLM")
        else {}
    ),
)

task = Task(
    description=(
        "Find draft customer invoices (account.move, move_type "
        "'out_invoice', state 'draft') older than 30 days."
    ),
    expected_output="A markdown table: invoice name, partner, date, amount.",
    agent=analyst,
)

if __name__ == "__main__":
    result = Crew(agents=[analyst], tasks=[task]).kickoff()
    print(result)
