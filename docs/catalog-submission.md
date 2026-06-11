# MCP Catalog Submission Notes

This file documents the manual steps for getting `odoo-mcp` listed in the
public MCP server catalogs. The repo ships a prebuilt `smithery.yaml`, a
GHCR Docker workflow, and a publish-to-PyPI workflow — what's left is the
manual registration in each catalog.

---

## 0. Official MCP Registry (registry.modelcontextprotocol.io) — automated

### Current status

Not yet listed. The workflow and `server.json` are ready; the first publish
triggers once a human manually runs the workflow or a GitHub release fires it.

### How it works

1. `server.json` at the repo root describes the server
   (`io.github.tuanle96/mcp-odoo`, PyPI package `odoo-mcp`).
2. README.md carries the `mcp-name: io.github.tuanle96/mcp-odoo` ownership
   marker on line 3 inside an HTML comment. The registry validates it against
   the released PyPI long description, so the marker must exist in the
   **released** package (it has been present since v0.5.0).
3. `.github/workflows/mcp-registry.yml` authenticates via GitHub OIDC and
   publishes. It is triggered by `workflow_dispatch` or `workflow_call`.

### server.json validation

Current `server.json` passes schema validation against
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`.

Verified fields:

| Field | Value | Status |
|---|---|---|
| `$schema` | 2025-12-11 schema URL | OK |
| `name` | `io.github.tuanle96/mcp-odoo` | OK — matches README mcp-name marker |
| `version` | `0.8.0` | OK — matches pyproject.toml |
| `packages[0].registryType` | `pypi` | OK |
| `packages[0].identifier` | `odoo-mcp` | OK |
| `packages[0].transport.type` | `stdio` | OK |
| `environmentVariables` | 4 entries | OK |
| `websiteUrl` | GitHub Pages URL | OK |
| `repository.source` | `github` | OK |

**No changes required to server.json.**

### Workflow checklist (.github/workflows/mcp-registry.yml)

The workflow is structurally correct. Verify before the first publish:

- [ ] Confirm the workflow `id-token: write` permission is present (it is).
- [ ] Confirm `mcp-publisher login github-oidc` is the correct sub-command name
  for the version downloaded. The binary is fetched from the latest release; if
  the login sub-command changes, update the `login` step accordingly.
  To check: run `./mcp-publisher --help` locally after downloading.
- [ ] The version sync step uses `jq` — confirm `jq` is available on
  `ubuntu-latest` runners (it is by default).
- [ ] The workflow triggers on `workflow_dispatch` and `workflow_call`. To
  trigger automatically on release, add:
  ```yaml
  on:
    release:
      types: [published]
    workflow_dispatch:
    workflow_call:
  ```
- [ ] After the first successful run, verify the listing:
  ```bash
  curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.tuanle96/mcp-odoo" | jq .
  ```

### To trigger the first publish

Go to **Actions → MCP Registry → Run workflow** in the GitHub UI, or call it
from the release workflow via `workflow_call`. No secrets needed — authentication
uses GitHub OIDC (ephemeral token tied to the repo identity).

---

## 1. Docker MCP Catalog (docker/mcp-registry)

### Current status

Submitted 2026-06-11: [docker/mcp-registry#3928](https://github.com/docker/mcp-registry/pull/3928) (pinned to the v0.9.0 commit). Listing appears on `hub.docker.com/mcp` ~24h after merge.

### About the catalog

Docker maintains a catalog at `hub.docker.com/mcp` populated from the
`docker/mcp-registry` repo. Docker builds, signs, and publishes the image to
`docker.io/mcp/odoo-mcp` on approval. Listing appears within ~24 hours of merge.

### Submission steps

1. Fork `https://github.com/docker/mcp-registry` on GitHub.
2. Create a directory `servers/odoo-mcp/` in the fork.
3. Create `servers/odoo-mcp/server.yaml` with the content below.
4. Install prerequisites: Go v1.24+, Docker Desktop, and the `task` CLI
   (`brew install go-task/tap/go-task`).
5. Test locally:
   ```bash
   task build SERVER=odoo-mcp
   task catalog
   ```
6. Open a PR against `docker/mcp-registry:main` with title:
   `feat: add odoo-mcp MCP server`
   - PR description should note: Odoo ERP integration, stdio transport,
     MIT license, Docker-built image preferred.
   - License: MIT — acceptable (GPL is not accepted).
   - All commits will be squashed on merge.
7. Share test credentials with the Docker team via the form linked in CONTRIBUTING.md.

### server.yaml (ready to copy into the PR)

```yaml
name: odoo-mcp
image: mcp/odoo-mcp
type: server
meta:
  category: database
  tags:
    - erp
    - odoo
    - database
    - crm
about:
  title: Odoo MCP
  description: >-
    Safe, approval-gated MCP bridge for Odoo ERP. Exposes search, read, field
    schema discovery, access diagnostics, chatter, and write operations for any
    Odoo model. Supports XML-RPC (Odoo 14–18) and JSON-2 (Odoo 19+) transports,
    multi-instance routing, and an optional OAuth 2.1 resource-server mode for
    Streamable HTTP deployments.
  icon: https://avatars.githubusercontent.com/u/52296800?v=4
source:
  project: https://github.com/tuanle96/mcp-odoo
  branch: main
  commit: REPLACE_WITH_RELEASE_COMMIT_SHA
config:
  description: >-
    Configure the Odoo instance connection. ODOO_URL, ODOO_DB, ODOO_USERNAME,
    and ODOO_PASSWORD are required. ODOO_TRANSPORT selects the RPC protocol
    (xmlrpc for Odoo 14–18, json2 for Odoo 19+).
  secrets:
    - name: odoo-mcp.password
      env: ODOO_PASSWORD
      example: <YOUR_ODOO_PASSWORD_OR_API_KEY>
    - name: odoo-mcp.api_key
      env: ODOO_API_KEY
      example: <YOUR_ODOO_API_KEY>
  env:
    - name: ODOO_URL
      example: https://mycompany.odoo.com
      value: "{{odoo-mcp.url}}"
    - name: ODOO_DB
      example: mycompany
      value: "{{odoo-mcp.db}}"
    - name: ODOO_USERNAME
      example: admin@mycompany.com
      value: "{{odoo-mcp.username}}"
    - name: ODOO_TRANSPORT
      example: xmlrpc
      value: "{{odoo-mcp.transport}}"
  parameters:
    type: object
    required:
      - url
      - db
      - username
    properties:
      url:
        type: string
        description: Odoo instance URL (e.g. https://mycompany.odoo.com)
      db:
        type: string
        description: Odoo database name
      username:
        type: string
        description: Odoo login email
      transport:
        type: string
        description: "RPC transport: xmlrpc (default) or json2 (Odoo 19+)"
```

**Before opening the PR:** replace `REPLACE_WITH_RELEASE_COMMIT_SHA` with the
actual commit SHA of the v0.8.0 tag:
```bash
git rev-parse v0.8.0
```

### After merge

Docker will build the image and publish it to `docker.io/mcp/odoo-mcp`. Verify:
```bash
docker pull mcp/odoo-mcp
docker run --rm mcp/odoo-mcp --health
```

---

## 2. modelcontextprotocol/servers (community catalog)

The community list at `https://github.com/modelcontextprotocol/servers`
maintains a `README.md` table. Submit a PR with one row matching the existing
format:

```markdown
| **odoo-mcp** | A safety-first MCP bridge for Odoo with diagnostic tools, JSON-2 transport, and approval-gated writes. |
```

Reference link: `https://github.com/tuanle96/mcp-odoo`.

---

## 3. Smithery (smithery.ai)

1. Sign in at `https://smithery.ai/` with the GitHub account that owns
   `tuanle96/mcp-odoo`.
2. Open the dashboard's **"Add server"** flow and select the GitHub repository.
3. Smithery picks up `smithery.yaml` automatically. Confirm the metadata,
   accept the Dockerfile-based runtime, and publish.
4. The published page exposes a one-click install snippet for Claude Desktop,
   Cursor, Windsurf, and others.

If Smithery asks for an API key for CI publishing, store it as the
`SMITHERY_API_KEY` repo secret and only then enable an automated publish step.

---

## 4. Outreach

### OEC.sh — proposed featured integration message

**Send to:** `sales@oec.sh` (primary) or `hello@oec.sh` — both published at
[oec.sh/about](https://oec.sh/about/). OEC.sh is run by OpenEduCat Inc. and
has a formal partner program ([oec.sh/for/partners](https://oec.sh/for/partners)).
Backup channels: X/Twitter [@openeducat](https://twitter.com/openeducat),
[LinkedIn](https://www.linkedin.com/company/openeducat-inc). No dedicated
`partnerships@` alias exists, so lead the subject with "featured integration".

> **To: sales@oec.sh**  
> **Subject: odoo-mcp — MCP server for Odoo ERP, open to collaboration**
>
> Hi OpenEduCat team,
>
> I'm the author of odoo-mcp (https://github.com/tuanle96/mcp-odoo), a
> production-ready MCP server for Odoo ERP. It exposes Odoo's XML-RPC and
> JSON-2 APIs through the Model Context Protocol with approval-gated writes,
> schema discovery, and multi-instance routing.
>
> It's on PyPI (odoo-mcp), the official MCP Registry, Smithery, Glama, and
> PulseMCP, with a Docker MCP Catalog listing in review. I'd love to explore
> featuring it on OEC.sh if there's a good fit — happy to provide docs, a
> demo, or a guest post. Let me know if you're interested.
>
> Best,  
> Tuan Le

### Peliqan — proposed featured integration message

**Send to:** `hello@peliqan.io` — published at
[peliqan.io/contact](https://peliqan.io/contact/) (also a contact form +
demo scheduler there). Backup: [LinkedIn](https://www.linkedin.com/company/peliqan-data).
No public author byline on the "MCP for Odoo Partners" post, so address the
team and reference the post directly.

> **To: hello@peliqan.io**  
> **Subject: Odoo MCP server — integration opportunity**
>
> Hi Peliqan team,
>
> I read your "MCP for Odoo Partners" post — it lines up closely with what
> I've been building.
>
> I built odoo-mcp (https://github.com/tuanle96/mcp-odoo), an open-source MCP
> server that gives AI agents direct, safety-gated access to Odoo ERP. The
> server handles field schema discovery, multi-instance routing, and
> approval-gated writes so agents can operate on Odoo data without running
> unchecked mutations.
>
> Given Peliqan's focus on data integration, I thought this might be a useful
> addition to your connector catalog or documentation. I'm happy to provide a
> writeup, example queries, or a live demo. Would you be open to a quick call?
>
> Best,  
> Tuan Le

---

## 5. mcpservers.org (optional)

`https://mcpservers.org/` aggregates community servers via a GitHub-style index.
PR a new entry under `/data/odoo-mcp.json` with the project URL, description,
transport, language, and license.

---

## Verification after listing

After each listing goes live:

```bash
# Official MCP Registry
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.tuanle96/mcp-odoo" | jq .

# PyPI package health
uvx odoo-mcp --health

# Docker image (after Docker MCP Catalog merge)
docker pull mcp/odoo-mcp && docker run --rm mcp/odoo-mcp --health
```
