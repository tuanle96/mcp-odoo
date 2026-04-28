# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

## [0.2.0] - 2026-04-28

### Added
- Added `diagnose_access` to inspect readable ACL, record-rule, current-user, and count evidence for the current Odoo credential without sudo or impersonation.
- Added exact side-effect method allowlisting with `ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS`.
- Added static addon scanner findings for computed-field `@api.depends` coverage and CRUD override `super()` return contracts.
- Added Docker Compose smoke coverage for a packaged custom addon XML install/update lifecycle and its XML-defined record rule.

### Changed
- Classified common Odoo side-effect methods such as `message_post`, `action_*`, `button_*`, `*_send*`, `*_post*`, and `*_validate*` separately from unknown methods.
- Updated smoke expectations from 21 to 22 MCP tools.

## [0.1.0] - 2026-04-28

### Added
- Added MCP safety annotations and structured output schemas across the tool surface.
- Added Odoo profile and schema catalog tools for bounded live environment discovery.
- Added safe write workflow tools: `preview_write`, `validate_write`, and fail-closed `execute_approved_write`.
- Hardened safe writes so execution requires a same-process `validate_write` approval record, `confirm=true`, and `ODOO_MCP_ENABLE_WRITES=1`.
- Blocked direct `create`/`write`/`unlink` through `execute_method` and blocked unknown side-effect methods unless explicitly trusted with `ODOO_MCP_ALLOW_UNKNOWN_METHODS=1`.
- Restricted explicit addon source scans to configured `ODOO_ADDONS_PATHS` roots.
- Added local addon source scanning, structured domain building, business pack reporting, runtime health, and 5 MCP prompts for agent workflows.

### Changed
- Reworked project documentation for a polished open-source GitHub presentation, including a concise README, client configuration guide, architecture notes, testing guide, contributing guide, security policy, support guide, code of conduct, package metadata URLs, and sdist documentation inclusion.
- Hardened HTTP runtime startup so non-local binds require `--allow-remote-http` or `MCP_ALLOW_REMOTE_HTTP=1`.
- Expanded the Docker Compose smoke harness to require 21 tools, 5 prompts, and live checks for the new agent workflow tools.

## [0.0.4] - 2026-04-28

### Added
- Added a Docker Compose integration smoke harness that boots disposable Odoo 16.0, 17.0, 18.0, and 19.0 stacks and validates live HTTP, XML-RPC, and MCP stdio behavior.
- Added Odoo 19 External JSON-2 transport support behind `ODOO_TRANSPORT=json2`, with bearer API-key authentication through `ODOO_API_KEY`.
- Added Odoo 19 JSON-2 smoke validation that generates a disposable API key and verifies both direct `/json/2` calls and MCP stdio calls.
- Added opt-in MCP Streamable HTTP and SSE runtime support through `--transport`, `--host`, `--port`, and `--path`.
- Added read-only typed tools: `list_models`, `get_model_fields`, `search_records`, and `read_record`.
- Added diagnostic/report tools for agent workflows: `diagnose_odoo_call`, `inspect_model_relationships`, `generate_json2_payload`, `upgrade_risk_report`, and `fit_gap_report`.
- Added `ODOO_JSON2_DATABASE_HEADER` support so JSON-2 calls send `X-Odoo-Database` by default and can opt out for host/dbfilter-routed deployments.
- Added MCP Inspector smoke validation for `tools/list` over stdio and Streamable HTTP.
- Added generic client configuration examples for stdio, Docker stdio, and Streamable HTTP.

### Changed
- Documented the current compatibility posture for the planned 0.0.4 release: XML-RPC remains the default transport, while Odoo's External JSON-2 API is available for Odoo 19.
- Documented JSON-2 named argument mapping, per-call transaction behavior, Odoo-shaped error preservation, and debug redaction defaults.
- Updated README setup guidance for Claude Desktop on macOS, including the expected config file location and the recommendation to use an absolute Python path.
- Updated Docker usage guidance to build and run the local `mcp/odoo:latest` image with the current Odoo environment variables.
- Clarified the current MCP surface as 12 tools and 4 resource URI patterns, matching the implemented server.
- Updated release workflow intent so PyPI publishing stays behind tests, build, and MCP Inspector runtime smoke validation.

### Notes
- Odoo's current 19.0 documentation source says XML-RPC and JSON-RPC endpoints are deprecated and scheduled for removal in Odoo 20 (fall 2026), with External JSON-2 as the replacement.
- JSON-2 is explicit opt-in. XML-RPC carries the database name per request; JSON-2 uses bearer auth and this server sends `X-Odoo-Database` by default for multi-database deployments.

## [0.0.3] - 2025-03-18

### Fixed
- Fixed `OdooClient` class by adding missing methods: `get_models()`, `get_model_info()`, `get_model_fields()`, `search_read()`, and `read_records()`
- Ensured compatibility with different Odoo versions by using only basic fields when retrieving model information

### Added
- Support for retrieving all models from an Odoo instance
- Support for retrieving detailed information about specific models
- Support for searching and reading records with various filtering options

## [0.0.2] - 2025-03-18

### Fixed
- Added missing dependencies in pyproject.toml: `mcp>=0.1.1`, `requests>=2.31.0`, `xmlrpc>=0.4.1`

## [0.0.1] - 2025-03-18

### Added
- Initial release with basic Odoo XML-RPC client support
- MCP Server integration for Odoo
- Command-line interface for quick setup and testing
