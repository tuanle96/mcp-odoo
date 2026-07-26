"""Tests for read_field_to_file and write_field_from_file.

Covers the file I/O contract:
- happy path (text + binary + base64 encoding)
- Field ACL redaction on read
- Path hardening: absolute-only, root containment, no overwrite,
  TOCTOU via symlink swap
- Preview/execute two-phase flow with hash re-check
- Hard gates: writes-enabled + confirm=true
- Readonly / unknown field rejection
"""

from __future__ import annotations

import base64
import hashlib
import importlib
from pathlib import Path
from typing import Any


from tests.test_batch_write import FakeCtx


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _FieldIOClient:
    """Minimal stand-in for OdooClient — text fields + a binary base64 stub.

    The ``execute_method`` signature matches OdooClient exactly:
    ``(self, model, method, *args, **kwargs)`` — so the test sees the same
    unpacked form OdooClient forwards to Odoo via XML-RPC execute_kw. This
    is what caught the original write_field_from_file bug (which packed
    ``[ids, vals]`` into a single positional arg, making Odoo see
    ``ProjectTask.write(*[[ids], {vals}])`` and fault with
    ``missing 1 required positional argument: 'vals'``).
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.field_value: Any = "<p>hello & \"world\"\nline 2</p>"
        self.field_meta: dict[str, dict[str, Any]] = {
            "comment": {"type": "html", "readonly": False},
            "description": {"type": "text", "readonly": False},
            "datas": {"type": "binary", "readonly": False},
            "create_uid": {"type": "many2one", "readonly": True},
        }

    def get_model_fields(self, model: str) -> dict[str, Any]:
        return dict(self.field_meta)

    def read_records(self, model: str, ids: list[int], fields=None):
        self.calls.append(("read_records", model, list(ids), list(fields or [])))
        record = {"id": ids[0]}
        if fields:
            for f in fields:
                if f in self.field_meta:
                    record[f] = self.field_value
        return [record]

    def execute_method(self, model: str, method: str, *args: Any, **kwargs: Any):
        # Record the *unpacked* form — same shape OdooClient forwards via
        # XML-RPC execute_kw. Tests assert on this shape directly so a
        # future regression that re-packs args is caught immediately.
        self.calls.append(
            ("execute_method", model, method, list(args), dict(kwargs))
        )
        return True


def _install_root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ODOO_MCP_FIELD_FILE_ROOTS", str(tmp_path))
    return tmp_path


def _import_server():
    return importlib.import_module("odoo_mcp.server")


# ---------------------------------------------------------------------------
# read_field_to_file
# ---------------------------------------------------------------------------


def test_read_field_to_file_writes_text_field_to_new_file(tmp_path, monkeypatch):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    output_path = tmp_path / "out.html"

    client = _FieldIOClient()
    client.field_value = "<p>hello & \"world\"\nline 2</p>"
    ctx = FakeCtx(client)
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "comment", str(output_path)
    )

    assert result["success"] is True, result
    assert result["output_path"] == str(output_path)
    assert result["bytes_written"] == len(client.field_value.encode("utf-8"))
    assert result["encoding"] == "utf-8"
    assert result["field_was_redacted"] is False
    assert output_path.read_bytes() == client.field_value.encode("utf-8")
    # sha256 fingerprint is in the response, not the file content.
    assert result["content_sha256"].startswith("sha256:")
    digest = result["content_sha256"].split(":")[1]
    assert digest == hashlib.sha256(client.field_value.encode("utf-8")).hexdigest()


def test_read_field_to_file_refuses_overwrite_of_existing_file(tmp_path, monkeypatch):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    output_path = tmp_path / "out.html"
    output_path.write_text("existing content")

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "comment", str(output_path)
    )

    assert result["success"] is False
    assert "already exists" in result["error"]
    # Existing file must not be modified.
    assert output_path.read_text() == "existing content"


def test_read_field_to_file_rejects_relative_path(tmp_path, monkeypatch):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "comment", "out.html"  # relative path
    )

    assert result["success"] is False
    assert "absolute" in result["error"].lower()


def test_read_field_to_file_rejects_path_outside_configured_root(
    tmp_path, monkeypatch
):
    server = _import_server()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("ODOO_MCP_FIELD_FILE_ROOTS", str(allowed))

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "comment", str(outside / "out.html")
    )

    assert result["success"] is False
    assert "outside" in result["error"].lower()


def test_read_field_to_file_requires_configured_root_when_no_override(
    tmp_path, monkeypatch
):
    server = _import_server()
    monkeypatch.delenv("ODOO_MCP_FIELD_FILE_ROOTS", raising=False)

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "comment", str(tmp_path / "out.html")
    )

    assert result["success"] is False
    error = result["error"]
    # The error must name the env var and explicitly warn against /tmp
    # (world-readable on Linux/macOS). The remediation is now ONLY to
    # set ODOO_MCP_FIELD_FILE_ROOTS — file_root cannot widen the
    # allow-list (see test_file_root_cannot_widen_allow_list below).
    assert "ODOO_MCP_FIELD_FILE_ROOTS" in error
    assert "/tmp" in error
    assert "world-readable" in error
    # The error should also explain why: refuses all field file I/O.
    assert "refusing" in error.lower() or "fail" in error.lower()
    # file_root is no longer advertised as a remediation.
    assert "Pass file_root=" not in error


def test_missing_root_error_lists_platform_specific_suggestions(
    tmp_path, monkeypatch
):
    """The remediation message should suggest a safe per-platform default
    directory, not just throw a generic error."""
    import os

    server = _import_server()
    monkeypatch.delenv("ODOO_MCP_FIELD_FILE_ROOTS", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "comment", str(tmp_path / "out.html")
    )

    assert result["success"] is False
    if os.name == "nt":
        # Windows suggestion should mention AppData/Local.
        assert "AppData" in result["error"]
    else:
        # POSIX suggestion should use ~/.cache (XDG) and name Linux/macOS.
        assert ".cache" in result["error"]
        assert "Linux" in result["error"]
        assert "macOS" in result["error"]


def test_read_field_to_file_uses_explicit_file_root_override_as_selector(
    tmp_path, monkeypatch
):
    """``file_root`` is a SELECTOR among configured roots (it must equal
    one of them after resolve) — it cannot invent a new root. The
    configured root must be set even when ``file_root`` is supplied."""
    server = _import_server()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ODOO_MCP_FIELD_FILE_ROOTS", str(allowed))

    output_path = allowed / "explicit_root.html"
    client = _FieldIOClient()
    ctx = FakeCtx(client)
    result = server.read_field_to_file(
        ctx,
        "res.partner",
        7,
        "comment",
        str(output_path),
        file_root=str(allowed),
    )

    assert result["success"] is True, result
    assert result["file_root"] == str(allowed.resolve())
    assert output_path.exists()


def test_file_root_cannot_widen_allow_list(tmp_path, monkeypatch):
    """Regression test for the prompt-injection bypass: ``file_root``
    used to accept any absolute path even when it was outside the
    configured ``ODOO_MCP_FIELD_FILE_ROOTS`` list — letting a
    prompt-injected agent pass ``file_root="/"`` and then read / write
    through any path under it. The selector semantics now reject this
    outright."""
    server = _import_server()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ODOO_MCP_FIELD_FILE_ROOTS", str(allowed))

    ctx = FakeCtx(_FieldIOClient())
    # ``/`` is not in the configured roots list.
    result = server.read_field_to_file(
        ctx,
        "res.partner",
        7,
        "comment",
        str(allowed / "out.html"),
        file_root="/",
    )

    assert result["success"] is False
    error_text = result["error"].lower()
    assert (
        "not one of the configured" in error_text
        or "cannot widen" in error_text
    )

    # Same bypass attempt against the write tool.
    input_path = allowed / "snippet.html"
    input_path.write_text("payload", encoding="utf-8")
    write_result = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "comment",
        str(input_path),
        file_root="/home/user",
    )
    assert write_result["success"] is False
    assert (
        "not one of the configured" in write_result["error"].lower()
        or "cannot widen" in write_result["error"].lower()
    )


def test_file_root_requires_env_var_even_when_supplied(tmp_path, monkeypatch):
    """Setting ``file_root`` no longer lets you skip
    ``ODOO_MCP_FIELD_FILE_ROOTS`` — the env var is always required."""
    server = _import_server()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.delenv("ODOO_MCP_FIELD_FILE_ROOTS", raising=False)

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx,
        "res.partner",
        7,
        "comment",
        str(allowed / "out.html"),
        file_root=str(allowed),
    )

    assert result["success"] is False
    assert "ODOO_MCP_FIELD_FILE_ROOTS" in result["error"]


def test_relative_file_root_rejected_before_resolve(tmp_path, monkeypatch):
    """Relative ``file_root`` values such as ``\"scratch\"`` must be
    rejected before ``Path.resolve()`` silently joins them to ``$CWD``."""
    server = _import_server()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ODOO_MCP_FIELD_FILE_ROOTS", str(allowed))

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx,
        "res.partner",
        7,
        "comment",
        str(allowed / "out.html"),
        file_root="scratch",
    )

    assert result["success"] is False
    assert "absolute" in result["error"].lower()


def test_multi_root_accepts_path_under_second_root(tmp_path, monkeypatch):
    """When no ``file_root`` is supplied, the candidate must be validated
    against ALL configured roots — not just the first one. Configs like
    ``ODOO_MCP_FIELD_FILE_ROOTS=/srv/a:/srv/b`` must accept paths under
    either root."""
    server = _import_server()
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setenv(
        "ODOO_MCP_FIELD_FILE_ROOTS", f"{root_a}{__import__('os').pathsep}{root_b}"
    )

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx,
        "res.partner",
        7,
        "comment",
        str(root_b / "out.html"),
    )

    assert result["success"] is True, result
    assert result["file_root"] == str(root_b.resolve())


def test_read_field_to_file_handles_binary_field_with_base64(tmp_path, monkeypatch):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    output_path = tmp_path / "blob.bin"

    raw_bytes = b"\x89PNG\r\n\x1a\n -- binary content --"
    client = _FieldIOClient()
    client.field_value = base64.b64encode(raw_bytes).decode("ascii")
    ctx = FakeCtx(client)
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "datas", str(output_path)
    )

    assert result["success"] is True, result
    assert result["encoding"] == "base64"
    assert output_path.read_bytes() == raw_bytes


def test_read_field_to_file_rejects_base64_on_non_binary_field(
    tmp_path, monkeypatch
):
    """``encoding=\"base64\"`` is only valid on fields whose
    ``fields_get.type`` is ``binary``. Without this guard, an
    arbitrary text field would be silently base64-decoded into garbage
    bytes (with ``validate=False`` making it best-effort). Reject up
    front with a clear error naming the field and the metadata type."""
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    output_path = tmp_path / "out.bin"

    ctx = FakeCtx(_FieldIOClient())
    # ``comment`` is type=\"html\" in the fake field meta, not binary.
    result = server.read_field_to_file(
        ctx,
        "res.partner",
        7,
        "comment",
        str(output_path),
        encoding="base64",
    )

    assert result["success"] is False
    error_text = result["error"].lower()
    assert "encoding" in error_text and "base64" in error_text
    assert "binary" in error_text
    # The error must name the field and the metadata-reported type so
    # the agent knows what to fix.
    assert "comment" in result["error"]
    assert "html" in result["error"]
    # The file must NOT have been written.
    assert not output_path.exists()


def test_read_field_to_file_blocks_symlink_escape_within_root(
    tmp_path, monkeypatch
):
    """A symlink whose resolved target sits outside the configured root
    must be rejected by the containment check (Path.resolve collapses it
    before the relative_to check runs)."""
    server = _import_server()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("ODOO_MCP_FIELD_FILE_ROOTS", str(allowed))

    # File outside the root that contains the secret bytes.
    secret_file = outside / "secret.html"
    secret_file.write_bytes(b"TOP-SECRET-VALUE")
    # A symlink inside the allowed root that points at the secret.
    escape_link = allowed / "out.html"
    escape_link.symlink_to(secret_file)

    ctx = FakeCtx(_FieldIOClient())
    result = server.read_field_to_file(
        ctx, "res.partner", 7, "comment", str(escape_link)
    )

    assert result["success"] is False
    assert "outside" in result["error"].lower()
    # The secret must not be exposed anywhere in the response.
    serialized = str(result)
    assert "TOP-SECRET-VALUE" not in serialized


# ---------------------------------------------------------------------------
# write_field_from_file
# ---------------------------------------------------------------------------


def test_write_field_from_file_preview_returns_token_without_content(
    tmp_path, monkeypatch
):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    input_path = tmp_path / "snippet.html"
    content = "<h1>Preview me</h1>\n<p>some <b>html</b></p>"
    input_path.write_text(content, encoding="utf-8")

    client = _FieldIOClient()
    ctx = FakeCtx(client)
    result = server.write_field_from_file(
        ctx, "res.partner", 9, "comment", str(input_path)
    )

    assert result["success"] is True, result
    assert result["mode"] == "preview"
    assert result["bytes_written"] == len(content.encode("utf-8"))
    approval = result["approval"]
    assert approval["token"]
    assert approval["content_sha256"].startswith("sha256:")
    # The real content must never appear in the approval payload.
    assert content not in str(approval)
    assert content.encode("utf-8") not in str(approval).encode("utf-8")
    # And nothing in the tool response either.
    assert content not in str(result)


def test_write_field_from_file_execute_round_trip(tmp_path, monkeypatch):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")

    input_path = tmp_path / "snippet.html"
    content = "<h1>Round trip</h1>"
    input_path.write_text(content, encoding="utf-8")

    client = _FieldIOClient()
    ctx = FakeCtx(client)
    preview = server.write_field_from_file(
        ctx, "res.partner", 9, "comment", str(input_path)
    )
    assert preview["success"] is True

    result = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "comment",
        str(input_path),
        approval=preview["approval"],
        confirm=True,
    )

    assert result["success"] is True, result
    assert result["mode"] == "execute"
    assert result["result"] is True
    # The write must reach Odoo as TWO positional arguments (ids, vals),
    # not one — see the regression test below for the failing form.
    # write_call == ("execute_method", model, method, args_list, kwargs_dict)
    write_call = next(c for c in client.calls if c[0] == "execute_method")
    _, model, method, args, kwargs = write_call
    assert (model, method) == ("res.partner", "write")
    # ids and vals must be TWO *separate* positional args for Odoo
    # XML-RPC execute_kw (not one nested list — see regression test).
    assert len(args) == 2, f"expected 2 positional args, got {args!r}"
    assert args == [[9], {"comment": content}]
    assert kwargs == {}


def test_write_field_from_file_rejects_tampered_file_after_preview(
    tmp_path, monkeypatch
):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")

    input_path = tmp_path / "snippet.html"
    input_path.write_text("original", encoding="utf-8")

    client = _FieldIOClient()
    ctx = FakeCtx(client)
    preview = server.write_field_from_file(
        ctx, "res.partner", 9, "comment", str(input_path)
    )
    assert preview["success"] is True

    # Swap the file contents between preview and execute.
    input_path.write_text("TAMPERED content", encoding="utf-8")

    result = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "comment",
        str(input_path),
        approval=preview["approval"],
        confirm=True,
    )

    assert result["success"] is False
    # Either defense may fire first: the token check (the new file hash
    # yields a new approval token that doesn't match the preview's) or
    # the explicit file-hash re-check (file contents changed). Both are
    # equally correct rejections.
    error_text = result["error"].lower()
    assert (
        "changed between preview and execute" in error_text
        or "token does not match" in error_text
    )
    # No write call must have reached Odoo.
    assert all(c[0] != "execute_method" for c in client.calls)


def test_write_field_from_file_requires_confirm_and_writes_enabled(
    tmp_path, monkeypatch
):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    # Writes disabled by default.
    input_path = tmp_path / "snippet.html"
    input_path.write_text("hi", encoding="utf-8")

    client = _FieldIOClient()
    ctx = FakeCtx(client)
    preview = server.write_field_from_file(
        ctx, "res.partner", 9, "comment", str(input_path)
    )
    assert preview["success"] is True

    # confirm=False -> rejected.
    denied = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "comment",
        str(input_path),
        approval=preview["approval"],
        confirm=False,
    )
    assert denied["success"] is False
    assert "confirm=true" in denied["error"]

    # Now enable writes but skip confirm -> still rejected (same gate).
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    denied2 = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "comment",
        str(input_path),
        approval=preview["approval"],
        confirm=False,
    )
    assert denied2["success"] is False
    assert "confirm=true" in denied2["error"]


def test_write_field_from_file_rejects_readonly_field(tmp_path, monkeypatch):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")

    input_path = tmp_path / "snippet.html"
    input_path.write_text("hi", encoding="utf-8")

    client = _FieldIOClient()
    ctx = FakeCtx(client)
    preview = server.write_field_from_file(
        ctx, "res.partner", 9, "create_uid", str(input_path)
    )
    assert preview["success"] is True

    result = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "create_uid",
        str(input_path),
        approval=preview["approval"],
        confirm=True,
    )

    assert result["success"] is False
    assert "readonly" in result["error"]


def test_write_field_from_file_rejects_oversized_input(tmp_path, monkeypatch):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_MAX_FIELD_FILE_BYTES", "10")

    input_path = tmp_path / "big.html"
    input_path.write_bytes(b"x" * 100)

    ctx = FakeCtx(_FieldIOClient())
    result = server.write_field_from_file(
        ctx, "res.partner", 9, "comment", str(input_path)
    )

    assert result["success"] is False
    assert "cap is 10" in result["error"]


def test_write_field_from_file_rejects_path_outside_root(tmp_path, monkeypatch):
    server = _import_server()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("ODOO_MCP_FIELD_FILE_ROOTS", str(allowed))
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")

    input_path = outside / "snippet.html"
    input_path.write_text("hi", encoding="utf-8")

    ctx = FakeCtx(_FieldIOClient())
    result = server.write_field_from_file(
        ctx, "res.partner", 9, "comment", str(input_path)
    )

    assert result["success"] is False
    assert "outside" in result["error"].lower()


def test_write_field_from_file_base64_encoding_round_trip(tmp_path, monkeypatch):
    """Binary file on disk + base64 encoding → real base64 value pushed to Odoo."""
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")

    raw_bytes = b"\x00\x01\x02binary blob"
    input_path = tmp_path / "blob.bin"
    input_path.write_bytes(raw_bytes)

    client = _FieldIOClient()
    ctx = FakeCtx(client)
    preview = server.write_field_from_file(
        ctx, "res.partner", 9, "datas", str(input_path)
    )
    assert preview["success"] is True

    result = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "datas",
        str(input_path),
        approval=preview["approval"],
        confirm=True,
    )

    assert result["success"] is True, result
    write_call = next(c for c in client.calls if c[0] == "execute_method")
    # write_call == ("execute_method", model, method, args_list, kwargs_dict)
    _, model, method, args, kwargs = write_call
    assert (model, method) == ("res.partner", "write")
    assert len(args) == 2, (
        "ids + vals must arrive as two *separate* positional args for "
        "Odoo XML-RPC execute_kw (not one nested list — see the bug "
        "test below)."
    )
    pushed_ids, pushed_vals = args
    assert pushed_ids == [9]
    pushed_value = pushed_vals["datas"]
    assert base64.b64decode(pushed_value) == raw_bytes
    assert kwargs == {}


def test_write_field_from_file_does_not_pack_args_into_single_list(
    tmp_path, monkeypatch
):
    """Regression test for the ProjectTask.write() bug.

    The original implementation called:
        odoo.execute_method(model, "write", [[ids], {vals}])
    OdooClient.execute_method(self, model, method, *args, **kwargs) splits
    *args, so this arrived at Odoo as execute_kw("project.task", "write",
    [[ids], {vals}]) — a single positional arg. Odoo's XML-RPC dispatcher
    then unpacked it as ``ProjectTask.write(*[[ids], {vals}])`` which
    means ``write([ids], {vals})`` — only one positional arg where two
    were required, so Odoo faulted with
    ``missing 1 required positional argument: 'vals'``.

    Correct form (mirroring execute_approved_write):
        odoo.execute_method(model, "write", [ids], {vals})

    This test asserts the args tuple is split into exactly the two
    separate values Odoo expects. If anyone ever re-introduces the
    nested-list form, this test fires.
    """
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")

    input_path = tmp_path / "snippet.html"
    input_path.write_text("payload", encoding="utf-8")

    client = _FieldIOClient()
    ctx = FakeCtx(client)
    preview = server.write_field_from_file(
        ctx, "project.task", 134, "description", str(input_path)
    )
    assert preview["success"] is True

    result = server.write_field_from_file(
        ctx,
        "project.task",
        134,
        "description",
        str(input_path),
        approval=preview["approval"],
        confirm=True,
    )
    assert result["success"] is True, result

    write_call = next(c for c in client.calls if c[0] == "execute_method")
    _, model, method, args, kwargs = write_call
    assert (model, method) == ("project.task", "write")
    # ids and vals must arrive as two SEPARATE positional args, not one
    # nested list. The fix changes
    #     odoo.execute_method(model, "write", [[ids], {vals}])  # bug
    # to
    #     odoo.execute_method(model, "write", [ids], {vals})     # fix
    # which OdooClient then forwards to execute_kw as
    #     ('project.task', 'write', [[ids]], {vals})             # also ok
    # either way the *args tuple Odoo receives is ([ids], {vals}).
    # The structural assertion is: args is a list whose first element is
    # the ids list and second element is the vals dict — *not* a single
    # nested list whose first element is also a list. Type-check the
    # first element specifically.
    assert len(args) == 2, f"expected 2 positional args, got {args!r}"
    ids_arg, vals_arg = args
    assert ids_arg == [134]
    assert isinstance(ids_arg, list) and all(isinstance(x, int) for x in ids_arg)
    assert isinstance(vals_arg, dict)
    assert vals_arg == {"description": "payload"}
    assert kwargs == {}


def test_write_field_from_file_rejects_garbage_approval_token(
    tmp_path, monkeypatch
):
    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")

    input_path = tmp_path / "snippet.html"
    input_path.write_text("hi", encoding="utf-8")

    ctx = FakeCtx(_FieldIOClient())
    result = server.write_field_from_file(
        ctx,
        "res.partner",
        9,
        "comment",
        str(input_path),
        approval={"token": "not-the-real-token", "content_sha256": "sha256:x:1"},
        confirm=True,
    )

    assert result["success"] is False
    assert "token does not match" in result["error"]


# ---------------------------------------------------------------------------
# Field ACL integration
# ---------------------------------------------------------------------------


def test_read_field_to_file_writes_placeholder_when_field_is_redacted(
    tmp_path, monkeypatch
):
    """When the field ACL withholds the value, the file gets a placeholder
    and the response flags it via field_was_redacted so the agent cannot
    hallucinate the value."""
    import odoo_mcp.field_policy as fp

    server = _import_server()
    _install_root(monkeypatch, tmp_path)
    output_path = tmp_path / "redacted.html"

    # Install a deny policy directly into the module-level cache and
    # reset afterwards so other tests are not affected.
    deny_policy = fp.FieldPolicy(
        {
            "default": {
                "res.partner": fp.ModelFieldRule(
                    mode="deny", fields=frozenset({"comment"})
                ),
            }
        }
    )
    monkeypatch.setattr(fp, "_policy", deny_policy)
    try:
        client = _FieldIOClient()
        ctx = FakeCtx(client)
        result = server.read_field_to_file(
            ctx, "res.partner", 7, "comment", str(output_path)
        )
        assert result["success"] is True, result
        assert result["field_was_redacted"] is True
        assert result["redacted_fields"] == ["comment"]
        # Placeholder in the file, not the real value.
        body = output_path.read_text(encoding="utf-8")
        assert body == "[REDACTED by field ACL]"
    finally:
        fp.reset_field_policy()
