"""Tests for the centralised error-formatting helpers and the
FastMCP ``call_tool`` translation hook.

Lives at ``tests/test_error_handling.py`` even though only
``_format_validation_error`` is now in ``error_handling.py`` (the rest of the
plumbing lives in ``server_core._TranslationAwareFastMCP``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, Field, ValidationError

from odoo_mcp.error_handling import _format_validation_error


class _Echo(BaseModel):
    """Tiny Pydantic model used to provoke ValidationError in tests."""

    items: list[str] = Field(default_factory=list)


def _make_int_error_dict(field: str, value: Any) -> dict[str, Any]:
    """Build a Pydantic v2-style error dict for an integer-parsing failure."""
    return {
        "type": "value_error",
        "loc": (field,),
        "msg": "value is not a valid integer",
        "input": value,
        "ctx": {"error": f"invalid literal for int() with base 10: '{value}'"},
    }


# ----- _format_validation_error -------------------------------------------


def test_validation_error_renderer_is_bounded():
    """The renderer caps the number of errors it surfaces."""

    try:
        _Echo(items=42)  # type: ignore[arg-type]
    except ValidationError as exc:
        rendered = _format_validation_error(exc)
        # Single error here, so no "(and more)" suffix.
        assert "(and more)" not in rendered
        assert "items" in rendered

    # 5 errors → only the first 3 are rendered, with an "(and more)" suffix.
    big_errors = [
        _make_int_error_dict(f"field_{i}", str(i))
        for i in range(5)
    ]
    exc = ValidationError.from_exception_data("BigModel", big_errors)  # type: ignore[arg-type]
    rendered = _format_validation_error(exc)
    assert "(and more)" in rendered
    # First three field names should be present.
    for i in range(3):
        assert f"field_{i}" in rendered
    # The fourth and fifth should NOT be rendered (capped).
    assert "field_3" not in rendered
    assert "field_4" not in rendered


# ----- FastMCP call_tool translation (server_core.py hook) ----------------


def _real_tool_error(message: str, cause: BaseException | None) -> Exception:
    """Build a real ``mcp.server.fastmcp.exceptions.ToolError`` with a cause."""
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        raise ToolError(message) from cause
    except ToolError as exc:
        return exc


def test_translate_validation_tool_error_to_envelope_returns_envelope():
    """A ``ToolError`` whose ``__cause__`` is a ``ValidationError`` becomes an envelope block."""
    from odoo_mcp.server_core import _translate_validation_tool_error_to_envelope

    ve = ValidationError.from_exception_data(
        "ArgsModel",  # type: ignore[arg-type]
        [_make_int_error_dict("measures", "")],
    )
    tool_error = _real_tool_error(
        "Error executing tool aggregate_records: 1 validation error", cause=ve
    )
    block = _translate_validation_tool_error_to_envelope(
        tool_error, tool_name_hint="aggregate_records"
    )
    assert block is not None
    assert block.type == "text"
    payload = json.loads(block.text)
    assert payload["success"] is False
    assert payload["tool"] == "aggregate_records"
    assert "measures" in payload["error"]
    assert "Invalid input" in payload["error"]


def test_translate_validation_tool_error_to_envelope_passes_non_validation():
    """A real ``ToolError`` whose cause is *not* a ``ValidationError`` is left alone."""
    from odoo_mcp.server_core import _translate_validation_tool_error_to_envelope

    plain_tool_error = _real_tool_error("Error executing tool foo: boom", cause=None)
    assert _translate_validation_tool_error_to_envelope(plain_tool_error) is None


def test_translate_validation_tool_error_to_envelope_passes_non_tool_error():
    """A bare ``ValidationError`` (not wrapped in ``ToolError``) is left alone."""
    from odoo_mcp.server_core import _translate_validation_tool_error_to_envelope

    ve = ValidationError.from_exception_data(
        "ArgsModel",  # type: ignore[arg-type]
        [_make_int_error_dict("measures", "")],
    )
    # The translator must not be tempted to wrap a raw ValidationError --
    # only a ToolError wrapping one is in scope.
    assert _translate_validation_tool_error_to_envelope(ve) is None
