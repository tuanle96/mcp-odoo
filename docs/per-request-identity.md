# Per-request identity mode

`ODOO_MCP_IDENTITY_MODE` selects who the MCP server is when it talks to Odoo.

| Mode | Value | WHO | Use |
| --- | --- | --- | --- |
| configured (default) | unset / `configured` | the credential in `odoo_config.json` / `ODOO_USERNAME`+`ODOO_PASSWORD` — upstream ERPipe behavior, byte-identical | single-user or bot deployments |
| request | `request` | the user named in the HTTP headers of **each** MCP call | LibreChat: every chat user acts as their own Odoo user |

Any other value refuses to start.

## Headers

| Header | Required | Meaning |
| --- | --- | --- |
| `X-User-Email` | yes | Odoo login of the acting user |
| `X-Odoo-Api-Key` | yes | that user's personal Odoo API key (Odoo → Preferences → Account Security) |
| `X-LibreChat-User-Id` | no | opaque front-end user id, recorded in the audit log only |

Header names are matched case-insensitively (HTTP semantics). Values are stripped;
whitespace/control characters or over-long values are rejected as *invalid*, absent or
blank values as *missing*. Both fail closed before any Odoo call.

LibreChat wiring (`librechat.yaml`):

```yaml
mcpServers:
  odoo:
    type: streamable-http
    url: http://mcp:8000/mcp
    headers:
      X-User-Email: "{{LIBRECHAT_USER_EMAIL}}"
      X-LibreChat-User-Id: "{{LIBRECHAT_USER_ID}}"
      X-Odoo-Api-Key: "{{ODOO_API_KEY}}"
    customUserVars:
      ODOO_API_KEY:
        title: "Odoo API-Schlüssel"
        description: "Persönlicher Schlüssel aus Odoo → Benutzer → Sicherheit → API-Schlüssel"
```

## Error taxonomy

All identity failures subclass `IdentityError(ValueError)`; messages are safe to show
to the caller and never contain a credential.

| Error | When | Tool response |
| --- | --- | --- |
| `MissingIdentityError` | headers absent/blank, transport without headers (stdio), or a shared-client code path in request mode | `success: false`, names the missing header(s) |
| `InvalidIdentityError` | header present but malformed (length, whitespace, control chars) | `success: false`, names the header |
| `IdentityAuthenticationError` | Odoo rejected login/key | `success: false`, "rejected the credentials supplied for <login>: invalid login or API key" |
| `IdentityOdooUnreachableError` | connection/timeout while authenticating | `success: false`, "instance <name> is unreachable: <reason>" |

## How the credential reaches Odoo

**XML-RPC (Odoo 16–19, the bauag2 path).** `common.authenticate(db, login, api_key, {})`
returns the uid once; every subsequent `object.execute_kw(db, uid, api_key, model, method,
…)` carries the key again, so Odoo re-checks the credential on **every** call. A revoked
key stops working at the next call, independent of any cache on our side.

**JSON-2 (Odoo 19+, `transport: json2`).** There is no uid. Every request carries
`Authorization: bearer <api_key>` (+ `X-Odoo-Database` when enabled) and Odoo resolves
the user from the key itself. Per-user JSON-2 therefore needs nothing beyond building the
client with the request's key as bearer — `build_identity_client` does exactly that.
Verified against upstream's `OdooClient._json2_call`; the disposable smoke in this repo
runs Odoo 18 (XML-RPC), so JSON-2 per-user operation is implemented but only unit-tested
until an Odoo 19 stack is available (see limitations in the report).

## Caching

```text
key   = sha256("odoo-mcp-identity" ‖ instance ‖ db ‖ lower(login) ‖ api_key)   (never the raw key)
value = the authenticated OdooClient
bound = ODOO_MCP_IDENTITY_CACHE_MAX (default 256 entries, LRU) · ODOO_MCP_IDENTITY_CACHE_TTL (default 900 s)
```

Consequences:

- Anna's session is never served to Bob (different login → different key).
- Instance A's session is never served for instance B (instance and db are in the key).
- A rotated or revoked key is a different entry; the old session object expires with the
  TTL and, because XML-RPC/JSON-2 re-send the credential per call, stops working at the
  very next Odoo call anyway.
- The cache lives on the process-wide `AppContext` (the SDK enters the lifespan once per
  server), which is why the key must carry the identity and not just the instance.

Compared with the previous Algorithma MCP (`_uid_cache[(email, key)] = uid`, unbounded, no
TTL): bounded, expiring, instance-aware, and it never stores the raw key as a key.

## Approvals are bound to the user and the instance

```text
validate_write (as Anna, instance bauag2)
   token     = sha256(model, operation, record_ids, values, context, instance, principal=anna)
   record    = { payload, expires_at, identity_binding = sha256(instance ‖ anna ‖ key) }

execute_approved_write (as Bob, same approval)   → "approval was issued to a different user"
execute_approved_write (as Anna, other key)      → "validated under a different identity or instance"
execute_approved_write (instance changed)        → token mismatch (upstream rule, kept)
execute_approved_write (as Anna, confirm=true)   → executes as Anna, approval consumed
```

`chatter_post` tokens carry the principal the same way. Configured mode keeps upstream's
exact tokens (the extra keys only appear when a principal exists).

## Field ACL as the AI data policy

```text
AI-visible data  =  Odoo permission  ∩  MCP field policy
```

Odoo decides what the human may see; the field policy decides what may enter the model's
context. The Algorithma rules (employee identity numbers, emergency contacts, DMS binaries
and access tokens, `res.users` reduced to an allow-list) live in
`examples/algorithma-vnext/odoo_mcp_policy.algorithma.json` and are enforced by ERPipe's
existing field ACL on every read tool, resource, and knowledge index; aggregating on a
denied field is rejected. No second policy system was added.

## Observability

Audit lines (`ODOO_MCP_AUDIT_LOG`) gain `principal` (the login) and `client_user_id`:

```json
{"ts": "2026-09-01T16:40:12Z", "event": "execute", "outcome": "success",
 "instance": "bauag2", "model": "res.partner", "operation": "create",
 "principal": "anna@example.ch", "client_user_id": "lc-42",
 "token_sha256": "5b2c…", "record_ids": [], "detail": null}
```

Never present: the API key. Storing the login in clear text is a deliberate prototype
choice; a deployment that must pseudonymise operators can hash the principal at the
audit boundary (`audit.record_write_event` is the single writer) — documented, not built.

## Health check

`health_check().identity` (also under `runtime.identity` and in `odoo-mcp --health`):

```json
{"identity_mode": "request", "request_identity_required": true,
 "configured_shared_credentials_used": false, "configured_credentials_present": false,
 "identity_headers": ["x-user-email", "x-odoo-api-key", "x-librechat-user-id"],
 "cache": {"max_entries": 256, "ttl_seconds": 900.0}, "warnings": [], "ok": true}
```

Warnings (and `ok: false`) appear for: request mode over stdio; shared credentials
configured next to request mode; tools with process-wide cross-user state still
registered (`index_knowledge`, `search_knowledge`, `knowledge_stats`, `*_async_task*`).

## Running locally / on the VM

```bash
uv sync --extra dev
uv run python -m pytest            # 978 tests (931 upstream + 47 identity)
uv run python -m ruff check .
uv run python -m mypy src
PYTHONPATH=src uv run --with import-linter lint-imports

scripts/run_request_mode.sh        # request mode, 127.0.0.1:8010, bauag2 instance config
scripts/identity_client.py --health
ODOO_MCP_USER_EMAIL=… scripts/identity_client.py --model res.partner     # key prompted
A_EMAIL=… A_API_KEY=… B_EMAIL=… B_API_KEY=… scripts/identity_client.py --compare
scripts/identity_client.py --no-identity                                   # expect refusal

# real two-user proof on a disposable Odoo 18 (Docker):
uv run --python 3.12 --with-editable . scripts/algorithma_identity_smoke.py

# the same proof against an existing Odoo container (dev VM, bauag2):
uv run python scripts/algorithma_identity_smoke.py --backend container \
    --container bauag2-odoo --odoo-url http://127.0.0.1:8071 --db bauag2 \
    --docker-sudo --mcp-port 8010 --save-keys ~/.algorithma-vnext/bauag2-smoke-keys.json
# ... and remove the fixture again:
uv run python scripts/algorithma_identity_smoke.py --backend container --docker-sudo --cleanup
```

The container backend creates two internal users (`anna@identity-smoke.test`,
`bob@identity-smoke.test`, with Contact Creation), partners prefixed
`ZZ Identity-Smoke …`, and one **global** record rule on `res.partner` whose domain is
`[('user_id', '=', user.id)]` only for logins ending in `@identity-smoke.test` and
`[(1, '=', 1)]` for everyone else — real users are never restricted. Result on bauag2
(2026-09-01): **20/20 checks passed** — A and B see disjoint partners, B cannot spend A's
approval, A's approved create ran as A (`create_uid`), `list_models` returned Odoo's own
ACL refusal for plain users, no key in audit or server log. Note for anyone scripting
Odoo fixtures: after `env.cr.commit()` in `odoo shell` call
`env.registry.signal_changes()`, otherwise the running server keeps stale per-user
ACL/rule caches.

The server binds `127.0.0.1` unless `--allow-remote-http` is given; inside the Turm
Docker network LibreChat reaches it as `http://<mcp-container>:8000/mcp`.
