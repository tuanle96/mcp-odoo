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
| Zero Odoo-side setup | ✅ | ✅ (YOLO) / ❌ (module tiers) | ❌ module install | ❌ |
| Gated write workflow (preview → validate → approve → execute) | ✅ approval token + live `fields_get` validation + explicit confirm + env gate | ❌ direct writes once enabled | Odoo ACL only | Odoo ACL only |
| Human-in-the-loop approval (MCP elicitation confirm form) | ✅ | ❌ | ❌ | ❌ |
| Audit trail without an Odoo module | ✅ JSONL per write-path event | ❌ | n/a (in Odoo) | ✅ (own UI, self-hosted) |
| Multi-instance (several Odoo databases, one server) | ✅ instance-scoped tokens + caches | ❌ one instance per process | ❌ | ✅ multi-tenant |
| Transports | XML-RPC (Odoo 16+) **and** External JSON-2 (Odoo 19+) | XML-RPC | Native in-Odoo endpoint | HTTP/SSE |
| Odoo 22 XML-RPC removal readiness | ✅ JSON-2 today + `generate_json2_payload` migration helper | ❌ | n/a | ❌ |
| Diagnostics (failed-call analysis, access root-cause, upgrade risk, fit/gap) | ✅ 7 tools | ❌ CRUD-focused | ❌ | ❌ |
| OAuth 2.1 resource server on HTTP transport | ✅ RFC 7662/8707/9728 | ❌ | n/a | ❌ |
| Free-text search helper (`query=` builds the ilike domain for the agent) | ✅ | ❌ | ❌ | ❌ |
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
  tokens and schema caches.
- **You want read-only exploration with the absolute minimum of flags** and
  accept direct writes if you later enable them: **mcp-server-odoo** is a
  solid, popular choice.
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

Questions or corrections to this comparison are welcome —
[open an issue](https://github.com/tuanle96/mcp-odoo/issues).
