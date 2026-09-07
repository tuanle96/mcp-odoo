#!/usr/bin/env bash
# Start odoo-mcp in request identity mode on localhost (Algorithma vNext prototype).
#
# WHERE comes from ODOO_CONFIG_FILE (instance url/db, no credentials);
# WHO comes from the X-User-Email / X-Odoo-Api-Key headers on every request.
# Writes stay OFF unless you export ODOO_MCP_ENABLE_WRITES=1 yourself.
#
#   scripts/run_request_mode.sh                 # demo config, port 8010
#   MCP_HTTP_PORT=8020 scripts/run_request_mode.sh
#   ODOO_CONFIG_FILE=/path/odoo_config.json scripts/run_request_mode.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export ODOO_MCP_IDENTITY_MODE=request
export ODOO_CONFIG_FILE="${ODOO_CONFIG_FILE:-examples/algorithma-vnext/odoo_config.example.json}"
export ODOO_MCP_FIELD_POLICY_FILE="${ODOO_MCP_FIELD_POLICY_FILE:-examples/algorithma-vnext/odoo_mcp_policy.algorithma.json}"
export ODOO_MCP_AUDIT_LOG="${ODOO_MCP_AUDIT_LOG:-$PWD/audit-request-mode.jsonl}"
# Tools whose state is shared process-wide across users stay off in request
# mode until they are made identity-aware (health_check warns otherwise).
export ODOO_MCP_TOOLS_EXCLUDE="${ODOO_MCP_TOOLS_EXCLUDE:-index_knowledge,search_knowledge,knowledge_stats,submit_async_task,get_async_task,cancel_async_task,list_async_tasks}"
# Shared credentials must not exist in this mode; unset any that leaked in.
unset ODOO_USERNAME ODOO_PASSWORD ODOO_API_KEY

exec uv run odoo-mcp \
  --transport streamable-http \
  --host "${MCP_HTTP_HOST:-127.0.0.1}" \
  --port "${MCP_HTTP_PORT:-8010}" \
  --path "${MCP_HTTP_PATH:-/mcp}"
