# MCP Catalog Submission Notes

This file documents the manual steps for getting `odoo-mcp` listed in the
public MCP server catalogs. The repo ships a prebuilt `smithery.yaml`, a
GHCR Docker workflow, and a publish-to-PyPI workflow — what's left is the
manual registration in each catalog. Smithery in particular requires an
account and an interactive submit step, so it can't run from CI.

## 1. modelcontextprotocol/servers (community catalog)

The community list at <https://github.com/modelcontextprotocol/servers>
maintains a `README.md` table of community servers. Submit a PR with one
row matching the existing format:

```markdown
| **odoo-mcp** | A safety-first MCP bridge for Odoo with diagnostic tools, JSON-2 transport, and approval-gated writes. |
```

Reference link: `https://github.com/tuanle96/mcp-odoo`.

## 2. Smithery (smithery.ai)

1. Sign in at <https://smithery.ai/> with the GitHub account that owns
   `tuanle96/mcp-odoo`.
2. Open the dashboard's **"Add server"** flow and select the GitHub
   repository.
3. Smithery picks up `smithery.yaml` automatically. Confirm the metadata,
   accept the Dockerfile-based runtime, and publish.
4. The published page exposes a one-click install snippet for Claude
   Desktop, Cursor, Windsurf, and others, sourced from the schema in
   `smithery.yaml`.

If Smithery asks for an API key for CI publishing, store it as the
`SMITHERY_API_KEY` repo secret and only then enable an automated
publish step. Until then, keep submission manual.

## 3. mcpservers.org

Optional: <https://mcpservers.org/> aggregates community servers via a
GitHub-style index. PR a new entry under `/data/odoo-mcp.json` with the
project URL, description, transport, language, and license.

## 4. Cursor / Continue marketplaces

Cursor and Continue surface MCP servers through their own marketplace
UIs that pull from the modelcontextprotocol community list and Smithery.
Submitting to the two sources above is enough.

## Verification after listing

After each listing goes live, run:

```bash
uvx odoo-mcp --health
```

to confirm the package on PyPI matches what the catalog page advertises.
