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
