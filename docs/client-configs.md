# MCP Client Config Examples

This server is most portable over `stdio`. Use Streamable HTTP when the client
explicitly supports remote MCP servers.

## Local stdio

Generic MCP client JSON:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "/path/to/python",
      "args": ["-m", "odoo_mcp"],
      "env": {
        "ODOO_URL": "https://your-odoo-instance.com",
        "ODOO_DB": "your-database-name",
        "ODOO_USERNAME": "your-username",
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
        "ODOO_DB": "your-database-name",
        "ODOO_USERNAME": "your-username",
        "ODOO_PASSWORD": "legacy-password-if-needed",
        "ODOO_TRANSPORT": "json2",
        "ODOO_API_KEY": "your-odoo-api-key",
        "ODOO_JSON2_DATABASE_HEADER": "1"
      }
    }
  }
}
```

`ODOO_JSON2_DATABASE_HEADER` defaults to `1`, which sends `X-Odoo-Database`
with JSON-2 calls. Set it to `0` only when the Odoo host/dbfilter routing
already selects the intended database.

## Streamable HTTP

Start the server:

```bash
odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

Generic remote MCP client JSON:

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

Client configuration keys vary across IDEs and agent frameworks. If your client
does not support Streamable HTTP, use the `stdio` config above.

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
        "ODOO_JSON2_DATABASE_HEADER",
        "mcp/odoo:latest"
      ],
      "env": {
        "ODOO_URL": "https://your-odoo-instance.com",
        "ODOO_DB": "your-database-name",
        "ODOO_USERNAME": "your-username",
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
  --port 8000
```

Use a reverse proxy or platform gateway for public exposure. Do not expose this
server directly to the internet with Odoo credentials in its environment.
