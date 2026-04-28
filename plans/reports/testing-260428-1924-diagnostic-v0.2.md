# Test Report - 2026-04-28 19:23 +07 - Diagnostic v0.2

Updated: 2026-04-28 21:20 +07 after release prep for package version `0.2.0`.

## Scope

- Repository: `/Users/justin/Dev/VibeLab/mcp-odoo`
- Branch: `main`
- Baseline HEAD: `d6d27ac58916a0de3d165e415345934cd8449fe4`
- Tested change set: local uncommitted Diagnostic v0.2 edits.
- New/changed behavior under test:
  - MCP tool `diagnose_access`
  - method safety classification and exact side-effect allowlist
  - static addon scanner findings for computed fields and CRUD overrides
  - expected MCP surface count from 21 to 22
  - docs/smoke expectation updates
  - bespoke complex Odoo record-rule diagnosis smoke across Odoo 16.0, 17.0,
    18.0, and 19.0
  - packaged custom addon XML install/update lifecycle smoke across Odoo 16.0,
    17.0, 18.0, and 19.0
  - release metadata bump from package `0.1.0` to `0.2.0`
  - source distribution inclusion for the Docker smoke script, Compose file, and
    packaged addon smoke fixture

## Verdict

Local verification passes and real Docker Compose smoke passes against disposable
Odoo 16.0, 17.0, 18.0, and 19.0 instances. The claim supported by this report is:
code quality gates, type checking, unit tests, whitespace diff hygiene, build,
package metadata checks, live admin Odoo smoke checks, and live restricted-user
Odoo smoke checks pass on this machine for the current local change set.

The live smoke result was `failed: []` and all four Odoo versions returned
`status: "passed"`. The restricted-user checks ran as a real disposable
non-admin Odoo user, not as `admin`, not with `sudo`, and not via impersonation
inside the MCP tool. The bespoke record-rule checks seeded real `res.partner`
records, tags, a dedicated rule-auditor user, and a custom `ir.rule` in each
disposable Odoo database; `diagnose_access` then observed the rule-filtered
count `2` from `4` expected records and emitted `record_rule_filter_likely` on
every tested Odoo version. The packaged-addon checks mounted a real addon from
`tests/fixtures/odoo_addons`, installed it with Odoo's module CLI, updated it
with Odoo's module CLI, loaded its XML data/rule records, and then diagnosed the
XML-defined rule as a dedicated fixture credential on every tested Odoo version.

## Workspace State Before Report

Command:

```bash
pwd && git status --short
```

Observed output:

```text
/Users/justin/Dev/VibeLab/mcp-odoo
 M CHANGELOG.md
 M README.md
 M docker-compose.integration.yml
 M docs/architecture.md
 M docs/testing.md
 M scripts/odoo_compose_smoke.py
 M src/odoo_mcp/agent_tools.py
 M src/odoo_mcp/diagnostics.py
 M src/odoo_mcp/server.py
 M tests/test_diagnostics.py
 M tests/test_server.py
?? .omc/
?? plans/
?? tests/fixtures/
```

Note: `.omc/` and existing `plans/` were already untracked research/report surfaces.
This report adds one new file under `plans/reports/`. The packaged-addon smoke
fixture is intentionally untracked until this change set is staged.

## Bugs And Gaps Fixed After First Real Smoke

### Fixed: Odoo 19 `res.users.groups_id` Schema Change

Observed failure signal during the first Odoo 19 real smoke:

```text
ValueError: Invalid field 'groups_id' on 'res.users'
```

Root cause:

- Odoo 16.0, 17.0, and 18.0 expose user group membership as `groups_id`.
- Odoo 19.0 exposes direct groups as `group_ids` and effective/inherited groups
  as `all_group_ids`.
- The first `diagnose_access` implementation hardcoded `groups_id`.

Fix implemented:

- `diagnose_access` now calls `res.users.fields_get` first and reads only fields
  present on the live Odoo server.
- Current-user evidence now records `group_field`, `all_group_field`,
  `direct_group_ids`, and effective `group_ids`.
- For Odoo 19.0, the real smoke now reports `group_field: "group_ids"` and
  `all_group_field: "all_group_ids"` for restricted XML-RPC and restricted
  JSON-2.

### Fixed: Restricted-User Live Coverage Was Missing

Earlier report gap:

```text
No real Odoo database with a restricted non-admin user was used.
```

Fix implemented:

- The Docker smoke harness now creates a disposable internal non-admin user:
  `mcp.smoke.restricted@example.test`.
- It runs MCP `diagnose_access` with that user's real credentials on Odoo 16.0,
  17.0, 18.0, and 19.0 over XML-RPC.
- It also generates a restricted user's Odoo 19 API key and runs the same
  `diagnose_access` path over JSON-2.
- The smoke asserts the returned `current_user.uid` matches the restricted user
  and asserts `metadata_used.sudo` and `metadata_used.impersonation` are false.

### Fixed: Odoo 19 Restricted JSON-2 API Key Generation

Observed intermediate failures while extending the smoke harness:

- Odoo 19 required an API-key expiration date.
- Passing a `date` object caused a datetime comparison failure.

Fix implemented:

- The smoke harness now creates Odoo 19 API keys with
  `datetime.now() + timedelta(days=7)` for both admin and restricted-user
  JSON-2 smoke paths.

### Added: Bespoke Complex Record-Rule Coverage

Earlier report gap:

```text
No custom record-rule scenario was exercised.
```

Fix implemented:

- The Docker smoke harness now seeds a custom `res.groups` group, a real
  `mcp.smoke.rule.auditor@example.test` internal user, four tagged/owned
  `res.partner` records, and a custom global `ir.rule` named
  `MCP smoke complex partner visibility` in each disposable database.
- The rule uses a nested domain with `&`, `|`, `user.id`, a many2many tag filter,
  and ownership:

```text
['&', ('active', '=', True), '|', ('user_id', '=', user.id), ('category_id', 'in', [visible_tag.id])]
```

- The fixture verifies inside Odoo that the rule-auditor user sees exactly two
  of four seeded partners before MCP is called.
- MCP then calls `diagnose_access` as that same rule-auditor user, without
  `sudo` and without impersonation, expecting four records and observing the
  rule-filtered count of two.
- The smoke asserts `record_rule_filter_likely`, asserts the seeded rule name is
  present in `rules.active`, and asserts ACL/rule metadata was readable.

Intermediate fixes found while adding this coverage:

- Odoo 19 `res.groups` no longer exposes `category_id` in this runtime image, so
  fixture seeding now writes `category_id` only when the live model has that
  field.
- Odoo blocks `res.users.read` for the fixture credentials even after a smoke
  ACL grant. The smoke now treats only
  `metadata_errors[].stage == "res.users.read"` as an expected partial
  user-evidence limitation while still requiring rule metadata, filtered count,
  and record-rule diagnosis to pass.

### Added: Packaged Custom Addon XML Install/Update Lifecycle

New user-requested gap:

```text
test lifecycle của một packaged custom addon XML install/update luôn đi
```

Fix implemented:

- Added fixture addon `mcp_smoke_access` under
  `tests/fixtures/odoo_addons/mcp_smoke_access`.
- Mounted `tests/fixtures/odoo_addons` into each disposable Odoo container at
  `/mnt/extra-addons`.
- Installed the addon with Odoo's module CLI using `-i mcp_smoke_access`.
- Updated the already installed addon with Odoo's module CLI using
  `-u mcp_smoke_access`.
- Loaded addon XML that creates a custom group, an `ir.model.access` row, four
  `res.partner` records, and an XML-defined `ir.rule` named
  `MCP packaged partner visibility`.
- Created a real packaged-rule auditor user, assigned one XML-loaded
  partner to that user, verified Odoo itself returns exactly two visible
  partners from four XML-loaded IDs, and then ran MCP `diagnose_access` as that
  user.

The XML-defined rule domain is:

```text
['&', ('active', '=', True), '|', ('ref', '=', 'MCP-PACKAGED-RULE-VISIBLE'), ('user_id', '=', user.id)]
```

Real bug caught while adding this:

- First attempt bound the packaged rule only to the packaged group.
- Odoo 16.0 then returned `visible_count: 4` instead of `2` because group-bound
  record rules are combined with other applicable group rules using OR
  semantics.
- The smoke correctly failed on Odoo 16.0.
- Fix: make the packaged rule global for the fixture, deactivate the earlier
  shell-seeded complex rule before the packaged-addon lifecycle starts, and run
  the packaged-addon diagnosis at the end of the version smoke.
- Focused Odoo 16.0 rerun then passed, followed by the full 16.0/17.0/18.0/19.0
  matrix passing.

## Gate Results

| Gate | Command | Exit | Evidence |
| --- | --- | ---: | --- |
| Ruff lint | `uv run python -m ruff check .` | 0 | `All checks passed!` |
| Mypy | `uv run python -m mypy src` | 0 | `Success: no issues found in 6 source files` |
| Pytest | `uv run python -m pytest -vv` | 0 | `60 passed in 0.53s` after release prep |
| Whitespace diff | `git diff --check` | 0 | no output |
| Build | `rm -rf dist && uv run python -m build` | 0 | built `odoo_mcp-0.2.0.tar.gz` and `odoo_mcp-0.2.0-py3-none-any.whl`; sdist includes smoke script, Compose file, and addon fixture |
| Twine check | `uv run python -m twine check dist/*` | 0 | `odoo_mcp-0.2.0` wheel `PASSED`, sdist `PASSED` |
| Sdist content check | `tar -tf dist/odoo_mcp-0.2.0.tar.gz \| rg ...` | 0 | found `docker-compose.integration.yml`, `scripts/odoo_compose_smoke.py`, and packaged addon XML fixture files |
| Docker Compose smoke | `uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py --versions 16.0 17.0 18.0 19.0 --timeout 360 --inspector-smoke` | 0 | 4 versions passed, `failed: []`; includes bespoke shell-seeded rule and packaged addon XML install/update lifecycle |
| Docker cleanup | `docker ps/network/volume --filter name=mcp-odoo-smoke` | 0 | no leftover containers, networks, or volumes |

## Exact Command Outputs

### Ruff

```text
All checks passed!
```

### Mypy

```text
Success: no issues found in 6 source files
```

### Pytest Header And Summary

```text
platform darwin -- Python 3.12.13, pytest-9.0.3, pluggy-1.6.0 -- /Users/justin/Dev/VibeLab/mcp-odoo/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /Users/justin/Dev/VibeLab/mcp-odoo
configfile: pyproject.toml
plugins: anyio-4.13.0
collected 60 items
...
============================== 60 passed in 0.53s ==============================
```

### Twine Check

```text
Checking dist/odoo_mcp-0.2.0-py3-none-any.whl: PASSED
Checking dist/odoo_mcp-0.2.0.tar.gz: PASSED
```

## Real Odoo Docker Compose Smoke

Docker runtime used:

```text
Docker version 29.4.0, build 9d7ad9f
Docker Compose version v5.1.2
```

Command:

```bash
uv run --python 3.12 --with-editable . scripts/odoo_compose_smoke.py \
  --versions 16.0 17.0 18.0 19.0 \
  --timeout 360 \
  --inspector-smoke
```

What this command did:

- started disposable PostgreSQL/Odoo Compose projects,
- initialized one clean Odoo database per version with `base`,
- created a disposable restricted internal user per database,
- tested direct XML-RPC for Odoo 16.0, 17.0, 18.0, 19.0,
- generated an Odoo 19 API key and tested direct JSON-2,
- mounted `tests/fixtures/odoo_addons` into the Odoo containers,
- installed and updated the packaged `mcp_smoke_access` addon through Odoo's
  module CLI,
- loaded the addon's XML group, ACL, partner records, and record rule,
- started this MCP server over stdio and listed tools/resources/prompts,
- called live MCP read/diagnostic/agent tools against real Odoo,
- ran `diagnose_access` as the restricted user over XML-RPC on all versions,
- ran `diagnose_access` as the restricted user over JSON-2 on Odoo 19,
- seeded and tested a bespoke complex `res.partner` record rule on all versions,
- ran bespoke complex record-rule `diagnose_access` over XML-RPC on all versions,
- ran bespoke complex record-rule `diagnose_access` over JSON-2 on Odoo 19,
- deactivated the shell-seeded complex rule before packaged-addon diagnosis,
- ran packaged XML record-rule `diagnose_access` over XML-RPC on all versions,
- ran packaged XML record-rule `diagnose_access` over JSON-2 on Odoo 19,
- ran MCP Inspector `tools/list` over stdio,
- for Odoo 19, also ran MCP stdio over JSON-2,
- for Odoo 19, also ran Streamable HTTP MCP over JSON-2,
- for Odoo 19, also ran MCP Inspector `tools/list` over Streamable HTTP,
- tore down the Compose containers, networks, and volumes after each version.

Per-version evidence:

| Odoo | Project | DB | Odoo port | MCP port | Direct XML-RPC | Direct JSON-2 | MCP stdio | Restricted `diagnose_access` | Bespoke complex rule `diagnose_access` | Packaged addon XML lifecycle | MCP stdio JSON-2 | MCP HTTP | Inspector | Status |
| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 16.0 | `mcp-odoo-smoke-160` | `mcp_smoke_160` | 18069 | 19069 | partner sample 1, model `Contact` | n/a | 22 tools, prompt 5, resource 1, templates 3 | XML-RPC uid 6, `groups_id`, actual count 3, `metadata_access_unavailable` | XML-RPC uid 7, `groups_id`, expected 4, actual 2, `record_rule_filter_likely`, seeded rule active | install/update passed, module `16.0.1.0.0`, XML-RPC expected 4, actual 2, `record_rule_filter_likely` | n/a | n/a | stdio 22 tools | passed |
| 17.0 | `mcp-odoo-smoke-170` | `mcp_smoke_170` | 18070 | 19070 | partner sample 1, model `Contact` | n/a | 22 tools, prompt 5, resource 1, templates 3 | XML-RPC uid 6, `groups_id`, actual count 3, `metadata_access_unavailable` | XML-RPC uid 7, `groups_id`, expected 4, actual 2, `record_rule_filter_likely`, seeded rule active | install/update passed, module `17.0.1.0.0`, XML-RPC expected 4, actual 2, `record_rule_filter_likely` | n/a | n/a | stdio 22 tools | passed |
| 18.0 | `mcp-odoo-smoke-180` | `mcp_smoke_180` | 18071 | 19071 | partner sample 1, model `Contact` | n/a | 22 tools, prompt 5, resource 1, templates 3 | XML-RPC uid 6, `groups_id`, actual count 3, `metadata_access_unavailable` | XML-RPC uid 7, `groups_id`, expected 4, actual 2, `record_rule_filter_likely`, seeded rule active | install/update passed, module `18.0.1.0.0`, XML-RPC expected 4, actual 2, `record_rule_filter_likely` | n/a | n/a | stdio 22 tools | passed |
| 19.0 | `mcp-odoo-smoke-190` | `mcp_smoke_190` | 18072 | 19072 | partner sample 1, model `Contact` | partner sample 1 | 22 tools, prompt 5, resource 1, templates 3 | XML-RPC uid 5, `group_ids` + `all_group_ids`, actual count 3; JSON-2 uid 5, same fields | XML-RPC uid 6 and JSON-2 uid 6, `group_ids`, expected 4, actual 2, `record_rule_filter_likely`, seeded rule active | install/update passed, module `19.0.1.0.0`, XML-RPC and JSON-2 expected 4, actual 2, `record_rule_filter_likely` | 22 tools, prompt 5, resource 1, templates 3 | partner sample 1, 22 tools | stdio 22 tools, HTTP 22 tools | passed |

Observed tool surface in every successful MCP smoke:

```text
build_domain
business_pack_report
diagnose_access
diagnose_odoo_call
execute_approved_write
execute_method
fit_gap_report
generate_json2_payload
get_model_fields
get_odoo_profile
health_check
inspect_model_relationships
list_models
preview_write
read_record
scan_addons_source
schema_catalog
search_employee
search_holidays
search_records
upgrade_risk_report
validate_write
```

Final smoke summary excerpt:

```json
{
  "failed": [],
  "passed": [
    {"version": "16.0", "status": "passed", "project": "mcp-odoo-smoke-160"},
    {"version": "17.0", "status": "passed", "project": "mcp-odoo-smoke-170"},
    {"version": "18.0", "status": "passed", "project": "mcp-odoo-smoke-180"},
    {"version": "19.0", "status": "passed", "project": "mcp-odoo-smoke-190"}
  ]
}
```

Restricted-user evidence from final smoke:

```json
{
  "16.0": {
    "uid": 6,
    "group_field": "groups_id",
    "all_group_field": null,
    "diagnosis_codes": ["metadata_access_unavailable"],
    "metadata_error_count": 2,
    "actual_count": 3
  },
  "17.0": {
    "uid": 6,
    "group_field": "groups_id",
    "all_group_field": null,
    "diagnosis_codes": ["metadata_access_unavailable"],
    "metadata_error_count": 2,
    "actual_count": 3
  },
  "18.0": {
    "uid": 6,
    "group_field": "groups_id",
    "all_group_field": null,
    "diagnosis_codes": ["metadata_access_unavailable"],
    "metadata_error_count": 2,
    "actual_count": 3
  },
  "19.0_xmlrpc": {
    "uid": 5,
    "group_field": "group_ids",
    "all_group_field": "all_group_ids",
    "diagnosis_codes": ["metadata_access_unavailable"],
    "metadata_error_count": 2,
    "actual_count": 3
  },
  "19.0_json2": {
    "uid": 5,
    "group_field": "group_ids",
    "all_group_field": "all_group_ids",
    "diagnosis_codes": ["metadata_access_unavailable"],
    "metadata_error_count": 2,
    "actual_count": 3
  }
}
```

Interpretation:

- `metadata_access_unavailable` is expected for this restricted internal user
  because the user cannot read `ir.model` / access metadata.
- The important pass condition is that the tool returns a sanitized partial
  diagnosis instead of crashing, and still reports the actual `current_user.uid`
  and live count.
- On Odoo 19.0 the final run no longer attempts the removed `groups_id` field;
  it uses `group_ids` and `all_group_ids`.

## Bespoke Complex Record-Rule Evidence

Fixture shape created inside each disposable Odoo database:

- User: `mcp.smoke.rule.auditor@example.test`
- Group: `MCP Smoke Rule Auditor`
- Rule: `MCP smoke complex partner visibility`
- Model: `res.partner`
- Seeded records: four partners, consisting of one visible-tag partner, one
  owned hidden-tag partner, one unowned hidden-tag partner, and one untagged
  partner.
- Expected diagnosis input: all four record IDs with `expected_count: 4`.
- Expected Odoo-visible count for the rule-auditor user: `2`.

Rule domain:

```text
['&', ('active', '=', True), '|', ('user_id', '=', user.id), ('category_id', 'in', [visible_tag.id])]
```

Per-version result from the final full matrix:

```json
{
  "16.0_xmlrpc": {
    "uid": 7,
    "fixture_group_field": "groups_id",
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"],
    "seeded_rule_active": true
  },
  "17.0_xmlrpc": {
    "uid": 7,
    "fixture_group_field": "groups_id",
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"],
    "seeded_rule_active": true
  },
  "18.0_xmlrpc": {
    "uid": 7,
    "fixture_group_field": "groups_id",
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"],
    "seeded_rule_active": true
  },
  "19.0_xmlrpc": {
    "uid": 6,
    "fixture_group_field": "group_ids",
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"],
    "seeded_rule_active": true
  },
  "19.0_json2": {
    "uid": 6,
    "fixture_group_field": "group_ids",
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"],
    "seeded_rule_active": true
  }
}
```

Interpretation:

- The complex custom rule was inserted into live Odoo databases by `odoo shell`
  during Docker smoke, not inferred from docs or unit fixtures.
- `metadata_access_unavailable` and `res.users.read` are expected partial
  user-evidence limitations for this rule-auditor fixture credential.
- The critical diagnosis proof is that ACL/rule metadata remained readable, the
  seeded rule appeared under `rules.active`, the count dropped from expected `4`
  to actual `2`, and `record_rule_filter_likely` was emitted across the matrix.

## Packaged Custom Addon Lifecycle Evidence

Addon fixture:

- Path: `tests/fixtures/odoo_addons/mcp_smoke_access`
- Manifest: `__manifest__.py`
- XML security/rule data:
  `security/mcp_smoke_access_security.xml`
- XML partner data:
  `data/mcp_smoke_partners.xml`

Lifecycle commands executed inside each disposable Odoo Compose project:

```bash
odoo --stop-after-init -d <db> -i mcp_smoke_access \
  --addons-path /mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons

odoo --stop-after-init -d <db> -u mcp_smoke_access \
  --addons-path /mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons
```

XML-defined rule:

- Name: `MCP packaged partner visibility`
- Model: `res.partner`
- XML-loaded record IDs: four partners
- Expected count sent to MCP: `4`
- Expected Odoo-visible count for the packaged-rule auditor: `2`
- Domain:

```text
['&', ('active', '=', True), '|', ('ref', '=', 'MCP-PACKAGED-RULE-VISIBLE'), ('user_id', '=', user.id)]
```

Per-version result from the final full matrix:

```json
{
  "16.0_xmlrpc": {
    "module_state": "installed",
    "module_installed_version": "16.0.1.0.0",
    "lifecycle": {"install": "passed", "update": "passed"},
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"]
  },
  "17.0_xmlrpc": {
    "module_state": "installed",
    "module_installed_version": "17.0.1.0.0",
    "lifecycle": {"install": "passed", "update": "passed"},
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"]
  },
  "18.0_xmlrpc": {
    "module_state": "installed",
    "module_installed_version": "18.0.1.0.0",
    "lifecycle": {"install": "passed", "update": "passed"},
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"]
  },
  "19.0_xmlrpc": {
    "module_state": "installed",
    "module_installed_version": "19.0.1.0.0",
    "lifecycle": {"install": "passed", "update": "passed"},
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"]
  },
  "19.0_json2": {
    "module_state": "installed",
    "module_installed_version": "19.0.1.0.0",
    "expected_count": 4,
    "actual_count": 2,
    "diagnosis_codes": [
      "metadata_access_unavailable",
      "acl_denied_likely",
      "record_rule_filter_likely"
    ],
    "metadata_error_stages": ["res.users.read"]
  }
}
```

Interpretation:

- This verifies real addon XML data loading, not only shell-created database
  records.
- The update path is a real Odoo module update (`-u`), not a no-op unit fixture.
- `metadata_access_unavailable` with `res.users.read` remains an expected
  partial user-evidence limitation for these fixture credentials.
- The critical proof is install passed, update passed, the XML rule remained
  active after update, Odoo's own count under the packaged user was `2`, and MCP
  diagnosed expected `4` versus actual `2` as `record_rule_filter_likely`.

Docker cleanup verification after the smoke:

```bash
docker ps -a --filter name=mcp-odoo-smoke --format '{{.Names}}\t{{.Status}}'
docker network ls --filter name=mcp-odoo-smoke --format '{{.Name}}'
docker volume ls --filter name=mcp-odoo-smoke --format '{{.Name}}'
```

All three cleanup commands returned no output.

## Pytest Test Inventory

All tests below were reported `PASSED` by `uv run python -m pytest -vv`.

```text
tests/test_cli.py::test_parse_args_defaults_to_stdio
tests/test_cli.py::test_cli_applies_streamable_http_runtime_settings
tests/test_cli.py::test_cli_rejects_remote_http_bind_without_explicit_opt_in
tests/test_cli.py::test_cli_health_prints_non_secret_runtime_json
tests/test_config.py::test_load_config_returns_environment_values_when_complete
tests/test_config.py::test_load_config_includes_optional_json2_environment_values
tests/test_config.py::test_get_odoo_client_defaults_json2_database_header_on
tests/test_config.py::test_get_odoo_client_allows_json2_database_header_opt_out
tests/test_config.py::test_load_config_returns_local_config_file_when_environment_missing
tests/test_config.py::test_load_config_raises_when_no_environment_or_config_file
tests/test_diagnostics.py::test_generate_json2_payload_builds_search_read_preview_without_client_side_effects
tests/test_diagnostics.py::test_generate_json2_payload_omits_x_odoo_database_when_disabled
tests/test_diagnostics.py::test_diagnose_odoo_call_marks_write_create_unlink_as_destructive_without_execution
tests/test_diagnostics.py::test_diagnose_odoo_call_marks_common_read_methods_as_read_only
tests/test_diagnostics.py::test_diagnose_odoo_call_marks_common_side_effect_methods
tests/test_diagnostics.py::test_diagnose_odoo_call_warns_unknown_positional_json2_method_without_execution
tests/test_diagnostics.py::test_odoo_error_redacts_debug_by_default_and_allows_explicit_debug
tests/test_diagnostics.py::test_odoo_error_parser_accepts_top_level_json_error_wrapper
tests/test_diagnostics.py::test_inspect_model_relationships_groups_relational_and_required_fields_from_metadata
tests/test_diagnostics.py::test_upgrade_risk_report_flags_odoo20_rpc_removal_and_destructive_methods
tests/test_diagnostics.py::test_fit_gap_report_normalizes_requirement_classifications_and_safe_discovery_calls
tests/test_odoo_client.py::test_client_initialization_creates_common_and_object_xmlrpc_endpoints
tests/test_odoo_client.py::test_execute_method_passes_database_credentials_model_method_args_and_kwargs
tests/test_odoo_client.py::test_search_read_passes_domain_as_single_positional_argument_with_keyword_options
tests/test_odoo_client.py::test_read_records_passes_ids_as_single_positional_argument_with_fields_kwarg
tests/test_odoo_client.py::test_get_model_info_passes_fields_as_keyword_argument
tests/test_odoo_client.py::test_profile_helpers_read_version_context_and_installed_modules
tests/test_odoo_client.py::test_json2_initialization_validates_bearer_without_xmlrpc
tests/test_odoo_client.py::test_json2_requests_omit_x_odoo_database_header_when_configured
tests/test_odoo_client.py::test_json2_search_read_maps_common_positional_args_to_named_payload
tests/test_odoo_client.py::test_json2_read_records_maps_ids_to_route_payload
tests/test_odoo_client.py::test_json2_write_maps_record_ids_and_values_to_named_payload
tests/test_odoo_client.py::test_json2_rejects_unknown_methods_with_positional_args
tests/test_odoo_client.py::test_json2_http_error_preserves_odoo_error_shape_and_redacts_debug_by_default
tests/test_odoo_client.py::test_json2_get_server_version_uses_web_version_endpoint
tests/test_server.py::test_server_import_initializes_fastmcp_with_current_sdk_without_lifespan
tests/test_server.py::test_server_registers_expected_tools_and_resources_without_lifespan
tests/test_server.py::test_tools_expose_safety_annotations_and_output_schemas
tests/test_server.py::test_resources_are_json_with_assistant_annotations
tests/test_server.py::test_domain_normalization_accepts_json_object_and_standard_domain_list
tests/test_server.py::test_safety_helpers_reject_bad_model_names_and_bounds_limits
tests/test_server.py::test_lifespan_is_lazy_and_preview_tools_call_tool_succeed_when_client_raises
tests/test_server.py::test_report_tools_do_not_execute_candidate_methods
tests/test_server.py::test_inspect_model_relationships_uses_only_get_model_fields_for_live_metadata
tests/test_server.py::test_new_tools_return_stable_top_level_response_keys
tests/test_server.py::test_safe_write_preview_validate_and_execute_gates
tests/test_server.py::test_execute_method_validates_model_and_method_before_client_call
tests/test_server.py::test_execute_method_blocks_direct_writes_and_unknown_methods
tests/test_server.py::test_execute_method_can_opt_into_unknown_methods
tests/test_server.py::test_execute_method_allows_exact_side_effect_allowlist
tests/test_server.py::test_validate_write_only_registers_live_metadata_approvals
tests/test_server.py::test_validate_write_rejects_empty_live_metadata_for_unlink
tests/test_server.py::test_execute_approved_write_runs_only_after_all_gates
tests/test_server.py::test_schema_catalog_caches_and_business_pack_uses_live_metadata
tests/test_server.py::test_domain_builder_and_addon_scanner
tests/test_server.py::test_profile_health_and_prompts_are_available
tests/test_server.py::test_diagnose_access_reports_acl_rules_and_count_mismatch
tests/test_server.py::test_diagnose_access_uses_odoo19_group_ids_field
tests/test_server.py::test_diagnose_access_reports_missing_permission_metadata
tests/test_server.py::test_diagnose_access_survives_record_rule_read_failure
```

## Feature Coverage Evidence

### `diagnose_access`

Covered by:

- `test_server_registers_expected_tools_and_resources_without_lifespan`
- `test_tools_expose_safety_annotations_and_output_schemas`
- `test_new_tools_return_stable_top_level_response_keys`
- `test_profile_health_and_prompts_are_available`
- `test_diagnose_access_reports_acl_rules_and_count_mismatch`
- `test_diagnose_access_uses_odoo19_group_ids_field`
- `test_diagnose_access_reports_missing_permission_metadata`
- `test_diagnose_access_survives_record_rule_read_failure`

What the tests prove:

- The tool is registered in the MCP surface.
- The tool is annotated read-only.
- The expected tool count is 22.
- Stable top-level response keys are present.
- Full metadata path returns ACL rows, active rules, group-bound rules, user/group
  evidence, actual count, expected count, and mismatch diagnosis.
- Metadata permission failure does not hard-fail the tool; it returns a sanitized
  partial report with `metadata_access_unavailable`.
- Record-rule read failure is isolated from the rest of the diagnosis.
- Odoo 19 `res.users` group evidence works through `group_ids` and
  `all_group_ids`, while older versions still use `groups_id`.
- Live Docker smoke proves a bespoke `ir.rule` on `res.partner` can be diagnosed
  as a record-rule filter across Odoo 16.0, 17.0, 18.0, and 19.0, including
  Odoo 19 JSON-2.

### Method Safety And Allowlist

Covered by:

- `test_diagnose_odoo_call_marks_common_side_effect_methods`
- `test_execute_method_blocks_direct_writes_and_unknown_methods`
- `test_execute_method_can_opt_into_unknown_methods`
- `test_execute_method_allows_exact_side_effect_allowlist`
- `test_profile_health_and_prompts_are_available`

What the tests prove:

- `message_post`, `action_*`, `button_*`, and send/post/validate-style methods are
  classified as side-effect methods.
- Direct destructive methods still fail closed.
- Broad `ODOO_MCP_ALLOW_UNKNOWN_METHODS=1` remains backward-compatible.
- Exact `ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS=model.method` allows only the named
  side-effect method.
- Health output includes method-safety configuration.

### Addon Source Scanner

Covered by:

- `test_server.py::test_domain_builder_and_addon_scanner`

What the test proves:

- Static scan still reports existing domain/security findings.
- New scanner findings detect:
  - computed field with unresolved compute method,
  - computed field using a compute method without `@api.depends`,
  - compute method reading fields not listed in `@api.depends`,
  - CRUD override missing `super()`,
  - CRUD override calling `super()` without clearly returning it.
- Correct `@api.depends` fixture is not flagged as missing dependency.

## Build And Package Evidence

Command:

```bash
rm -rf dist && uv run python -m build
```

Observed successful end:

```text
Successfully built odoo_mcp-0.2.0.tar.gz and odoo_mcp-0.2.0-py3-none-any.whl
```

Command:

```bash
uv run python -m twine check dist/*
```

Observed output:

```text
Checking dist/odoo_mcp-0.2.0-py3-none-any.whl: PASSED
Checking dist/odoo_mcp-0.2.0.tar.gz: PASSED
```

## Not Verified In This Pass

- No coverage percentage was generated; this repo does not currently configure
  `pytest-cov` in the documented local gates.
- No external PyPI upload or GitHub Release creation was attempted in this pass;
  this machine has no `TWINE_*`/`PYPI_API_TOKEN` environment configured and no
  `gh` executable available.
- The packaged addon fixture covers XML install/update and XML record-rule data,
  but it does not cover addon uninstall hooks, migration scripts, or custom
  Python model code.

## Remaining Risks

- Packaged addon lifecycle is tested for XML data install/update, but not addon
  uninstall, migration scripts, or Python model extensions.
- Static addon scanning is intentionally conservative and may miss dynamic Odoo
  patterns; it should not be treated as a full Odoo linter.
- Side-effect method classification is heuristic plus exact allowlist. It reduces
  accidental execution risk but is not a proof that every custom method is safe.

## Anti-Fabrication Notes

- Every command listed under "Gate Results" was executed in this workspace during
  this report pass.
- Failed/untested areas are listed explicitly under "Not Verified In This Pass".
- This report now includes live Docker Compose Odoo smoke evidence for versions
  16.0, 17.0, 18.0, and 19.0.
- The bespoke record-rule proof was executed against those live Docker Odoo
  databases, including Odoo 19 JSON-2, and the report records the seeded rule
  name, count mismatch, and diagnosis codes.
- The packaged addon proof was executed against those same live Docker Odoo
  versions, including real module install/update commands and Odoo 19 JSON-2.
- The report includes the real Odoo 16.0 packaged-rule failure found during
  implementation and the fix that made the focused Odoo 16.0 rerun pass before
  the final full matrix.
- Release prep rebuilt package artifacts as `0.2.0` and verified that the source
  distribution includes `scripts/odoo_compose_smoke.py`,
  `docker-compose.integration.yml`, and the packaged addon fixture.
- The earlier Odoo 19 `groups_id` mismatch and missing restricted-user live smoke
  gap are documented above with the implemented fixes and final passing evidence.
- Earlier during implementation, mypy caught one return-type issue in
  `agent_tools.py`; it was fixed before the final gates above were rerun.
