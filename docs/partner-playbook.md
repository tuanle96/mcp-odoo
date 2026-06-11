# Partner playbook — cross-instance queries

Odoo partners and multi-entity finance teams run the same questions across
many client databases: *which clients are behind on receivables? who is on an
unsupported Odoo version? where did margins slip this quarter?* Commercial
platforms answer this by syncing every client DB into a warehouse — expensive
and another copy of sensitive data to secure.

`odoo-mcp` answers it by **fanning out over the instances you already
configured** and merging the results. No warehouse, no sync, no extra data
copy. Read-only, each instance under its own field ACL, with a partial-result
contract so one client's downtime never sinks the whole query.

## Setup

Configure the instances as usual (see [multi-instance.md](./multi-instance.md)),
then optionally add two keys per instance:

- `"tags": ["eu", "retail"]` — group instances for tag-based selection.
- `"cross_instance": false` — opt an instance out of *all* fan-out queries
  (e.g. a sandbox or an internal DB). Default is opt-in.

See [`odoo_config.multi.json.example`](../odoo_config.multi.json.example).

## The three tools

| Tool | Question it answers |
| --- | --- |
| `search_across_instances` | "Show me X everywhere" — merged rows tagged with `_instance`. |
| `aggregate_across_instances` | "Group/total X everywhere" — per-instance rows + additive grand totals. |
| `accounting_health_across_instances` | "AR/AP aging across all clients" — per-instance aging + summed buckets. |

Instance selection on every tool: omit (or `"all"`) for every opted-in
instance, a list of names, or `{"tags": ["eu"]}`.

Every response is the same partial-failure envelope:

```json
{
  "success": true,
  "instances_queried": ["acme", "globex"],
  "results": { "acme": ... },
  "errors": { "globex": "ConnectionError: instance unreachable" },
  "skipped_opt_out": ["internal_sandbox"]
}
```

## Recipe 1 — AR aging sweep (the partner flagship)

> "Which of my clients have receivables over 90 days?"

Call `accounting_health_across_instances(direction="receivable")`. You get,
per client, the aging buckets and top partners, plus `combined_buckets`
summing the 90+ bucket across the whole portfolio — one call, no warehouse.
Killing one client's Odoo container yields that client under `errors` and the
rest under `results`.

## Recipe 2 — Margin / value scan

> "Total untaxed sales by month across the EU entities this year."

```
aggregate_across_instances(
  model="sale.order",
  group_by=["date_order:month"],
  measures=["amount_untaxed:sum"],
  domain=[["date_order", ">=", "2026-01-01"]],
  instances={"tags": ["eu"]},
)
```

Per-instance rows let you drill into a single entity; `combined_measures`
gives the portfolio total. Averages are intentionally *not* combined (an
average of averages is wrong without weights) — request `sum`/`count` for
cross-instance totals.

## Recipe 3 — Module / version census

> "Which clients are still on Odoo 17, and which lack the Accounting module?"

`search_across_instances(model="ir.module.module",
domain=[["name","=","account"],["state","=","installed"]])` tells you which
entities have Accounting; pair with per-instance `get_odoo_profile` for the
server version. Fan-out plus the `_instance` tag turns a manual audit into one
call.

## Scale & politeness

- Fan-out concurrency is bounded (`ODOO_MCP_CROSS_INSTANCE_WORKERS`, default
  4) so you never hammer client production servers.
- Each target instance is counted against its own rate-limit budget
  (`ODOO_MCP_RATE_LIMIT_MODE`).
- For very large fleets, wrap any of the three in a background task:
  `submit_async_task(operation="accounting_health_across_instances", params={...})`
  and poll with `get_async_task`.
- `limit_per_instance` defaults to 50 (max 100), matching single-instance
  search bounds.

## Boundaries (by design)

- **Read-only.** Cross-instance *writes* multiply blast radius; the gated
  write workflow stays single-instance.
- **Per-source redaction.** Each instance's field ACL is applied *inside* the
  fan-out worker, before merge — denied data never enters the merged buffer.
- **Opt-out is honored everywhere** — `cross_instance: false` instances are
  excluded from `"all"`, tag, and (reported, not queried) explicit selection.
- **No sync / no warehouse.** This is live fan-out. If you need historical
  snapshots or sub-second dashboards over 100+ DBs, that is a different tool.
