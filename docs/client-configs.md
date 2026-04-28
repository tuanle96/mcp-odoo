# Client Configurations

Odoo MCP is most predictable over `stdio`. Use Streamable HTTP only when your client explicitly supports remote MCP servers.

## Local stdio

Generic MCP client configuration:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "/path/to/python",
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

For Odoo 19 JSON-2:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "/path/to/python",
      "args": ["-m", "odoo_mcp"],
      "env": {
        "ODOO_URL": "https://your-odoo-instance.com",
        "ODOO_DB": "your-database",
        "ODOO_USERNAME": "your-user",
        "ODOO_PASSWORD": "legacy-password-if-needed",
        "ODOO_TRANSPORT": "json2",
        "ODOO_API_KEY": "your-odoo-api-key",
        "ODOO_JSON2_DATABASE_HEADER": "1"
      }
    }
  }
}
```

`ODOO_JSON2_DATABASE_HEADER` defaults to `1`. Set it to `0` only when host or dbfilter routing already selects the intended database.

## Claude Desktop

On macOS, Claude Desktop reads this file:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

GUI apps may not inherit your shell `PATH`, so prefer an absolute Python path:

```bash
which python3
```

Example:

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

If you use a virtual environment, set `command` to that environment's Python binary, for example `/path/to/.venv/bin/python`.

## Streamable HTTP

Start the server locally:

```bash
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

Generic remote MCP client:

```json
{
  "mcpServers": {
    "odoo": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Some clients use `transport` instead of `type`:

```json
{
  "mcpServers": {
    "odoo": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Non-local binds require `--allow-remote-http` or `MCP_ALLOW_REMOTE_HTTP=1`. This server does not implement built-in HTTP authentication. `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` harden transport handling, but they are not an auth layer.

For public or shared-network use, put the server behind a reverse proxy or platform gateway that provides authentication, TLS, access logs, and rate limits. Do not expose Odoo credentials through an unauthenticated MCP endpoint.

## Docker stdio

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
        "-e", "ODOO_JSON2_DATABASE_HEADER",
        "mcp/odoo:latest"
      ],
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

## Docker Streamable HTTP

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

## Quick checks

List tools through MCP Inspector over stdio:

```bash
npx --yes @modelcontextprotocol/inspector --cli --method tools/list -- python -m odoo_mcp
```

Check HTTP health posture:

```bash
odoo-mcp --transport streamable-http --health
```
