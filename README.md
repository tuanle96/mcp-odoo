# Odoo MCP

<p align="center">
  <strong>A precise MCP bridge for Odoo.</strong><br>
  Give AI agents a safe, typed, and testable way to read, inspect, diagnose, and operate Odoo.
</p>

<p align="center">
  <a href="https://pypi.org/project/odoo-mcp/"><img alt="PyPI" src="https://img.shields.io/pypi/v/odoo-mcp.svg"></a>
  <a href="https://pypi.org/project/odoo-mcp/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/odoo-mcp.svg"></a>
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-black.svg"></a>
</p>

Odoo MCP turns an Odoo database into a Model Context Protocol server. It is built for local agents, IDEs, and automation tools that need real Odoo context without hand-rolled scripts or unsafe direct write access.

It speaks XML-RPC for Odoo 16-18 and External JSON-2 for Odoo 19. It exposes a compact MCP surface with read tools, diagnostics, schema discovery, migration helpers, local addon scanning, and a gated write workflow.

## Highlights

| Capability | What it gives you |
| --- | --- |
| 21 MCP tools | Read records, inspect schema, build domains, scan addons, diagnose calls, and validate writes. |
| 5 agent prompts | Reusable workflows for failed calls, fit/gap workshops, JSON-2 migration, safe writes, and module audits. |
| Odoo 16-19 coverage | XML-RPC by default, JSON-2 opt-in for Odoo 19. |
| Streamable HTTP | Local HTTP/SSE support for clients that do not use stdio. |
| Safe writes | Direct `create`, `write`, and `unlink` are blocked; approved writes require live metadata, a same-session token, explicit confirmation, and an env gate. |
| Real smoke tests | Docker Compose validation boots disposable Odoo 16.0, 17.0, 18.0, and 19.0 stacks. |

## Install

```bash
pip install odoo-mcp
```

For local development:

```bash
git clone https://github.com/tuanle96/mcp-odoo.git
cd mcp-odoo
uv sync --extra dev
```

## Configure

Set connection values in the environment:

```bash
export ODOO_URL="https://your-odoo-instance.com"
export ODOO_DB="your-database"
export ODOO_USERNAME="your-user"
export ODOO_PASSWORD="your-password-or-api-key"
export ODOO_TRANSPORT="xmlrpc"
```

For Odoo 19 JSON-2:

```bash
export ODOO_TRANSPORT="json2"
export ODOO_API_KEY="your-odoo-api-key"
export ODOO_JSON2_DATABASE_HEADER="1"
```

`ODOO_JSON2_DATABASE_HEADER=1` sends `X-Odoo-Database` on JSON-2 calls. Set it to `0` only when host or dbfilter routing already selects the intended database.

You can also use `odoo_config.json`:

```json
{
  "url": "https://your-odoo-instance.com",
  "db": "your-database",
  "username": "your-user",
  "password": "your-password-or-api-key"
}
```

## Run

Start the MCP server over stdio:

```bash
odoo-mcp
```

or:

```bash
python -m odoo_mcp
```

Start Streamable HTTP for local clients:

```bash
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

Non-local HTTP binds are rejected unless you pass `--allow-remote-http` or set `MCP_ALLOW_REMOTE_HTTP=1`. This server does not include built-in HTTP authentication. Put remote HTTP deployments behind your own authentication, TLS, and network policy.

Check runtime posture without starting the server loop:

```bash
odoo-mcp --health
```

## MCP Tools

| Tool | Purpose |
| --- | --- |
| `execute_method` | Execute a reviewed model method. Direct `create`, `write`, and `unlink` are blocked. Unknown side-effect methods require `ODOO_MCP_ALLOW_UNKNOWN_METHODS=1`. |
| `list_models` | List Odoo model technical names and labels. |
| `get_model_fields` | Read field metadata for one model. |
| `search_records` | Run bounded read-only `search_read`. |
| `read_record` | Read one record by model and ID. |
| `search_employee` | Search employees by name. |
| `search_holidays` | Search leave records by date range. |
| `diagnose_odoo_call` | Diagnose a model call without executing it. |
| `inspect_model_relationships` | Group relationship fields, required fields, and create/write hints. |
| `generate_json2_payload` | Convert XML-RPC-shaped input into JSON-2 endpoint, headers, and named body. |
| `upgrade_risk_report` | Surface transport, method, and migration risks across Odoo versions. |
| `fit_gap_report` | Classify requirements into standard, configuration, Studio, custom module, avoid, or unknown. |
| `get_odoo_profile` | Read server version, user context, transport, database, and installed module summary. |
| `schema_catalog` | Build a bounded model catalog with optional field metadata. |
| `preview_write` | Produce a non-executing approval payload for `create`, `write`, or `unlink`. |
| `validate_write` | Validate a write payload against trusted live `fields_get` metadata. |
| `execute_approved_write` | Execute only a same-session, live-validated, confirmed write when `ODOO_MCP_ENABLE_WRITES=1`. |
| `scan_addons_source` | Scan local addon source without importing addon code. |
| `build_domain` | Build and validate an Odoo domain from structured conditions. |
| `business_pack_report` | Report expected modules, models, and discovery calls for sales, CRM, inventory, accounting, or HR. |
| `health_check` | Report non-secret MCP runtime posture. |

## Resources

| URI | Description |
| --- | --- |
| `odoo://models` | List available models. |
| `odoo://model/{model_name}` | Read model metadata and fields. |
| `odoo://record/{model_name}/{record_id}` | Read one record. |
| `odoo://search/{model_name}/{domain}` | Search records with a bounded domain. |

## Prompts

| Prompt | Use it for |
| --- | --- |
| `diagnose_failed_odoo_call` | Root-cause a failing Odoo call before retrying. |
| `fit_gap_workshop` | Turn raw requirements into Odoo fit/gap buckets. |
| `json2_migration_plan` | Plan XML-RPC or JSON-RPC migration to External JSON-2. |
| `safe_write_review` | Review a proposed `create`, `write`, or `unlink`. |
| `custom_module_audit` | Audit local addon source with scan, risk, and business evidence. |

## Safe Write Model

Writes are intentionally boring.

1. `preview_write` creates a canonical, non-executing payload.
2. `validate_write` checks model metadata, required fields, readonly fields, relation hints, record IDs, and payload shape.
3. `execute_approved_write` runs only when all gates pass:
   - the approval came from `validate_write` in the same server process,
   - validation used trusted, non-empty live Odoo `fields_get` metadata,
   - the token has not expired or been consumed,
   - `confirm=true` is passed,
   - `ODOO_MCP_ENABLE_WRITES=1` is set.

Odoo access rules, record rules, and server-side constraints still decide the final result.

## Client Setup

Claude Desktop on macOS reads MCP configuration from:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Use an absolute Python path because GUI apps may not inherit your shell `PATH`:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "/opt/homebrew/bin/python3",
      "args": ["-m", "odoo_mcp"],
      "env": {
        "ODOO_URL": "https://your-odoo-instance.com",
        "ODOO_DB": "your-database",
        "ODOO_USERNAME": "your-user",
        "ODOO_PASSWORD": "your-password-or-api-key",
        "ODOO_TRANSPORT": "xmlrpc"
      }
    }
  }
}
```

More examples are in [docs/client-configs.md](./docs/client-configs.md).

## Docker

Build the image:

```bash
docker build -t mcp/odoo:latest -f Dockerfile .
```

Run over stdio from an MCP client:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "ODOO_URL",
        "-e", "ODOO_DB",
        "-e", "ODOO_USERNAME",
        "-e", "ODOO_PASSWORD",
        "-e", "ODOO_TRANSPORT",
        "-e", "ODOO_API_KEY",
        "mcp/odoo:latest"
      ]
    }
  }
}
```

Run Streamable HTTP locally:

```bash
docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -e ODOO_URL \
  -e ODOO_DB \
  -e ODOO_USERNAME \
  -e ODOO_PASSWORD \
  -e ODOO_TRANSPORT \
  -e ODOO_API_KEY \
  mcp/odoo:latest \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000 \
  --allow-remote-http
```

## Test

Run the normal quality gates:

```bash
uv run python -m ruff check .
uv run python -m mypy src
uv run python -m pytest
```

Run real Odoo smoke tests:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py \
  --versions 16.0 17.0 18.0 19.0 \
  --timeout 360 \
  --inspector-smoke
```

The smoke harness boots disposable Docker Compose stacks, validates direct Odoo access, validates MCP stdio, and for Odoo 19 also validates JSON-2 and Streamable HTTP.

## Compatibility

XML-RPC remains the default transport for broad compatibility. Odoo 19 supports External JSON-2 through `ODOO_TRANSPORT=json2`. Odoo has documented XML-RPC and JSON-RPC deprecation for Odoo 20, so new integrations should plan for JSON-2.

## Contributing

Issues, pull requests, and compatibility reports are welcome. Start with [CONTRIBUTING.md](./CONTRIBUTING.md), include your Odoo version, transport, client type, and the verification you ran.

## Security

Do not publish logs that contain Odoo credentials, API keys, database names from private environments, or full Odoo debug traces. Report vulnerabilities through [SECURITY.md](./SECURITY.md).

## License

MIT. See [LICENSE](./LICENSE).
