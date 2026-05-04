<!--
Thanks for contributing. Keep PRs small, tested, and clear about the
Odoo version and transport they affect.
-->

## Summary

<!-- Describe the user-facing behavior change in 1-3 lines. -->

## Coverage

- Odoo versions tested: <!-- e.g. 17.0, 18.0, 19.0 -->
- Transports tested: <!-- xmlrpc | json2 | stdio | streamable-http | sse -->

## Verification

```bash
# Paste the exact commands you ran
uv run python -m ruff check .
uv run python -m mypy src
uv run python -m pytest
```

For compatibility-affecting changes, also include:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py \
  --versions 16.0 17.0 18.0 19.0 \
  --timeout 360 \
  --inspector-smoke
```

## Checklist

- [ ] Updated `README.md` / `docs/` when behavior changes
- [ ] Updated `CHANGELOG.md` under `## Unreleased`
- [ ] No credentials, private database names, or production debug traces in diff or logs
- [ ] Write-execution safety gates (`preview_write` → `validate_write` → `execute_approved_write`, `ODOO_MCP_ENABLE_WRITES`) are unchanged, OR the change is accompanied by a security note explaining why

## Notes for reviewers

<!-- Anything reviewers should focus on: trade-offs, follow-ups, known gaps -->
