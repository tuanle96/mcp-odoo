# Support

Use GitHub issues for bugs, compatibility reports, and feature requests.

## Good bug reports

Include:

- Odoo version,
- Odoo transport: XML-RPC or JSON-2,
- MCP transport: stdio, Streamable HTTP, or SSE,
- Python version,
- package version or commit,
- client name and version,
- exact command or MCP client configuration,
- sanitized logs,
- expected behavior,
- actual behavior.

## Compatibility reports

For Odoo compatibility, include the output summary from:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py \
  --versions 16.0 17.0 18.0 19.0 \
  --timeout 360 \
  --inspector-smoke
```

If the issue only happens on a custom Odoo database, include the module list and a minimal model/method reproduction. Do not include private data.

## Security issues

Do not report vulnerabilities in public issues. Follow [SECURITY.md](./SECURITY.md).
