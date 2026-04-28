[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/tuanle96-mcp-odoo-badge.png)](https://mseep.ai/app/tuanle96-mcp-odoo)

# Odoo MCP Server

An MCP server implementation that integrates with Odoo ERP systems, enabling AI assistants to interact with Odoo data and functionality through the Model Context Protocol.

## Features

* **Odoo Integration**: Generic method execution plus resource-based model and record access
* **Odoo Transports**: XML-RPC for Odoo 16-18/backward compatibility, plus Odoo 19 External JSON-2 via `/json/2/<model>/<method>`
* **MCP Transports**: `stdio` by default, plus opt-in Streamable HTTP or SSE for remote MCP clients
* **Agent-Friendly Tools**: Safe read-only tools plus diagnostic/report tools for JSON-2 migration, model relationships, upgrade risk, and fit/gap analysis
* **Flexible Configuration**: Support for config files and environment variables
* **Resource Pattern System**: URI-based access to Odoo data structures
* **Error Handling**: Clear error messages for common Odoo API issues
* **Stateless Operations**: Clean request/response cycle for reliable integration

## Tools

The current MCP tool surface has 12 tools: 7 execution/read tools and 5 diagnostic/report tools. Diagnostic/report tools are preview-only unless explicitly documented otherwise; they do not execute candidate Odoo model methods.

* **execute_method**
  * Execute a custom method on an Odoo model
  * Inputs:
    * `model` (string): The model name (e.g., 'res.partner')
    * `method` (string): Method name to execute
    * `args` (optional array): Positional arguments
    * `kwargs` (optional object): Keyword arguments
  * Returns: Dictionary with the method result and success indicator

* **list_models**
  * List Odoo model technical names and display names
  * Inputs:
    * `query` (optional string): Filter by technical model name or display name
    * `limit` (optional number): Maximum number of models to return, capped for safety
  * Returns: Object containing count and matching models

* **get_model_fields**
  * Read field metadata for one model
  * Inputs:
    * `model` (string): Odoo technical model name, for example `res.partner`
    * `field_names` (optional array): Field names to include
  * Returns: Object containing field definitions

* **search_records**
  * Search and read records through bounded read-only `search_read`
  * Inputs:
    * `model` (string): Odoo technical model name
    * `domain` (optional array/object/string): Odoo search domain
    * `fields` (optional array): Field names to read
    * `limit` (optional number): Maximum records to read, capped at 100
    * `offset` (optional number): Search offset
    * `order` (optional string): Odoo order expression
  * Returns: Object containing count and matching records

* **read_record**
  * Read one record by model and ID
  * Inputs:
    * `model` (string): Odoo technical model name
    * `record_id` (number): Record ID
    * `fields` (optional array): Field names to read
  * Returns: Object containing the record or a not-found error

* **search_employee**
  * Search for employees by name
  * Inputs:
    * `name` (string): The name (or part of the name) to search for
    * `limit` (optional number): The maximum number of results to return (default 20)
  * Returns: Object containing success indicator, list of matching employee names and IDs, and any error message

* **search_holidays**
  * Searches for holidays within a specified date range
  * Inputs:
    * `start_date` (string): Start date in YYYY-MM-DD format
    * `end_date` (string): End date in YYYY-MM-DD format
    * `employee_id` (optional number): Optional employee ID to filter holidays
  * Returns: Object containing success indicator, list of holidays found, and any error message

* **diagnose_odoo_call**
  * Diagnose an Odoo model call without executing it
  * Flags read-only, destructive, and unknown-risk methods; highlights JSON-2 named-argument issues; redacts Odoo error `debug` details by default
  * Inputs:
    * `model` (string): Odoo technical model name
    * `method` (string): Odoo method name
    * `args` (optional array): XML-RPC-style positional arguments to diagnose
    * `kwargs` (optional object): Keyword arguments to diagnose
    * `observed_error` (optional string/object): Error text or Odoo-shaped error object
  * Returns: Structured diagnosis, suggested payload, issues, and next actions

* **inspect_model_relationships**
  * Inspect relationship and required-field metadata for one model
  * Uses caller-provided metadata or bounded read-only `fields_get` metadata
  * Inputs:
    * `model` (string): Odoo technical model name
    * `fields_metadata` (optional object): Pre-fetched `fields_get` metadata
    * `use_live_metadata` (optional boolean): Whether to call bounded live `fields_get`
  * Returns: Grouped `many2one`, `one2many`, `many2many`, required fields, and create/write hints

* **generate_json2_payload**
  * Build a JSON-2 endpoint, headers, and named JSON body from XML-RPC-style args or kwargs without network I/O
  * Includes destructive-method warnings, optional `X-Odoo-Database` guidance, and per-call transaction notes
  * Inputs:
    * `model` (string): Odoo technical model name
    * `method` (string): Odoo method name
    * `args` (optional array): XML-RPC-style positional arguments to map
    * `kwargs` (optional object): Named JSON arguments
    * `base_url` (optional string): Odoo base URL for preview output
    * `database` (optional string): Database name for `X-Odoo-Database`
  * Returns: JSON-2 path/URL, headers, named body, warnings, and transaction note

* **upgrade_risk_report**
  * Report migration risks for Odoo version upgrades, deprecated XML-RPC/JSON-RPC usage, JSON-2 named-argument changes, transaction behavior, and destructive methods
  * Inputs:
    * `source_version` / `target_version` (optional strings): Odoo versions
    * `modules`, `methods`, `source_findings`, `observed_errors` (optional arrays): Input evidence
  * Returns: Risk summary, transport risk, destructive methods, and next actions

* **fit_gap_report**
  * Classify requirements as `standard`, `configuration`, `studio`, `custom_module`, `avoid`, or `unknown`
  * Inputs:
    * `requirements` (array): Requirements to classify
    * `available_models`, `available_fields`, `installed_modules`, `business_context` (optional): Evidence for classification
  * Returns: Classification items, evidence, and safe next discovery calls

## Resources

The current MCP resource surface has 4 resource URI patterns:

* **odoo://models**
  * Lists all available models in the Odoo system
  * Returns: JSON array of model information

* **odoo://model/{model_name}**
  * Get information about a specific model including fields
  * Example: `odoo://model/res.partner`
  * Returns: JSON object with model metadata and field definitions

* **odoo://record/{model_name}/{record_id}**
  * Get a specific record by ID
  * Example: `odoo://record/res.partner/1`
  * Returns: JSON object with record data

* **odoo://search/{model_name}/{domain}**
  * Search for records that match a domain
  * Example: `odoo://search/res.partner/[["is_company","=",true]]`
  * Returns: JSON array of matching records (limited to 10 by default)

## Configuration

### Odoo Connection Setup

1. Create a configuration file named `odoo_config.json`:

```json
{
  "url": "https://your-odoo-instance.com",
  "db": "your-database-name",
  "username": "your-username",
  "password": "your-password-or-api-key"
}
```

2. Alternatively, use environment variables:
   * `ODOO_URL`: Your Odoo server URL
   * `ODOO_DB`: Database name
   * `ODOO_USERNAME`: Login username
   * `ODOO_PASSWORD`: Password or API key
   * `ODOO_TRANSPORT`: `xmlrpc` (default) or `json2`
   * `ODOO_API_KEY`: Odoo API key used as the JSON-2 bearer token; if omitted with `ODOO_TRANSPORT=json2`, `ODOO_PASSWORD` is treated as the API key
   * `ODOO_JSON2_DATABASE_HEADER`: `1`/`true` (default) to send `X-Odoo-Database` on JSON-2 requests, or `0`/`false` to rely on host/dbfilter routing
   * `ODOO_TIMEOUT`: Connection timeout in seconds (default: 30)
   * `ODOO_VERIFY_SSL`: Whether to verify SSL certificates (default: true)
   * `HTTP_PROXY`: Force the ODOO connection to use an HTTP proxy

### MCP Transport Setup

The server defaults to `stdio`, which is the most widely supported local MCP transport:

```bash
odoo-mcp
python -m odoo_mcp
```

For clients that support MCP over Streamable HTTP:

```bash
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

Equivalent environment variables:

```bash
export MCP_TRANSPORT=streamable-http
export MCP_HTTP_HOST=127.0.0.1
export MCP_HTTP_PORT=8000
export MCP_HTTP_PATH=/mcp
export MCP_LOG_LEVEL=INFO
```

The default HTTP bind host is `127.0.0.1`. Keep it local unless you put the server behind your own authentication, TLS, and network policy. Odoo credentials and API keys are sensitive.

### Usage with Claude Desktop

On macOS, Claude Desktop reads MCP server configuration from:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Claude Desktop may not inherit the same shell `PATH` you use in Terminal, so prefer an absolute Python path. Find it with:

```bash
which python3
```

Then add this to `claude_desktop_config.json`, replacing `/opt/homebrew/bin/python3` with your actual path if different:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "/opt/homebrew/bin/python3",
      "args": [
        "-m",
        "odoo_mcp"
      ],
      "env": {
        "ODOO_URL": "https://your-odoo-instance.com",
        "ODOO_DB": "your-database-name",
        "ODOO_USERNAME": "your-username",
        "ODOO_PASSWORD": "your-password-or-api-key"
      }
    }
  }
}
```

If you install into a virtual environment, point `command` at that environment's Python binary, for example `/path/to/venv/bin/python`.

### Docker

Build the local image first:

```bash
docker build -t mcp/odoo:latest -f Dockerfile .
```

Then configure Claude Desktop to run the container over stdio:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "ODOO_URL",
        "-e",
        "ODOO_DB",
        "-e",
        "ODOO_USERNAME",
        "-e",
        "ODOO_PASSWORD",
        "-e",
        "ODOO_TRANSPORT",
        "-e",
        "ODOO_API_KEY",
        "-e",
        "ODOO_TIMEOUT",
        "-e",
        "ODOO_VERIFY_SSL",
        "mcp/odoo:latest"
      ],
      "env": {
        "ODOO_URL": "https://your-odoo-instance.com",
        "ODOO_DB": "your-database-name",
        "ODOO_USERNAME": "your-username",
        "ODOO_PASSWORD": "your-password-or-api-key",
        "ODOO_TRANSPORT": "xmlrpc",
        "ODOO_TIMEOUT": "30",
        "ODOO_VERIFY_SSL": "1"
      }
    }
  }
}
```

The container entrypoint is `odoo-mcp`; it starts the MCP server over stdio using the installed package entry point.

To run the same image over Streamable HTTP for a local MCP client:

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
  --port 8000
```

## Odoo API Compatibility

This package keeps XML-RPC as the default transport for backward compatibility. As of the Odoo 19.0 documentation checked on 2026-04-28, Odoo marks XML-RPC and JSON-RPC endpoints (`/xmlrpc`, `/xmlrpc/2`, and `/jsonrpc`) as deprecated and scheduled for removal in Odoo 20 (fall 2026), with the External JSON-2 API as the replacement: https://www.odoo.com/documentation/19.0/developer/reference/external_api.html

For Odoo 19, enable JSON-2 explicitly:

```bash
export ODOO_TRANSPORT=json2
export ODOO_API_KEY="your-odoo-api-key"
```

JSON-2 uses bearer authentication and named JSON arguments. The client maps common ORM positional calls (`search`, `search_count`, `search_read`, `read`, `write`, `unlink`, `create`, `name_search`, and `fields_get`) to JSON-2 named arguments so the existing MCP tools keep working. For arbitrary custom methods with positional-only XML-RPC style arguments, pass `kwargs` that match the Odoo method signature or keep `ODOO_TRANSPORT=xmlrpc`.

Odoo JSON-2 does not accept a database name in the request body the way XML-RPC does. For multi-database deployments, this server sends the selected database through `X-Odoo-Database` by default. Set `ODOO_JSON2_DATABASE_HEADER=0` only when the Odoo host/dbfilter routing already resolves the intended database.

JSON-2 uses named JSON arguments and each call runs in its own transaction. The diagnostic/report tools surface this difference so agents do not assume XML-RPC-style positional arguments or multi-call transaction boundaries.

When Odoo returns a structured JSON-2 error, the server preserves Odoo-shaped fields such as `name`, `message`, `arguments`, `context`, and `debug`. The `debug` field is redacted by default and is returned only when the caller explicitly opts in.

## Release Verification

Before publishing a compatibility release, verify at least:

```bash
python -m pytest
python -m build
python -m twine check dist/*
python -c "from importlib.metadata import version; assert version('odoo-mcp') == '0.0.4'"
docker build -t mcp/odoo:latest -f Dockerfile .
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py --versions 16.0 17.0 18.0 19.0 --timeout 360 --inspector-smoke
```

The Docker Compose smoke test boots disposable Postgres and official `odoo:<version>` containers, initializes a fresh Odoo database, checks HTTP and XML-RPC readiness, then starts this MCP server and validates `list_tools`, `list_resources`, `list_resource_templates`, `read_resource`, `execute_method`, the typed read-only tools, and the diagnostic/report tools against live Odoo data. For Odoo 19.0 it also generates a disposable API key inside the container and validates direct JSON-2, MCP stdio JSON-2, MCP Streamable HTTP JSON-2, and MCP Inspector `tools/list`.

The GitHub release workflow must keep PyPI upload gated behind successful test, build, and MCP Inspector smoke jobs. For live runtime validation, connect the server to a test Odoo database and confirm the 12 tools and 4 resource URI patterns listed above initialize and can read/search non-sensitive test records.

## Installation

### Python Package

```bash
pip install odoo-mcp
```

### Running the Server

```bash
# Using the installed package
odoo-mcp

# Using the MCP development tools
mcp dev odoo_mcp/server.py

# With additional dependencies
mcp dev odoo_mcp/server.py --with pandas --with numpy

# Mount local code for development
mcp dev odoo_mcp/server.py --with-editable .
```

## Build

Docker build:

```bash
docker build -t mcp/odoo:latest -f Dockerfile .
```

## Parameter Formatting Guidelines

When using the MCP tools for Odoo, pay attention to these parameter formatting guidelines:

1. **Domain Parameter**:
   * The following domain formats are supported:
     * List format: `[["field", "operator", value], ...]`
     * Object format: `{"conditions": [{"field": "...", "operator": "...", "value": "..."}]}`
     * JSON string of either format
   * Examples:
     * List format: `[["is_company", "=", true]]`
     * Object format: `{"conditions": [{"field": "date_order", "operator": ">=", "value": "2025-03-01"}]}`
     * Multiple conditions: `[["date_order", ">=", "2025-03-01"], ["date_order", "<=", "2025-03-31"]]`

2. **Fields Parameter**:
   * Should be an array of field names: `["name", "email", "phone"]`
   * The server will try to parse string inputs as JSON

## License

This MCP server is licensed under the MIT License.
