# Adding a tool to odoo-mcp

A walkthrough of the conventions a new MCP tool must follow. Ten minutes of
reading here saves a round of review comments.

## Where things live

| Layer | Files | Rule (enforced by `.importlinter`) |
| --- | --- | --- |
| Core (pure logic) | `odoo_client.py`, `agent_tools.py`, `diagnostics.py`, `tool_helpers.py`, … | May **never** import the MCP surface |
| Surface (MCP tools) | `tools_read.py`, `tools_write.py`, `tools_diagnostics.py`, `tools_knowledge.py`, `tools_accounting.py`, `tools_cross_instance.py`, `tools_async.py` | Import `mcp` from `server_core`, register via decorator |
| Aggregation | `server.py` | Pure re-export; importing it registers everything |

Put the logic in a core module (new or existing) and the thin `@mcp.tool`
wrapper in the `tools_*.py` file that matches its domain. If you create a new
module, add it to the matching contract in `.importlinter`.

## The registration ritual

1. **Write the tool** in the right `tools_*.py`:

   ```python
   from .server_core import mcp

   @mcp.tool(structured_output=True)
   def my_tool(model: str, instance: str | None = None) -> dict[str, Any]:
       """One-line summary the agent will read.

       Longer guidance: when to use it, caps, side effects (none for reads).
       """
       try:
           client = _get_client(instance)
           ...
           return {"success": True, "tool": "my_tool", ...}
       except Exception as e:  # noqa: BLE001
           return {"success": False, "error": sanitize_odoo_error(e)}
   ```

2. **Error envelope**: every tool returns `{"success": True, ...}` or
   `{"success": False, "error": str}`. Never raise through the tool boundary.

3. **Re-export in `server.py`**: add the symbol to the module import block
   *and* to `__all__`. Tests and downstream users import from
   `odoo_mcp.server`.

4. **The `_srv()` seam**: if your tool calls a symbol tests monkeypatch
   (`get_odoo_client`, `resolve_instance_name`, `build_domain_report`, …),
   resolve it late via the module-local `_srv()` helper instead of importing it
   directly — that is what keeps `monkeypatch.setattr(server, ...)` working.

5. **Field ACL & limits**: any tool that returns record data must pass results
   through the field-policy choke point (see how `search_records` uses
   `apply_field_policy`) and clamp result sizes (`clamp_limit`,
   `MAX_SEARCH_LIMIT`).

6. **Writes are gated**: new write-capable behavior must route through the
   `preview_write` → `validate_write` → `execute_approved_write` flow. Do not
   add a tool that mutates Odoo directly; PRs that bypass the gate are
   declined (see CONTRIBUTING.md).

## Tests

- Unit tests live next to the domain: `tests/test_<module>.py`.
- Surface behavior (registration, envelope, instance routing) is asserted in
  `tests/test_server.py` — follow the existing patterns there.
- Run everything the CI runs:

  ```bash
  uv run python -m pytest
  uv run python -m ruff check .
  uv run python -m mypy src
  PYTHONPATH=src uv run lint-imports
  ```

## Easy to forget

- `scripts/odoo_compose_smoke.py` asserts the **exact tool count** and some
  response strings — adding a tool without updating it breaks the smoke run.
- `health_check`'s `mcp_surface_counts` reflects registration automatically,
  but README/docs tables that state tool counts must be bumped by hand.
- Tool descriptions are agent-facing UX: say when to use the tool, its caps,
  and its side effects (or that it has none).

## Good first contributions

Check the [good first issue label](https://github.com/erpipe-org/mcp-odoo/labels/good%20first%20issue)
— typed output schemas, rename-catalog entries, and client examples are all
self-contained. Questions → GitHub Discussions.
