# odoo-mcp plugin example

Minimal third-party tool plugin for [odoo-mcp](https://github.com/erpipe-org/mcp-odoo).

```bash
pip install -e .                       # install next to odoo-mcp
ODOO_MCP_PLUGINS=example odoo-mcp      # opt in by entry-point name
```

The `example_count_records` tool then appears in `tools/list`, and
`health_check.plugins.loaded` shows `["example"]`.

Authoring guide + security model: [docs/plugins.md](../../docs/plugins.md).
