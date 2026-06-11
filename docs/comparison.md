# Odoo MCP server comparison (2026)

Several MCP servers bridge AI agents to Odoo. They make different trade-offs
around setup effort, write safety, transports, and multi-database support.
This page compares the main options honestly so you can pick the right one —
including when that is not this project. Last reviewed: June 2026; check each
project's repository for current details.

## The options

| Project | Install | Odoo-side setup | License |
| --- | --- | --- | --- |
| [odoo-mcp](https://github.com/tuanle96/mcp-odoo) (this project) | `uvx odoo-mcp --setup` | **None** — existing credentials only | MIT |
| [mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo) (ivnvxd) | `uvx mcp-server-odoo` | None in YOLO mode; optional Odoo module for permission tiers | MPL-2.0 |
| [MuK MCP Server](https://apps.odoo.com) (MuK IT) | Odoo App Store module | Install module + configure (admin access required) | Proprietary |
| [odoo-claude-mcp](https://github.com/rosenvladimirov/odoo-claude-mcp) | Self-hosted | Module + infrastructure | AGPL-3.0 |
| [mcp.odoo](https://github.com/Vauxoo/mcp.odoo) (Vauxoo) | CLI + MCP | None | See repo |
| Vertical tools ([finance](https://github.com/yourtechtribe/mcp-odoo-for-finance), [odoo-mcp-improved](https://pypi.org/project/odoo-mcp-improved/)) | Various | Various | Various |

## Feature matrix

| Capability | odoo-mcp (this project) | mcp-server-odoo (ivnvxd) | MuK MCP | odoo-claude-mcp |
| --- | --- | --- | --- | --- |
| Zero Odoo-side setup | ✅ | ✅ (YOLO) / ❌ (module tiers — 404 if companion addon absent) | ❌ module install | ❌ |
| Gated write workflow (preview → validate → approve → execute) | ✅ approval token + live `fields_get` validation + explicit confirm + env gate | ❌ direct writes once enabled | Odoo ACL only | Odoo ACL only |
| Human-in-the-loop approval (MCP elicitation confirm form) | ✅ | ❌ | ❌ | ❌ |
| Audit trail without an Odoo module | ✅ JSONL per write-path event | ❌ | n/a (in Odoo) | ✅ (own UI, self-hosted) |
| Multi-instance (several Odoo databases, one server) | ✅ instance-scoped tokens + caches | ❌ one instance per process | ❌ | ✅ multi-tenant |
| Multi-tenant cross-instance queries (fan-out read, partial-failure, per-source ACL) | ✅ 3 tools — open-source first | ❌ | ❌ | ❌ |
| Transports | XML-RPC (Odoo 16+) **and** External JSON-2 (Odoo 19+) | XML-RPC | Native in-Odoo endpoint | HTTP/SSE |
| Odoo 22 XML-RPC removal readiness | ✅ JSON-2 today + `generate_json2_payload` migration helper | ❌ | n/a | ❌ |
| Diagnostics (failed-call analysis, access root-cause, upgrade risk, fit/gap) | ✅ 7 tools | ❌ CRUD-focused | ❌ | ❌ |
| OAuth 2.1 resource server on HTTP transport | ✅ RFC 7662/8707/9728 | ❌ | n/a | ❌ |
| Free-text search helper (`query=` builds the ilike domain for the agent) | ✅ | ❌ | ❌ | ❌ |
| MCP workflow prompts (end-to-end business processes) | ✅ 10 prompts (invoice approval, PO-to-receipt, customer onboarding, expense review, accounting close, + diagnostics) | ❌ | ❌ | ❌ |
| Field-level ACL (read-path, deny/allow per model per instance) | ✅ enforced across all read tools + knowledge index — open-source first | ❌ | Odoo field security only | ❌ |
| Measured latency — head-to-head vs mcp-server-odoo v0.6.0 (Odoo 19 Docker, XML-RPC, 15 iter) | `search_records` **9.9 ms** p50; `read_record` **9.0 ms**; `list_models` **16.8 ms**; `aggregate_records` 12.8 ms (competitor faster at 9.4 ms — noted honestly). See [docs/benchmarks.md](./benchmarks.md). | `search_records` 22.4 ms; `read_record` 10.9 ms; `list_models` 19.6 ms; `aggregate_records` 9.4 ms. Standard mode requires companion Odoo module ([#68](https://github.com/ivnvxd/mcp-server-odoo/issues/68) ~12 s/call regression in Claude Desktop; [#70](https://github.com/ivnvxd/mcp-server-odoo/issues/70) session drop on streamable-http). | n/a | n/a |
| Release testing against real Odoo | ✅ Docker smoke on Odoo 16/17/18/19 each release | mock-based | n/a | unknown |
| Framework adapter examples (Cursor, OpenAI Agents, LangGraph, CrewAI, n8n) | ✅ verified end-to-end | partial | ❌ | ❌ |

Capabilities of other projects are summarized from their public READMEs as of
June 2026 — verify against the linked repositories before deciding.

## Which one should you pick?

- **You want safe writes in production** — agents that can create invoices or
  update records with an approval gate, an audit trail, and a human confirm
  step: use **odoo-mcp**. No other bridge ships the full preview → validate →
  approve → execute chain.
- **You manage several Odoo databases** (agency, multi-company, staging +
  production): use **odoo-mcp** — instances are isolated down to approval
  tokens and schema caches, and the three cross-instance fan-out tools let you
  query AR/AP aging, sales data, or module state across all of them in one call.
- **You need end-to-end workflow automation** — agents that run invoice approval
  chains, PO-to-receipt flows, or accounting period close with human checkpoints
  baked in: use **odoo-mcp**. The 10 MCP workflow prompts encode the multi-step
  procedure and route all writes through the gated workflow automatically.
- **You handle sensitive fields** (credit limits, margins, PII) and need to
  prevent them from reaching the agent context: use **odoo-mcp**. Field-level
  ACL (deny/allow per model per instance) is enforced across all read tools and
  the knowledge index — the first open-source Odoo MCP server to offer this.
- **You want read-only exploration with the absolute minimum of flags** and
  accept direct writes if you later enable them: **mcp-server-odoo** is a
  solid, popular choice. Note that its standard (non-YOLO) mode requires
  installing a companion Odoo module, and v0.6.0 has an open regression
  ([#68](https://github.com/ivnvxd/mcp-server-odoo/issues/68)) causing ~12 s
  overhead per call in Claude Desktop.
- **You are all-in on the Odoo App Store** and have admin access to install
  modules: look at **MuK MCP Server**.
- **You need a self-hosted multi-tenant deployment with its own audit UI**
  and AGPL is acceptable: look at **odoo-claude-mcp**.
- **You only need one vertical** (accounting, sales): the vertical tools may
  be enough — though odoo-mcp covers the same models plus reusable agent
  prompts for fit/gap, safe writes, and module audits.

## Why we built odoo-mcp this way

1. **Writes are the risk.** A read-only bridge is easy; the hard problem is
   letting an agent mutate an ERP without a "yolo" switch. The write gate
   (approval token bound to the exact payload and instance, TTL, live schema
   validation, explicit confirm, env opt-in, JSONL audit) is the core design.
2. **No Odoo-side module, ever.** Anything that requires installing an app
   or admin access excludes Odoo Online users, locked-down hosts, and anyone
   evaluating quickly. Everything works with the credentials you already have.
3. **The XML-RPC clock is ticking.** Odoo removes XML-RPC in Odoo 22
   (fall 2028). Supporting External JSON-2 today — with a migration preview
   tool — means no forced rewrite later.
4. **Agents should not see what they do not need.** Field-level ACL (opt-in,
   server-side, fail-closed) keeps sensitive columns out of agent context
   without touching Odoo credentials or record rules. Defense in depth.
5. **Prompts encode process, not just capability.** A tool that can post an
   invoice is not the same as a workflow that triages, validates, presents a
   diff, waits for approval, posts, and logs. The 10 MCP workflow prompts are
   the difference — write-bearing steps are routed through the gated workflow
   automatically, every human checkpoint is named, and the required modules are
   verified before any action runs.

Questions or corrections to this comparison are welcome —
[open an issue](https://github.com/tuanle96/mcp-odoo/issues).
