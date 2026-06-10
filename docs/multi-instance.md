# Multiple Odoo Instances

One server process can front several named Odoo databases. Shipped in
v0.4.0; this guide covers configuration, routing, and the isolation model.

## Configuration

`odoo_config.json` (or the path in `ODOO_CONFIG_FILE`):

```json
{
  "default": "production",
  "instances": {
    "production": {
      "url": "https://mycompany.odoo.com",
      "db": "mycompany",
      "username": "agent@mycompany.com",
      "password": "api-key-1",
      "timeout": 30,
      "verify_ssl": true
    },
    "staging": {
      "url": "https://staging.mycompany.com",
      "db": "mycompany_staging",
      "username": "agent@mycompany.com",
      "password": "api-key-2",
      "transport": "json2"
    }
  }
}
```

Rules:

- Instance names match `[A-Za-z0-9_-]{1,64}`.
- Each entry is **self-contained** — credentials and transport never
  inherit from another entry. Global env vars only act as fallback
  defaults for `timeout`/`verify_ssl` style keys.
- If all four legacy env vars (`ODOO_URL`/`ODOO_DB`/`ODOO_USERNAME`/
  `ODOO_PASSWORD`) are set, they win and define a single `default`
  instance; a warning is printed when a config file is ignored because of
  this.
- See `odoo_config.multi.json.example` for a copyable template.

## Routing

Every Odoo-facing tool accepts an optional `instance` argument:

```text
search_records(model="sale.order", instance="staging", ...)
```

- Omitted → the `default` instance.
- `list_instances` returns names, URLs, databases, and transports — never
  credentials.
- There is **no HTTP-header-based routing** into the MCP server; the
  `instance` tool argument is the only switch. (`X-Odoo-Database` is a
  different thing: a JSON-2 header between this server and Odoo, controlled
  by `ODOO_JSON2_DATABASE_HEADER`.)
- In prompts, just name the instance: "Using the `staging` instance, …".

## Isolation model

| Mechanism | Guarantee |
| --- | --- |
| Lazy clients | Instances connect on first use; one bad credential does not block others. |
| Approval tokens | Tokens encode the instance; a write validated against `staging` can never execute on `production`. `execute_approved_write` runs on the instance recorded in the approval — no override at execution time. |
| Schema caches | Partitioned per instance (`{instance}:{model}`), bounded by TTL + LRU (`ODOO_MCP_SCHEMA_CACHE_TTL`, `ODOO_MCP_SCHEMA_CACHE_MAX`). |
| Audit log | Each JSONL entry records the instance (see `ODOO_MCP_AUDIT_LOG`). |

## Limitations

- MCP **resources** (`odoo://...`) always use the default instance; tools
  are the multi-instance surface.
- Verified end-to-end by `scripts/odoo_multi_instance_smoke.py` (three
  databases, two accounts, cross-instance token replay rejection).
