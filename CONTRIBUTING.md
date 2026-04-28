# Contributing

Thanks for helping make Odoo MCP better. The best contributions are small, tested, and clear about the Odoo version and transport they affect.

## Development setup

```bash
git clone https://github.com/tuanle96/mcp-odoo.git
cd mcp-odoo
uv sync --extra dev
```

## Before you change code

Open an issue or draft PR for large behavior changes, new transports, security-sensitive changes, or changes that alter write execution behavior.

For small fixes, a focused pull request is enough.

## Quality gates

Run:

```bash
uv run python -m ruff check .
uv run python -m mypy src
uv run python -m pytest
```

For compatibility work, also run real Odoo smoke tests:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py \
  --versions 16.0 17.0 18.0 19.0 \
  --timeout 360 \
  --inspector-smoke
```

Package checks before release-oriented changes:

```bash
rm -rf dist
uv run python -m build
uv run python -m twine check dist/*
```

## Pull request checklist

- Describe the user-facing behavior change.
- Name the Odoo versions tested.
- Name the transport tested: XML-RPC, JSON-2, stdio, Streamable HTTP, or SSE.
- Include the exact test commands you ran.
- Update `README.md`, `docs/`, and `CHANGELOG.md` when behavior changes.
- Keep credentials, private database names, and Odoo debug traces out of issues and pull requests.

## Safety rules

Write execution is intentionally gated. Do not loosen these gates without a dedicated security discussion:

- direct `create`, `write`, and `unlink` remain blocked in `execute_method`,
- standard writes go through `preview_write`, `validate_write`, and `execute_approved_write`,
- executable approval requires trusted live metadata,
- `ODOO_MCP_ENABLE_WRITES=1` is required for execution.

## Style

Prefer clear, boring code. Keep helpers pure when possible, bound network calls, return structured errors, and document runtime gates.
