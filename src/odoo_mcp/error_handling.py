"""Centralised error handling helpers for MCP server_core.py.

Kept deliberately small: this module hosts ``_format_validation_error``,
the one pure helper that turns a ``pydantic.ValidationError`` into a
single-line, bounded, agent-readable summary. The rest of the friendly-envelope
plumbing lives in ``server_core.py`` (see ``_TranslationAwareFastMCP``).

Why not a ``@safe_tool_call`` decorator anymore? FastMCP validates annotated
tool arguments via Pydantic **before** the tool body runs (see
``mcp.server.fastmcp.utilities.func_metadata.call_fn_with_arg_validation``)
and converts any ``ValidationError`` to a ``ToolError`` whose ``__cause__`` is
the original error. A decorator on the function therefore never sees the
``ValidationError`` — the error is reformatted into a stack-trace-like string
before reaching the function. The actual hook must sit one layer up, at the
JSON-RPC dispatch boundary; that's what ``_TranslationAwareFastMCP`` does.
"""

from __future__ import annotations

from pydantic import ValidationError


def _format_validation_error(exc: ValidationError) -> str:
    """Render a ``ValidationError`` as one human-readable line per problem.

    Capped at three entries; a trailing ``(and more)`` flag is added when
    additional errors were truncated. Agent callers benefit from the
    one-line format because they read it as a single diagnostic sentence
    instead of a wall of Pydantic multi-line output that wraps at
    unpredictable column widths.
    """
    parts: list[str] = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    suffix = " (and more)" if len(exc.errors()) > 3 else ""
    return "; ".join(parts) + suffix


__all__: list[str] = ["_format_validation_error"]
