# Testing

This project uses fast local tests for development and Docker Compose smoke tests for real Odoo compatibility.

## Local gates

Run these before opening a pull request:

```bash
uv run python -m ruff check .
uv run python -m mypy src
uv run python -m pytest
```

Build and package checks:

```bash
rm -rf dist
uv run python -m build
uv run python -m twine check dist/*
```

Whitespace and patch hygiene:

```bash
git diff --check
git diff --cached --check
```

## Real Odoo smoke tests

The smoke harness starts disposable Docker Compose projects and tears them down after each run.

Run the full matrix:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py \
  --versions 16.0 17.0 18.0 19.0 \
  --timeout 360 \
  --inspector-smoke
```

Run one version at a time:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py --versions 16.0 --timeout 360 --inspector-smoke
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py --versions 17.0 --timeout 360 --inspector-smoke
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py --versions 18.0 --timeout 360 --inspector-smoke
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py --versions 19.0 --timeout 360 --inspector-smoke
```

The current smoke checks validate:

- disposable Odoo database bootstrap,
- direct XML-RPC read access,
- direct JSON-2 access for Odoo 19,
- MCP stdio tool/resource/prompt listing,
- MCP read calls through XML-RPC,
- MCP read calls through JSON-2 for Odoo 19,
- Streamable HTTP MCP calls for Odoo 19,
- MCP Inspector `tools/list` over stdio and HTTP,
- teardown of Compose containers, networks, and volumes.

## Expected surface

The smoke harness expects:

- 21 tools,
- 5 prompts,
- 1 direct resource,
- 3 resource templates,
- safe write preview and validation behavior,
- fail-closed approved write behavior when runtime gates are absent.

If you add or remove tools, prompts, or resources, update the smoke expectations in `scripts/odoo_compose_smoke.py` and document the change in `CHANGELOG.md`.

## Cleanup checks

After smoke testing, confirm there are no leftovers:

```bash
docker ps -a --filter name=mcp-odoo-smoke --format '{{.Names}}\t{{.Status}}'
docker network ls --filter name=mcp-odoo-smoke --format '{{.Name}}'
docker volume ls --filter name=mcp-odoo-smoke --format '{{.Name}}'
```

No output means the smoke stack cleaned up correctly.
