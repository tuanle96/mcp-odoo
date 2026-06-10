# Troubleshooting

How to go from "the agent got an error" to a root cause. Start by feeding
the error text back to the server — that is what the classifier is for:

```text
diagnose_access(model="sale.order", operation="read", observed_error="<paste the error>")
```

The report's `error_classification.category` tells you which section below
applies. `diagnose_odoo_call` returns the same classification for
shape/transport problems.

## Categories

### `authentication`
The credential itself was rejected before any model-level check.
- Verify `ODOO_URL` / `ODOO_DB` / `ODOO_USERNAME` / `ODOO_PASSWORD`.
- Odoo Online (SaaS) requires an API key, not the login password.
- Run `odoo-mcp --health`, then the `health_check` tool.

### `db_routing`
The database name does not exist or the request reached the wrong database.
- `list_instances` shows what this server is configured for.
- `get_odoo_profile` shows what it is actually connected to.
- Self-hosted with `dbfilter`: the URL's host must match the filter.

### `acl`
`ir.model.access` denies the operation for the user's groups.
- `diagnose_access(model=..., operation=...)` lists ACL rows and which ones
  grant; compare `current_user.group_ids` with the granting rows' groups.
- Fix in Odoo: Settings → Technical → Security → Access Rights.

### `record_rule`
ACL allows the operation, but an `ir.rule` domain filters these records —
the classic "I can read *some* records but not this one".
- `diagnose_access(record_ids=[...], include_rules=True)` shows active,
  global, and group-bound rules with their domains.
- `expected_count` vs `actual_count` in the same report proves silent
  filtering.

### `multi_company`
A company-scoped rule blocked the operation.
- Compare the user's `company_ids` (in `diagnose_access.current_user.record`)
  with the records' `company_id`.
- Common with cross-company many2one links (e.g. a sale order referencing a
  product restricted to another company).

### `missing_or_filtered`
Odoo reports "record does not exist" — either truly deleted or hidden by a
record rule (Odoo masks unauthorized IDs as missing).
- `search_records(domain=[["id", "=", <id>]])` — empty result + existing ID
  elsewhere means a rule hides it; then run `diagnose_access` on the ID.

## Other frequent issues

| Symptom | Cause / fix |
| --- | --- |
| `write execution disabled` | Set `ODOO_MCP_ENABLE_WRITES=1` (writes are off by default). |
| `approval token has not been validated` | Call `validate_write` (with live metadata) before `execute_approved_write`; tokens expire after 10 minutes and are session-bound. |
| `method ... is not allowed` on `execute_method` | Direct `create/write/unlink` are always blocked; side-effect methods need a review entry — see the policy file in [docs/architecture.md](./architecture.md) and `odoo_mcp_policy.json.example`. |
| Hallucinated model names (`account.invoice`) | `lookup_model_history(name=...)` maps old names to current ones. |
| Agent loops `read_record` | `health_check` → `runtime.n_plus_one.hot_models` flags it; batch with `search_records` and an `["id", "in", [...]]` domain. |
| Intermittent `Failed to connect` | Read-only calls retry automatically (`ODOO_MCP_RETRY_ATTEMPTS`, default 2, exponential backoff). Persistent failures: check URL/SSL (`ODOO_VERIFY_SSL`), proxy, and Odoo worker availability. |
| HTTP transport refuses to start | Non-local binds need `--allow-remote-http` (or `MCP_ALLOW_REMOTE_HTTP=1`) — deliberate, keep it behind your own auth proxy. |

## When opening an issue

Attach the output of `health_check` (it is non-secret by design) plus the
sanitized error from `diagnose_odoo_call` — both redact debug payloads.
