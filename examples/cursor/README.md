# Cursor + odoo-mcp

Two files, both copied into your project:

| Example file | Copy to | Purpose |
| --- | --- | --- |
| [`mcp.json`](./mcp.json) | `.cursor/mcp.json` | Registers the odoo-mcp server (stdio and/or HTTP) |
| [`rules/odoo.mdc`](./rules/odoo.mdc) | `.cursor/rules/odoo.mdc` | Teaches the agent safe Odoo conventions |

> `.cursorrules` (root-level file) is **deprecated** — use `.cursor/rules/*.mdc`
> with frontmatter, as shipped here.

## Setup

1. Copy the files as shown above.
2. Either export `ODOO_URL` / `ODOO_DB` / `ODOO_USERNAME` / `ODOO_PASSWORD`
   in your shell (the stdio entry reads them via `${env:...}`), or start the
   HTTP transport yourself and keep only the `odoo-http` entry:

   ```bash
   odoo-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
   ```

3. Open Cursor Settings → MCP and confirm the `odoo` server shows its tools.

## Try it

> "Show me 5 customers from Spain with their emails."
>
> "Why can't I see sale orders? Here is the error: …" (the rule steers the
> agent to `diagnose_access` with `observed_error`)

## Multiple instances

With a multi-instance `odoo_config.json`, ask naturally:

> "Using the `client_a` instance, list draft invoices."

The rules file reminds the agent to pass the `instance` argument.

---
Last verified: 2026-06-10 against Cursor MCP config + rules spec (stable since mid-2025).
