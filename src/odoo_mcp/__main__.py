"""
Command line entry point for the Odoo MCP Server
"""

import argparse
import os
import sys
import traceback

from .server import mcp

SUPPORTED_MCP_TRANSPORTS = {"stdio", "streamable-http", "sse"}
SECRET_ENV_KEYS = {"ODOO_PASSWORD", "ODOO_API_KEY"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments and environment defaults."""
    parser = argparse.ArgumentParser(description="Run the Odoo MCP server.")
    parser.add_argument(
        "--transport",
        choices=sorted(SUPPORTED_MCP_TRANSPORTS),
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="MCP transport to serve. Defaults to MCP_TRANSPORT or stdio.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HTTP_HOST", "127.0.0.1"),
        help="HTTP bind host for streamable-http or sse transports.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_HTTP_PORT", "8000")),
        help="HTTP bind port for streamable-http or sse transports.",
    )
    parser.add_argument(
        "--path",
        default=os.environ.get("MCP_HTTP_PATH", "/mcp"),
        help="Streamable HTTP path. Defaults to MCP_HTTP_PATH or /mcp.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=os.environ.get("MCP_LOG_LEVEL", "INFO").upper(),
        help="MCP HTTP server log level.",
    )
    return parser.parse_args(argv)


def configure_mcp_runtime(args: argparse.Namespace) -> None:
    """Apply CLI/env runtime settings to the FastMCP instance."""
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.log_level = args.log_level
    mcp.settings.streamable_http_path = args.path


def main() -> int:
    """
    Run the MCP server
    """
    try:
        args = parse_args()
        configure_mcp_runtime(args)

        print("=== ODOO MCP SERVER STARTING ===", file=sys.stderr)
        print(f"Python version: {sys.version}", file=sys.stderr)
        print("Environment variables:", file=sys.stderr)
        for key, value in os.environ.items():
            if key.startswith(("ODOO_", "MCP_")):
                if key in SECRET_ENV_KEYS:
                    print(f"  {key}: ***hidden***", file=sys.stderr)
                else:
                    print(f"  {key}: {value}", file=sys.stderr)

        print(f"Starting MCP server over {args.transport}...", file=sys.stderr)
        if args.transport in {"streamable-http", "sse"}:
            print(f"  Bind: {args.host}:{args.port}", file=sys.stderr)
            if args.transport == "streamable-http":
                print(f"  Path: {args.path}", file=sys.stderr)
        sys.stderr.flush()
        mcp.run(transport=args.transport)

        print("MCP server stopped normally", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("MCP server stopped by user", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        print("Exception details:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
