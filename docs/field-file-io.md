# Field file I/O (`read_field_to_file`, `write_field_from_file`)

Two new MCP tools route single-field payloads between Odoo and the local
filesystem instead of forcing them through the JSON-RPC envelope. The
response carries only metadata (`path`, `sha256`, byte count, encoding) —
the field content stays out of the agent's context window. The agent then
reads or edits the file with its ordinary file tools.

This solves two recurring pain points:

| Pain | Symptom | Fix |
| --- | --- | --- |
| JSON-escape hell | Long HTML / source code / rich text round-trips through the agent context with `\\n`, double-escaped quotes, and embedded Unicode that LLMs routinely mistransform. | Field value is read/written verbatim from disk; only the SHA-256 fingerprint crosses the wire. |
| Context-window blow-up | A 50 KB product description or 30 KB e-mail body lands in every subsequent tool call's history. | The agent pulls the value once, edits it locally, and writes it back without the file content re-entering the LLM context. |

The tools also give a write-side preview/execute flow modeled on
`chatter_post` and `execute_approved_write`, so the destructive write still
goes through an approval gate rather than executing on the first call.

## The two tools

### `read_field_to_file` (read domain)

```jsonc
{
  "model": "res.partner",
  "record_id": 42,
  "field": "comment",
  "output_path": "/srv/agent-scratch/comment-42.html",
  "file_root": null,         // optional per-call override (defaults to first ODOO_MCP_FIELD_FILE_ROOTS entry)
  "encoding": null,         // optional: "utf-8" (default for text) or "base64" (default for binary fields)
  "instance": null          // optional, multi-instance routing
}
```

The server resolves `output_path` against `file_root`, refuses to overwrite
an existing file, fetches the field via Odoo's `read`, applies the field
ACL (redacted values become a `[REDACTED by field ACL]` placeholder), and
streams the bytes to disk. The response is:

```jsonc
{
  "success": true,
  "tool": "read_field_to_file",
  "model": "res.partner",
  "record_id": 42,
  "field": "comment",
  "output_path": "/srv/agent-scratch/comment-42.html",
  "file_root": "/srv/agent-scratch",
  "encoding": "utf-8",
  "bytes_written": 14823,
  "content_sha256": "sha256:5e1c…:14823",
  "field_was_redacted": false,
  "redacted_fields": [],
  "metadata_used": { "instance": "default", "file_root": "…", "encoding": "utf-8", "max_bytes": 10485760, "field_type": "html" }
}
```

The agent verifies the on-disk file with the SHA-256 fingerprint before
acting on it.

### `write_field_from_file` (write domain)

Two-phase preview/execute flow:

```jsonc
// Phase 1 — preview
{
  "model": "res.partner",
  "record_id": 42,
  "field": "comment",
  "input_path": "/srv/agent-scratch/comment-42.html"
}
// → { "mode": "preview", "approval": { …, "content_sha256": "sha256:…", "token": "…" }, "warnings": [...] }
```

```jsonc
// Phase 2 — execute
{
  "model": "res.partner",
  "record_id": 42,
  "field": "comment",
  "input_path": "/srv/agent-scratch/comment-42.html",
  "approval": <the approval dict from preview>,
  "confirm": true
}
// → { "mode": "execute", "result": true, "bytes_written": 14823, "content_sha256": "…", "metadata_used": { … "field_type": "html" } }
```

The preview token never carries the file content — only its SHA-256 + size
fingerprint. The execute call re-reads the file, re-checks the hash
(so a tamper or benign race between the two calls is rejected), fetches
live `fields_get` metadata, refuses `readonly` fields, and routes the
write through the same gates as every other write: `confirm=true`,
`ODOO_MCP_ENABLE_WRITES=1`.

## Configuration: file roots

Both tools require an allow-listed root directory. The path you pass must be
**absolute** and must sit **inside** one of the configured roots.

```bash
# Colon-separated absolute directories. Linux/macOS:
export ODOO_MCP_FIELD_FILE_ROOTS="/srv/agent-scratch:/srv/team-scratch"

# Windows (semicolon):
export ODOO_MCP_FIELD_FILE_ROOTS="C:/agent-scratch;C:/team-scratch"
```

> **No default root, by design.** If neither `ODOO_MCP_FIELD_FILE_ROOTS`
> nor a per-call `file_root` override is configured, the tools refuse
> every call. The error message names the env var, lists platform-specific
> safe defaults (below), and **explicitly warns against `/tmp`** — see
> [Enabling the tools](#enabling-the-tools) below.

- **Absolute paths only.** Relative paths are rejected with a clear error
  before any file system call.
- **Containment, not chroot.** The agent can write anywhere inside a
  configured root (and into sub-directories), but cannot escape it via
  `..` traversal or symlinks — `Path.resolve(strict=False)` collapses
  both before the containment check.
- **Fail closed.** Without `ODOO_MCP_FIELD_FILE_ROOTS` and without a
  per-call `file_root` override, both tools reject every call.
- **No overwrite on read.** `read_field_to_file` refuses if the
  destination path already exists; both the pre-check and `O_CREAT | O_EXCL`
  on open cover a TOCTOU race with a symlink swap.
- **Symlink defense on read.** The create fd uses `O_NOFOLLOW`; a
  symlink whose target sits outside the root would also be caught by the
  containment check (since `Path.resolve` follows it first).
- **Symlink defense on write.** Reads use `O_NOFOLLOW`; a TOCTOU swap
  between the root check and the read is rejected because the size cap
  and SHA-256 are derived from the bytes read through that same fd.

### Enabling the tools

There is **no implicit default**. To make `read_field_to_file` /
`write_field_from_file` work, set `ODOO_MCP_FIELD_FILE_ROOTS` to one or
more absolute directories (OS-PATHSEP separated), or pass `file_root=`
on each call. When the tools reject a call because no root is
configured, the error includes platform-specific safe defaults:

```text
ODOO_MCP_FIELD_FILE_ROOTS is not configured and no file_root override
was supplied — refusing all field file I/O. To enable
read_field_to_file / write_field_from_file, do ONE of:

  1. Set ODOO_MCP_FIELD_FILE_ROOTS to one or more absolute directories
     (OS-PATHSEP separated):
    - Linux:    /home/<user>/.cache/odoo-mcp/field-files
    - macOS:    /home/<user>/.cache/odoo-mcp/field-files
               (or any user-owned absolute path with mode 0700)
  2. Pass file_root="/abs/path" on each tool call (operator-only).

Security: do NOT point these roots at /tmp — /tmp is typically
world-readable on Linux/macOS, which would expose long field payloads
(HTML comments, source code, internal notes) to other local users and
processes. Use a per-user directory with mode 0700 instead.
```

The path suggestions honour `$XDG_CACHE_HOME` on POSIX and
`%LOCALAPPDATA%` on Windows; otherwise they default to
`~/.cache/odoo-mcp/field-files` (POSIX) or
`%LOCALAPPDATA%\odoo-mcp\Cache\field-files` (Windows).

> **Why not `/tmp`?** It is `1777` (world-writable + sticky) on most
> Linux distributions and world-readable on macOS. A 50 KB product
> description or 80 KB internal note written there would be visible to
> every other process and user on the box. The tools refuse this category
> of mistake in the error message rather than silently honouring it.

### Per-call `file_root` override

```jsonc
{
  "file_root": "/srv/another-root"
}
```

Useful when an agent is invoked against a temporary working area not in
the default root set. The override still has to be absolute and is
resolved at call time, so it can never widen the operator's allow-list.

## Environment variables

| Variable | Default | Effect |
| --- | --- | --- |
| `ODOO_MCP_FIELD_FILE_ROOTS` | unset | Colon-separated (or `os.pathsep`-separated) absolute directories the tools may read from / write into. **Required** — fails closed without it. |
| `ODOO_MCP_MAX_FIELD_FILE_BYTES` | `10485760` (10 MiB) | Hard cap per payload (read or write). Shared 16 MiB ceiling with attachment uploads (`ATTACHMENT_BYTES_HARD_CAP`). |

Both knobs appear in `health_check.runtime.field_file_roots`:

```jsonc
{
  "field_file_roots": {
    "env": "ODOO_MCP_FIELD_FILE_ROOTS",
    "count": 1,
    "roots": ["/srv/agent-scratch"]
  }
}
```

## Encoding

| Field type (`fields_get.type`) | Default encoding | What the file holds |
| --- | --- | --- |
| `binary` (or caller passes `encoding="base64"`) | `base64` | The raw decoded bytes. |
| anything else (or caller passes `encoding="utf-8"`) | `utf-8` | `str(value).encode("utf-8")`. |

A caller-supplied `encoding` argument overrides the type-based default.
Passing `encoding="base64"` on a non-binary field triggers a base64
decode of the Odoo-supplied base64 string, which only succeeds when
`fields_get.type == "binary"` — otherwise the caller gets a clear
validation error rather than silently truncated bytes.

## Field ACL interaction

`read_field_to_file` runs through `get_field_policy()`:

- Fields named in `field_acl[*][model].deny` are not exposed. The tool
  writes a `[REDACTED by field ACL]` placeholder to the file and returns
  `field_was_redacted: true` plus `redacted_fields: [...]` so the agent
  cannot hallucinate the value.
- A redacted value never appears in the tool response payload, audit log
  entry, or anywhere else.

`write_field_from_file` does not consult the field ACL — the path is
gated by the write gate (`ODOO_MCP_ENABLE_WRITES=1` + `confirm=true`)
and by the live `fields_get` metadata check (refuses `readonly=True`).

## Examples

### Read a partner's HTML comment, edit locally, write it back

```text
1. read_field_to_file(model="res.partner", record_id=42, field="comment",
                      output_path="/srv/agent-scratch/comment-42.html")
   → file is on disk, response carries the SHA-256 fingerprint.

2. Agent uses its ordinary file tools to read the file, edit, and save it.

3. write_field_from_file(model="res.partner", record_id=42, field="comment",
                         input_path="/srv/agent-scratch/comment-42.html")
   → mode="preview", returns an approval token with the new SHA-256.

4. write_field_from_file(..., approval=<token>, confirm=true)
   → mode="execute", file is re-read, hash re-checked, write performed.
```

### Read a binary PDF attached to a marketing brochure

```jsonc
read_field_to_file(
  model="ir.attachment",
  record_id=1985,
  field="datas",                      // type=binary in fields_get
  output_path="/srv/agent-scratch/brochure.pdf",
  // encoding omitted — defaults to base64 for binary fields
)
```

The file on disk is a raw PDF; the agent's PDF library can read it
without first base64-decoding it from a JSON envelope.

### Append to a long internal note without rewriting the whole field

The agent can read the field to disk, append a paragraph with its file
tools, and write the file back — all without the 80 KB note entering the
agent's context twice.

## Security model (summary)

| Threat | Mitigation |
| --- | --- |
| Prompt-injected agent reads SSH keys or other secrets from `/etc` | Path is absolute + must sit inside `ODOO_MCP_FIELD_FILE_ROOTS`. No root configured → every call rejected. |
| Agent overwrites an unrelated file | `read_field_to_file` fails if destination exists (`O_CREAT|O_EXCL`). `write_field_from_file` only writes to Odoo, not the filesystem. |
| Symlink swap (TOCTOU) on read | `O_NOFOLLOW` on create fd; `Path.resolve` collapses symlinks before the containment check. |
| Symlink swap (TOCTOU) on write | File is opened with `O_RDONLY|O_NOFOLLOW`; size cap and SHA-256 derived from bytes read through that same fd. |
| File tamper between preview and execute | Execute re-reads the file and re-checks the SHA-256; mismatch → reject, no Odoo call. |
| Sensitive field leaking via filesystem | Field ACL redaction applies to `read_field_to_file`; redacted fields write a placeholder. |
| Token replay / payload swap | Execute verifies the approval token matches the current `content_sha256` of the current file content before doing anything. |

## Auditing

Both tools emit one audit-log entry per call (when `ODOO_MCP_AUDIT_LOG` is
set), with:

- `tool`, `outcome` (`preview` / `success` / `denied`)
- `model`, `operation`, `record_ids`
- `instance`
- `token` (digest only — the raw token never lands on disk)
- `detail`: `field=<name> bytes=<size>`

A successful `write_field_from_file` execute shows up as
`operation="write"` with `bytes=<size>` and the resolved `instance`.

## Compatibility

| Odoo version | Behaviour |
| --- | --- |
| 16.0 / 17.0 / 18.0 (XML-RPC default) | Fully supported. Binary fields round-trip via base64 encoding. |
| 19.0 (XML-RPC or JSON-2) | Fully supported. Field-ACL integration unchanged. |

The tools share no code with Odoo transports — they sit on top of the
same `OdooClient.read_records` and `OdooClient.execute_method("…", "write", …)`
calls the rest of the surface uses.

## See also

- [docs/field-acl.md](field-acl.md) — field-level ACL semantics and `read_field_to_file` redaction behavior
- [docs/adding-a-tool.md](adding-a-tool.md) — how a new MCP tool is registered (relevant when extending the file-root model)
- [docs/architecture.md](architecture.md) — surface vs. core module split (the file-root helper lives in `server_core.py`; both tools are thin `@mcp.tool` wrappers)
