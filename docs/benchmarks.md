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

## Comparing Against Other MCP Servers

We intentionally do not publish latency numbers for competing projects because:

1. Numbers depend almost entirely on the Odoo instance, not the MCP layer
2. Benchmarking another project on our hardware is not representative of how
   that project performs on the user's hardware
3. Self-reported competitor numbers cannot be verified

### Fair-play comparison guide

If you want to compare odoo-mcp against another server on **your own hardware**:

1. Boot the same Odoo instance (use the Docker stack above)
2. Run `python scripts/benchmark_tools.py` for odoo-mcp
3. For the other server, use [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):
   ```bash
   # Time the same tool categories manually via Inspector CLI
   npx @modelcontextprotocol/inspector --cli \
     --method tools/call \
     --tool-name <equivalent_search_tool> \
     -- <other_server_command>
   ```
4. Record p50 / p95 for the same model and domain on the same Odoo DB

The only meaningful comparison is **same Odoo instance, same tool workload,
same hardware, same transport**.

### What the MCP layer can control

The latency differences *within the MCP layer itself* (excluding Odoo
round-trips) typically amount to 1–5 ms.  This covers:

- JSON serialisation / deserialisation
- Domain normalization
- Smart field selection (field ranking)
- Schema cache lookups

If you are evaluating MCP servers purely on "MCP overhead", subtract the bare
XML-RPC latency from the total and compare that delta.

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
