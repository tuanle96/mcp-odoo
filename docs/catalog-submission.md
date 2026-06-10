# MCP Catalog Submission Notes

This file documents the manual steps for getting `odoo-mcp` listed in the
public MCP server catalogs. The repo ships a prebuilt `smithery.yaml`, a
GHCR Docker workflow, and a publish-to-PyPI workflow — what's left is the
manual registration in each catalog. Smithery in particular requires an
account and an interactive submit step, so it can't run from CI.

## 0. Official MCP Registry (registry.modelcontextprotocol.io) — automated

The canonical discovery surface. Publishing is automated:

- `server.json` at the repo root describes the server
  (`io.github.tuanle96/mcp-odoo`, PyPI package `odoo-mcp`).
- README.md carries the `mcp-name: io.github.tuanle96/mcp-odoo` ownership
  marker; the registry validates it against the published PyPI long
  description, so the marker must be in the **released** package.
- The Dockerfile carries the matching
  `io.modelcontextprotocol.server.name` label for a future OCI package
  entry (not yet listed in `server.json` — add it once a labeled image
  is on GHCR).
- The `mcp-registry-publish` job in `.github/workflows/publish.yml` runs
  on every GitHub release after the PyPI upload: it syncs the version
  from `pyproject.toml`, authenticates via GitHub OIDC
  (`mcp-publisher login github-oidc`), and publishes.

Note: the first registry publish only succeeds once a PyPI release whose
README contains the `mcp-name` marker is live (0.5.0+).

After publishing, verify the listing:

```bash
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.tuanle96/mcp-odoo" | jq .
```

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
