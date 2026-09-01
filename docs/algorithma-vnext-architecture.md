# Algorithma Odoo MCP vNext — architecture

vNext is a **maintained fork** of [erpipe-org/mcp-odoo](https://github.com/erpipe-org/mcp-odoo)
(MIT, upstream base `76ec136` = v1.3.2, tag `algorithma/upstream-base`). ERPipe stays the
generic core; the fork adds one concept — **per-request Odoo identity** — at the single
place where every tool obtains its Odoo client, and keeps the rest of the code as close to
upstream as possible so future ERPipe releases can be merged.

```text
ERPipe upstream  ──merge──►  Algorithma fork  ──►  per-request identity  ──►  Algorithma plugins
```

## The one idea: WHERE ≠ WHO

```text
INSTANCE = WHERE?   which Odoo server + database         (ERPipe: odoo_config.json / ODOO_URL, ODOO_DB)
IDENTITY = WHO?     which Odoo user acts on this request  (vNext: X-User-Email + X-Odoo-Api-Key headers)
```

Upstream ERPipe binds one configured credential per instance: every caller is "the
bot". vNext keeps that as **configured mode** (unchanged, default) and adds **request
mode**, where the credential arrives with the request and the MCP server authenticates
*that* user against Odoo. Odoo's ACLs and record rules stay the authorization
authority; the MCP server never becomes one.

```text
                      LibreChat (one chat user = one Odoo user)
                                     │
                                     │  X-User-Email: anna@example.ch
                                     │  X-Odoo-Api-Key: ****   (Anna's personal key)
                                     │  X-LibreChat-User-Id: lc-42 (optional, audit only)
                                     ▼
                   ┌────────────────────────────────────┐
                   │        Request Identity            │  identity.py
                   │  email · api_key · librechat_id    │  RequestIdentity (frozen, key never in repr)
                   └───────────────┬────────────────────┘
                                   │
                ┌──────────────────┴──────────────────┐
                │                                     │
            INSTANCE                              IDENTITY
             WHERE?                                 WHO?
   odoo_config.json / ODOO_URL+ODOO_DB      headers of this request
                │                                     │
                └──────────────────┬──────────────────┘
                                   ▼
                   ┌────────────────────────────────────┐
                   │   _resolve_odoo(ctx, instance)     │  server_core.py — the choke point
                   │   = instance entry + identity      │  every builtin tool and every plugin
                   │   → get_identity_client()          │  (plugin_api.resolve_odoo) passes here
                   └───────────────┬────────────────────┘
                                   ▼
                   ┌────────────────────────────────────┐
                   │   IdentityClientCache (TTL/LRU)    │  key = sha256(instance, db, login, key)
                   │   build_identity_client(entry, who)│  configured username/password ignored
                   └───────────────┬────────────────────┘
                                   ▼
                   ┌────────────────────────────────────┐
                   │   ERPipe core (unchanged)          │  read tools · schema · field ACL · rate limit
                   │   reads · policy · approvals ·     │  preview → validate → approve → execute
                   │   audit · retry · diagnostics      │  JSONL audit · retry on reads only
                   └───────────────┬────────────────────┘
                                   ▼
                   ┌────────────────────────────────────┐
                   │   OdooClient(url, db,              │  XML-RPC: authenticate(db, login, key) → uid,
                   │     username=Anna, password=key)   │           execute_kw(db, uid, key, …) per call
                   └───────────────┬────────────────────┘  JSON-2:  Authorization: bearer <key> per call
                                   ▼
                   ┌────────────────────────────────────┐
                   │              Odoo                  │
                   │   ACL · record rules · constraints │  Anna sees Anna's data; Bob sees Bob's
                   └────────────────────────────────────┘
```

Two users, one server:

```text
Anna ── key A ──►  MCP  ──► Odoo as Anna  ──► record rules → Anna's records
Bob  ── key B ──►  same ──► Odoo as Bob   ──► record rules → Bob's records
```

## Invariant: the pipeline

Every Odoo operation passes through the same stations. Plugins get their client from
`plugin_api.resolve_odoo(ctx, instance)`, which *is* `_resolve_odoo`, so they cannot
skip a station without violating the plugin contract.

```text
Reads                                     Writes
─────                                     ──────
REQUEST                                   REQUEST
  ↓                                         ↓
IDENTITY   headers → RequestIdentity        IDENTITY
  ↓                                         ↓
INSTANCE   name → url/db (no credentials)   INSTANCE
  ↓                                         ↓
POLICY     field ACL (AI data policy)       POLICY
  ↓                                         ↓
ODOO CLIENT  per (instance, identity)       PREVIEW     canonical payload + principal → token
  ↓                                         ↓
ODOO ACL / RECORD RULES                     VALIDATION  live fields_get → server-side approval
                                            ↓             record bound to (user, key, instance)
                                            APPROVAL    same user · same instance · same payload
                                            ↓             · unexpired · single use · confirm=true
                                            ODOO ACL / RECORD RULES
                                            ↓
                                            EXECUTE     as the validating user
                                            ↓
                                            AUDIT       JSONL: principal, instance, token digest
```

## What changed in the fork (and what did not)

| Area | Upstream ERPipe (kept) | vNext addition |
| --- | --- | --- |
| Server, tools, resources, prompts | `MCPServer` from the `mcp` SDK v2, 41 tools | untouched |
| Instance config | `odoo_config.json`, env vars, multi-instance | request mode needs only `url` + `db` (`ODOO_URL`+`ODOO_DB` suffice) |
| Client acquisition | `_resolve_odoo(ctx, instance)` → `AppContext` cache **per instance** | in request mode → `AppContext.get_identity_client()` cache **per (instance, db, login, key digest)** |
| Client factory | `build_odoo_client(entry)` with configured credentials | `build_identity_client(entry, identity)`; configured credentials ignored; `get_odoo_client*()` refuse in request mode |
| Write flow | preview → validate → execute, token = SHA-256 of canonical payload (model, op, ids, values, context, instance) | canonical payload gains `principal` (login) in request mode; the server-side approval record gains `identity_binding` (instance + login + key digest); execute checks both; token compare is constant-time |
| Chatter | preview token → confirm | token includes `principal` in request mode |
| Audit | JSONL with instance + token digest | + `principal`, `client_user_id` (never a key) |
| Health | runtime posture | + `identity` block: mode, headers, cache bounds, **warnings** for insecure setups |
| CLI | stdio default, HTTP opt-in | request mode over stdio is refused at start-up (no headers on stdio) |
| Field ACL | per-instance deny/allow | used as the **AI data policy** — no second mechanism (`examples/algorithma-vnext/odoo_mcp_policy.algorithma.json`) |
| Cross-instance fan-out | clients from the app context | resolved per instance under the requesting identity |
| Async tasks | job captures the client at submit time | captured client is the submitting user's; task listing is still process-wide (see limitations) |

Files: `src/odoo_mcp/identity.py` (new, pure core), `odoo_client.py`, `server_core.py`,
`agent_tools.py`, `tools_write.py`, `tools_read.py`, `tools_cross_instance.py`,
`tools_async.py`, `audit.py`, `schemas.py`, `__main__.py`, `server.py`, `.importlinter`.

## Fail-closed rules (request mode)

- No headers, blank headers, malformed headers → `MissingIdentityError` / `InvalidIdentityError`
  → the tool returns `{"success": false, "error": …}`; nothing touches Odoo.
- Configured `username`/`password`/`api_key` are never read on the request path. The
  functions that build shared clients (`get_odoo_client`, `get_odoo_client_for`,
  `AppContext.odoo`, `AppContext.get_client`) raise in request mode, so even code paths
  without a request context (the `odoo://` resources) cannot fall back to a bot account.
- An invalid `ODOO_MCP_IDENTITY_MODE` value aborts start-up.
- `health_check.identity.warnings` is non-empty when request mode runs over stdio, when
  shared credentials are still configured, or when tools with cross-user process state
  are registered.

## Where the API key lives (and where it never goes)

- In the request headers, in the `RequestIdentity` object (`repr` redacts it), and inside the
  authenticated `OdooClient` (needed for every RPC call), for the cache TTL at most.
- Never: log lines, audit entries, error messages, health output, cache keys, approval
  tokens (the token carries the *login*, the binding is a digest).

## Not in this iteration

Business tools from the current `A-Odoo-MCP` (documents, DMS, field service, Swiss
accounting, tax-document AI, deep analysis) are **not** ported. Their target shape is
described in [migration-from-algorithma-mcp.md](migration-from-algorithma-mcp.md); the
identity details, JSON-2 notes, and caching rules are in
[per-request-identity.md](per-request-identity.md).
