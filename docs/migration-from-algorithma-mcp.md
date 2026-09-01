# Migration: from `A-Odoo-MCP` (v3.3.x) to vNext

The current Algorithma MCP (`A-Odoo-MCP`, one `server.py` on fastmcp 2 with 19 tools plus
`documents.py`) stays **untouched and in production for sanitaer** during this phase. It is
the behavioural and security reference; vNext is the structural base. This page classifies
every feature of the current MCP and says where it goes.

Legend: **REPLACE WITH ERPIPE** = the generic core already does it (better) ·
**KEEP IN CORE** = ported into the fork's core in this iteration ·
**PORT AS PLUGIN** = later, as an `odoo_mcp.tools` entry-point plugin ·
**DEPRECATE** = not carried over.

## Classification

| Current feature (`server.py` / `documents.py`) | Verdict | Where / how |
| --- | --- | --- |
| `X-User-Email` / `X-Odoo-Api-Key` / `X-LibreChat-User-Id` per request, `_identity()` | **KEEP IN CORE** | `identity.py` · `ODOO_MCP_IDENTITY_MODE=request` — done |
| `_uid()` + `_uid_cache[(email, key)]` (unbounded, no TTL) | **REPLACE** | `IdentityClientCache`: TTL/LRU, key = digest of instance+db+login+key — done |
| `_execute()` (XML-RPC `execute_kw` as the user) | **REPLACE WITH ERPIPE** | `OdooClient` built per identity; retry on read-only methods only |
| `search_read`, `get_record`, `count`, `felder`, `read_group` | **REPLACE WITH ERPIPE** | `search_records`, `read_record`, `aggregate_records`, `get_model_fields`, `list_models`, `schema_catalog` (+ smart field selection, schema cache, rate limit) |
| `create_record` / `update_record` with `confirm: bool` + confirmation card | **REPLACE WITH ERPIPE** (security) · card UX later | `preview_write → validate_write → execute_approved_write`; token now bound to user + instance. The LibreChat *card* can be re-created on top of `preview_write` output (UI ≠ authorization) |
| `BLOCKED_PREFIXES` / `BLOCKED_MODELS` (model-level deny: `ir.*`, `mail.*`, `res.users`, …) | **KEEP IN CORE — pending** | Not expressible in ERPipe's field ACL (field-level only). Option: `ODOO_MCP_TOOLS_INCLUDE` limits *tools*, not models. Needs a small model-allow/deny list in `field_policy.py` or `tool_helpers.validate_model_name`; upstream-friendly change, proposed as the first follow-up. Until then `res.users` is reduced to an allow-list in the policy file |
| `FIELD_BLOCKLIST` + `_scrub()` (FADP data diet: `ssnid`, `identification_id`, `passport_id`, `emergency_*`, DMS binaries/tokens) | **KEEP IN CORE** | Expressed as ERPipe field ACL — `examples/algorithma-vnext/odoo_mcp_policy.algorithma.json` (deny even if Odoo allows; aggregation on denied fields rejected) — done |
| `_user_in_groups()` (`res.users.has_group` checks inside tools) | **DEPRECATE** | Odoo's ACL/record rules decide; tool-level group checks moved to policy (`diagnose_access` for diagnostics). Re-introduce only as a plugin-local guard if a business pack needs it |
| `log.info("call: user= model= method=")` | **REPLACE WITH ERPIPE** | JSONL audit (`ODOO_MCP_AUDIT_LOG`) with `principal`, `client_user_id`, instance, token digest — done |
| `mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")` | **REPLACE WITH ERPIPE** | CLI with local-only default bind, `--allow-remote-http`, host/origin allowlists, optional OAuth resource server; request mode refuses stdio |
| health (none today; `smoke-test.sh` probes 406) | **REPLACE WITH ERPIPE** | `health_check` incl. identity posture and warnings; `odoo-mcp --health` |
| `create_partner`, `create_invoice`, `post_journal_entry`, `pay_invoice`, `get_account_by_code`, `CHF_SCHWELLE`, 8.1 % VAT defaults | **PORT AS PLUGIN** `algorithma_accounting_ch` | Business intent tools on top of the gated write flow (preview/validate/execute via the core API); Swiss defaults stay in the plugin, generic invoice logic could later go to `algorithma_accounting` |
| `einsatzrapport_erstellen`, `auftrag_monteur_zuweisen` (`person_id`), `auftrag_abschliessen` (`action_complete`), `termin_buchen`, `bericht_link` | **PORT AS PLUGIN** `algorithma_fieldservice` | OCA field-service knowledge (incl. the regression tests for `person_id` and `action_complete`) moves with the plugin; `action_complete` becomes a reviewed side-effect method in the policy file |
| `dms_*` tools (list, upload, rename, move, lock, delete, share link, chat uploads) — `DMS_ENABLED` | **PORT AS PLUGIN** `algorithma_dms` | OCA DMS pack; binary fields stay denied for reads; uploads go through the plugin's dedicated tool |
| `dokument_entwurf_erstellen` + `documents.py` (CSV/XLSX/PDF/DOCX/XML, formula-injection neutralisation, XML DOCTYPE/ENTITY rejection, size/table limits, safe filenames, per-user `DraftStore`, SHA-256 integrity, TTL, tamper detection) + `dokument_entwurf_im_dms_speichern` (idempotent save) | **PORT AS PLUGIN** `algorithma_documents` | **Do not rewrite.** `documents.py` and its tests move as-is; the plugin obtains the identity (for per-user drafts) and the DMS save goes through the write gate |
| `dokument_analysieren` (Gemini tax-document extraction, consensus) | **PORT AS PLUGIN** `algorithma_tax_ai` | needs its own key management; out of the core by design |
| `tiefe_analyse` (Azure deep analysis) | **PORT AS PLUGIN** `algorithma_deep_analysis` | same |
| `VERSION` file / `_component_version()` | **REPLACE WITH ERPIPE** | package version (`pyproject.toml`) + `health_check.server` |
| Tests `test_server_document_tools.py`, `test_server_fsm_tools.py`, `test_documents.py` | **PORT AS PLUGIN** | move with their plugins; the FSM tests encode Odoo-specific behaviour worth keeping |

## Order of porting

0. **Done (2026-09-01, `plugins/algorithma_workflows`):** the intent tools users ask
   for in sentences — `termin_buchen`, `create_partner`, `get_account_by_code`,
   `bericht_link`, `auftrag_monteur_zuweisen`, `auftrag_abschliessen`, `create_invoice`,
   `post_journal_entry`, `pay_invoice`, `einsatzrapport_erstellen`. Same names, German
   confirmation card, second call with `bestaetigen=true` + `freigabe_code`; underneath
   every write is `validate_write` → `execute_approved_write` (identity-bound approval,
   audit) and every method call is `execute_method` (side-effect allowlist:
   `fsm.order.action_complete`, `account.move.action_post`,
   `account.payment.register.action_create_payments`). The first LibreChat test showed
   why this had to come first: without intent tools the model would not compose the
   generic three-step write for "Termin eintragen". Two old bugs fixed on the way:
   appointments now convert Europe/Zurich → UTC and default to one hour.
1. **`algorithma_documents`** next: self-contained, already well tested, no Odoo write of
   its own except the final DMS save, and it exercises the plugin ↔ identity contract
   (per-user drafts) end to end.
2. `algorithma_dms` (needed by documents' save step).
3. `algorithma_tax_ai`, `algorithma_deep_analysis`.

## Plugin structure (future)

ERPipe's entry-point plugins (`docs/plugins.md`) are the only plugin mechanism.

```text
plugins/
  algorithma_documents/      pyproject.toml  [project.entry-points."odoo_mcp.tools"] algorithma_documents = "algorithma_documents.plugin:register"
  algorithma_dms/
  algorithma_fieldservice/
  algorithma_accounting_ch/
  algorithma_tax_ai/
```

Activation stays explicit: `ODOO_MCP_PLUGINS=algorithma_documents,algorithma_dms`.

How a plugin obtains what it needs **without bypassing the pipeline**:

```python
def register(api):
    @api.tool(description="...", annotations=api.READ_ONLY_TOOL, structured_output=True)
    def dokument_entwurf_erstellen(ctx, ...):
        instance_name, odoo = api.resolve_odoo(ctx, instance)   # WHERE + WHO: identity-aware client
        rows, redacted = api.redact_records(instance_name, model, rows)   # AI data policy
        ...
```

| Need | Source (plugin API v1 today) | Note |
| --- | --- | --- |
| current Odoo client (as the user) | `api.resolve_odoo(ctx, instance)` | identical to builtin tools; request mode applies automatically |
| current identity (login for per-user drafts, audit) | `odoo_mcp.server_core.current_identity(ctx)` | to be added to `plugin_api` as `api.current_identity(ctx)` in the next API version — returns `None` in configured mode, never the key to plugin logs |
| field policy | `api.redact_records(...)` | mandatory before returning record data |
| writes | none in plugin API by design | plugins must **not** write directly; they build a preview payload and let the agent go through `preview_write → validate_write → execute_approved_write`, or call a reviewed side-effect method via `execute_method` (policy file) |
| audit | `odoo_mcp.audit.record_write_event(..., identity=identity.audit_fields())` | core module, importable |

Rule: **no plugin creates its own privileged Odoo connection.** A plugin that instantiates
`OdooClient` with configured credentials violates the contract and is treated as untrusted.

## What stays where during the transition

- `A-Odoo-MCP` keeps serving sanitaer (image `algorithma-agent-mcp:3.3.x`) unchanged.
- vNext serves **bauag2 only** (per Parwiz, 2026-09-01), first on the VM loopback
  (`scripts/run_request_mode.sh`), later as the `mcp` service of the bauag2 Turm stack.
- The LibreChat side needs no change: it already sends the three headers.
