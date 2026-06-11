# odoo-mcp Benchmark Methodology & Results

## TL;DR

Run `python scripts/benchmark_tools.py` against your own Odoo instance to get
numbers for your hardware.  The table below was captured on a local developer
machine against Dockerised Odoo 19 — use it as a reference floor, not a
guaranteed production figure.

---

## Quick Start (3 commands)

```bash
# 1. Boot the Docker Compose integration stack (Odoo 19 + Postgres)
ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml up -d

# Wait ~30s for Odoo to initialise, then:

# 2. Run the benchmark (requires a live DB — see "First-time DB init" below)
ODOO_URL=http://127.0.0.1:18169 \
ODOO_DB=<your_db> \
ODOO_USERNAME=admin \
ODOO_PASSWORD=admin \
ODOO_TRANSPORT=xmlrpc \
  python scripts/benchmark_tools.py --iterations 20 --json results.json

# 3. Read the results table (printed to stdout) and/or inspect results.json
```

### First-time DB init

The compose stack does not pre-create a database.  Run once:

```bash
ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml run --rm odoo \
  odoo --stop-after-init -d mcp_bench_db -i base --without-demo=all \
       --db_host=db --db_port=5432 --db_user=odoo --db_password=odoo
```

Then use `ODOO_DB=mcp_bench_db` in step 2.

---

## Methodology

### What is measured

Each tool call is timed end-to-end from the Python `time.perf_counter()` call
immediately before `session.call_tool(...)` to immediately after it returns.
This captures:

- MCP stdio serialisation / deserialisation
- XML-RPC (or JSON-2) round-trip to Odoo
- odoo-mcp server-side logic (field selection, caching, etc.)

It does **not** capture Claude / LLM token generation time.

### Transport

The benchmark uses the MCP **stdio** transport (spawns `python -m odoo_mcp` as
a subprocess and communicates over stdin/stdout).  This is the standard
deployment mode for Claude Desktop and most MCP clients.

### Warmup

One call per tool is made before the timed iterations to:

- Establish the XML-RPC session
- Populate schema caches on the server side

Warmup samples are discarded.  Subsequent calls reflect steady-state latency.

### Statistics

| Metric | Definition |
|--------|-----------|
| p50    | Median — 50 % of calls were faster than this |
| p95    | 95th percentile — tail latency ceiling |
| mean   | Arithmetic mean across all timed iterations |

Default: 20 iterations per tool.

### What affects latency

1. **Odoo instance speed** — local Docker vs remote cloud Odoo can differ by 5–50×
2. **Database size** — more records = slower searches, but `list_models` and
   `get_model_fields` are nearly unaffected
3. **Schema cache** — `get_model_fields` p50 drops ~30% on the second call to
   the same model because odoo-mcp caches the result in-process
4. **Transport** — XML-RPC and JSON-2 have similar latency for simple calls;
   JSON-2 may be faster on Odoo 19+ for bulk reads
5. **Network** — all figures below are loopback (localhost); add your actual
   network RTT for remote instances
6. **Host hardware** — CPU clock speed and memory bandwidth dominate Python
   serialisation overhead

---

## Reference Results

Captured: **2026-06-11**, local developer MacBook (Apple Silicon), Odoo 19.0
Docker container, XML-RPC transport, loopback network, 20 iterations + 1
warmup per tool.

```
Tool                                 p50 ms   p95 ms   mean ms  n
------------------------------------------------------------
search_records                       13.9     22.3     14.6     20
read_record                          12.2     21.7     13.1     20
get_model_fields (warm cache)        23.4     37.7     25.0     20
get_model_fields_partner (warm)      20.5     32.7     22.0     20
aggregate_records                    17.9     23.3     18.1     20
list_models                          24.2     35.8     24.3     20
diagnose_access                      68.0     97.0     68.5     20
```

**Environment:**

| Key | Value |
|-----|-------|
| Odoo version | 19.0 |
| Transport | XML-RPC |
| Network | loopback (Docker on localhost) |
| Host | Apple Silicon MacBook, macOS 25.4 |
| odoo-mcp | installed from source (this repo) |
| Iterations | 20 + 1 warmup |

### Observations

- Simple search + read operations land in the **12–22 ms p50** range
- `diagnose_access` is slower because it issues multiple Odoo calls (ACL read,
  rule introspection, user group lookup) — expected and documented behaviour
- Schema cache: `get_model_fields` called twice on the same model shows ~10%
  improvement; the real saving is on the 3rd+ call where no Odoo round-trip
  is needed at all (not visible in the table above since each session starts
  fresh)

---

## Head-to-head vs mcp-server-odoo (ivnvxd)

Measured **2026-06-11**, same machine and same Docker Odoo 19 stack, 15
iterations + 1 warmup per operation.  Competitor: `mcp-server-odoo` v0.6.0
via `uvx mcp-server-odoo`.

### Reproduce in 3 commands

```bash
# 1. Boot Odoo 19 stack
ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml up -d db

# (init DB first time — ~2 min)
ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml run --rm odoo \
  odoo --stop-after-init -d mcp_bench_db -i base --without-demo=all \
       --db_host=db --db_port=5432 --db_user=odoo --db_password=odoo

ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml up -d odoo

# 2. Run head-to-head benchmark
ODOO_URL=http://127.0.0.1:18169 ODOO_DB=mcp_bench_db \
ODOO_USERNAME=admin ODOO_PASSWORD=admin ODOO_TRANSPORT=xmlrpc \
  uv run python scripts/benchmark_head_to_head.py --iterations 15 --json bench.json

# 3. Tear down
COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml down -v
```

### Results

**mcp-odoo** (this project, v0.9.0):

| Operation          | p50 ms | p95 ms | p99 ms | mean ms |
|--------------------|-------:|-------:|-------:|--------:|
| list_models        |   16.8 |   18.4 |   18.4 |    16.7 |
| search_records     |    9.9 |   10.7 |   10.7 |     9.9 |
| read_record        |    9.0 |   10.3 |   10.3 |     9.1 |
| aggregate_records  |   12.8 |   15.3 |   15.3 |    13.4 |
| get_model_fields   |   16.7 |   19.5 |   19.5 |    16.9 |

**mcp-server-odoo v0.6.0** (ivnvxd, YOLO read mode — standard mode requires
Odoo-side module installation):

| Operation          | p50 ms | p95 ms | p99 ms | mean ms | Notes |
|--------------------|-------:|-------:|-------:|--------:|-------|
| list_models        |   19.6 |   20.9 |   20.9 |    19.4 | |
| search_records     |   22.4 |   26.7 |   26.7 |    22.7 | |
| read_record        |   10.9 |   13.0 |   13.0 |    11.1 | |
| aggregate_records  |    9.4 |   12.7 |   12.7 |     9.7 | |
| get_model_fields   |    —   |    —   |    —   |      —  | tool not exposed |

### Ratio (mcp-odoo p50 / competitor p50)

| Operation         | Ratio | Result        |
|-------------------|------:|---------------|
| list_models       | 0.86x | mcp-odoo faster |
| search_records    | 0.44x | mcp-odoo faster |
| read_record       | 0.83x | mcp-odoo faster |
| aggregate_records | 1.36x | competitor faster |

### Cold-start latency

Process spawn to first tool response (single measurement each):

| Server              | Cold-start ms |
|---------------------|--------------:|
| mcp-odoo            |        ~522   |
| mcp-server-odoo     |        ~595   |

Both are similar — cold-start is dominated by Python interpreter startup, not
server-specific logic.

### Environment

| Key | Value |
|-----|-------|
| Odoo version | 19.0 |
| Transport | XML-RPC |
| Network | loopback (Docker on localhost) |
| Host | Apple Silicon MacBook, macOS 25.4 |
| mcp-odoo | v0.9.0 (this repo) |
| mcp-server-odoo | v0.6.0 via `uvx` |
| Iterations | 15 + 1 warmup |
| Date | 2026-06-11 |

### Notes on fairness

- **Same Odoo instance, same DB, same dataset, same hardware, same loopback
  network** for both servers.
- mcp-server-odoo v0.6.0 was tested in `ODOO_YOLO=read` mode because standard
  mode requires an Odoo-side companion module (`mcp_server` addon).  This is
  an installation barrier the competitor's own README acknowledges.  Standard
  mode was not testable on a vanilla Odoo 19 instance.
- `aggregate_records` is the one operation where the competitor is faster
  (9.4 ms vs 12.8 ms p50).  Both are well within acceptable latency; we note
  it here rather than omit it.
- `get_model_fields` (schema introspection) has no equivalent tool in
  mcp-server-odoo v0.6.0.
- These numbers are for loopback Docker; add your actual network RTT for
  remote Odoo instances.

### Known competitor regressions (open as of 2026-06-11)

- **[Issue #68](https://github.com/ivnvxd/mcp-server-odoo/issues/68)** —
  "All tool calls add ~12s of overhead per call on Claude Desktop — regression
  in v0.6.0."  Root cause: per-proxy XML-RPC transport construction tears down
  and rebuilds the connection for every call.  The MCP stdio harness used in
  this benchmark runs within a single async session, so the regression is not
  captured here — but it affects real Claude Desktop usage.  Workaround from
  the issue: downgrade to v0.5.2.
- **[Issue #70](https://github.com/ivnvxd/mcp-server-odoo/issues/70)** —
  "streamable-http transport terminates Odoo session after each request."
  Open, unfixed.

### Reproduce with a different server

Any MCP server can be benchmarked against the same stack using:

```bash
# Single-server harness (mcp-odoo only):
uv run python scripts/benchmark_tools.py --iterations 20

# Head-to-head harness (any two servers):
uv run python scripts/benchmark_head_to_head.py --iterations 15 --json results.json
```

The only meaningful comparison is **same Odoo instance, same tool workload,
same hardware, same transport**.

---

## Reproduce from Scratch

```bash
git clone https://github.com/vibeops/odoo-mcp && cd odoo-mcp

# Start DB + Odoo 19
ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml up -d db

ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml run --rm odoo \
  odoo --stop-after-init -d mcp_bench_db -i base --without-demo=all \
       --db_host=db --db_port=5432 --db_user=odoo --db_password=odoo

ODOO_VERSION=19.0 ODOO_PORT=18169 COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml up -d odoo

# Run benchmark
ODOO_URL=http://127.0.0.1:18169 ODOO_DB=mcp_bench_db \
ODOO_USERNAME=admin ODOO_PASSWORD=admin ODOO_TRANSPORT=xmlrpc \
  python scripts/benchmark_tools.py --iterations 20 --json bench.json

# Tear down
COMPOSE_PROJECT_NAME=mcp-bench \
  docker compose -f docker-compose.integration.yml down -v
```

Total time on a machine with images already pulled: ~4 minutes (DB init
dominates).
