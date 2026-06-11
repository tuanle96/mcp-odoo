# Performance Tuning

Defaults are sized for interactive agent sessions. This page lists the
knobs for heavier deployments and the patterns that keep agents fast.

## Server-side knobs

| Env var | Default | Effect |
| --- | --- | --- |
| `ODOO_TIMEOUT` | `30` | Per-request timeout (seconds) toward Odoo. |
| `ODOO_MCP_RETRY_ATTEMPTS` | `2` | Extra attempts for **read-only** calls on connection errors (0–5). Writes never retry. |
| `ODOO_MCP_RETRY_BACKOFF` | `0.5` | Base backoff seconds; doubles per retry. |
| `ODOO_MCP_SCHEMA_CACHE_TTL` | `600` | Seconds before cached schema/catalog entries expire. |
| `ODOO_MCP_SCHEMA_CACHE_MAX` | `256` | Max cached entries (LRU eviction). Raise for many instances × many models. |
| `ODOO_MCP_MAX_SMART_FIELDS` | `15` | Cap for smart field selection on `search_records`/`read_record`. |

## Patterns that keep agents fast

1. **Batch instead of N+1.** Looping `read_record` is the most common agent
   slowdown. `health_check` → `runtime.n_plus_one.hot_models` flags any
   model read ≥10 times in 60 s; switch to one `search_records` call with
   an `["id", "in", [...]]` domain.
2. **Aggregate server-side.** `aggregate_records` pushes groupby/sum/count
   into Postgres; reading rows to add them up in the LLM is both slow and
   token-expensive.
3. **Let smart field selection work.** Omitting `fields` returns a curated
   business subset. `fields=["*"]` on `res.partner` fetches hundreds of
   columns — only do that deliberately.
4. **Rank before exploring.** `get_model_fields(relevance="top")` keeps
   schema exploration on wide models to the `max_fields` most relevant
   columns instead of the full metadata dump.
5. **Bounded reads are intentional.** `search_records` caps `limit` at 100;
   page with `offset` rather than fighting the cap — it protects both Odoo
   and your context window.
6. **Reuse the catalog cache.** `schema_catalog` results are cached
   (`refresh=true` forces a reload). Repeated catalog calls within the TTL
   are free.

## Sizing notes

- The schema cache stores at most `ODOO_MCP_SCHEMA_CACHE_MAX` entries of
  field metadata; with the default 256 and typical models this stays in the
  tens of MB. Fifty instances with broad catalog use may want `1024`+.
- Retries multiply worst-case latency: `timeout × (1 + attempts)` plus
  backoff. Latency-sensitive setups can set `ODOO_MCP_RETRY_ATTEMPTS=0`.

## Long-running work: background tasks

Operations that can take many seconds (large addon scans, knowledge
indexing, full AR/AP aging) should go through `submit_async_task` so the
agent keeps reasoning while the work runs on a bounded thread pool
(`ODOO_MCP_ASYNC_MAX_WORKERS`, default 2). Poll with `get_async_task`.
Results are in-memory only: `ODOO_MCP_ASYNC_RESULT_TTL` (default 1h) and
`ODOO_MCP_ASYNC_MAX_TASKS` (default 50) bound retention, and a restart
clears them.

## Repeated lookups: local knowledge index

When an agent will ask many free-text questions over the same data slice,
one `index_knowledge` call followed by `search_knowledge` queries replaces
N `search_records` round-trips with local BM25 ranking (accent-insensitive,
zero RPC per query). `ODOO_MCP_KNOWLEDGE_MAX_DOCS` (default 5000) bounds
total memory across all indexes.

## Runaway loops: rate limiting

`health_check.runtime.n_plus_one` catches per-record read loops by shape;
`ODOO_MCP_RATE_LIMIT_MODE` catches raw volume. `warn` only surfaces
counters in `health_check.rate_limits`; `block` refuses calls beyond
`ODOO_MCP_RATE_LIMIT_MAX_CALLS` per `ODOO_MCP_RATE_LIMIT_WINDOW` seconds
on `search_records`, `read_record`, `aggregate_records`, and
`execute_method`. Default is `off`.

Note: `block` mode covers exactly those four high-volume tools. Other read
tools (`schema_catalog`, `search_employee`, `search_holidays`,
`index_knowledge`) and the async path are not rate-checked — async volume
is instead bounded by the worker pool and live-task cap.
