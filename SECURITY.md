# Security Policy

Odoo MCP connects AI clients to business data. Treat every deployment as sensitive.

## Supported versions

Security fixes target the latest released package and the current `main` branch.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability.

Report privately through GitHub security advisories when available, or contact the maintainer listed in `pyproject.toml`.

Include:

- affected version or commit,
- Odoo version,
- transport in use: XML-RPC, JSON-2, stdio, Streamable HTTP, or SSE,
- minimal reproduction steps,
- expected impact,
- whether credentials, private data, or logs were exposed.

## Sensitive data

Do not publish:

- Odoo passwords,
- Odoo API keys,
- database names from private deployments,
- session cookies,
- full Odoo debug traces from production,
- customer records or employee records.

## Deployment guidance

`stdio` is the safest default because it stays local to the MCP client process.

Streamable HTTP and SSE are available for compatible clients, but this server does not implement built-in HTTP authentication. For any non-local deployment:

- require external authentication,
- terminate TLS at a trusted proxy or platform gateway,
- restrict network access,
- keep audit logs,
- avoid exposing Odoo credentials in shared environments.

`MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` are transport hardening controls. They are not an authentication layer.

## Write execution

Write execution is disabled unless `ODOO_MCP_ENABLE_WRITES=1` is set. Even then, standard writes require a same-session approval token produced by live metadata validation and explicit confirmation.

Do not enable writes for untrusted clients.
