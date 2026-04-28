"""
Command line entry point for the Odoo MCP Server
"""

import argparse
import json
import os
import sys
import traceback

from .server import mcp

SUPPORTED_MCP_TRANSPORTS = {"stdio", "streamable-http", "sse"}
SECRET_ENV_KEYS = {"ODOO_PASSWORD", "ODOO_API_KEY", "MCP_HTTP_AUTH_TOKEN"}
LOCAL_HTTP_HOSTS = {"127.0.0.1", "localhost", "::1"}


def parse_bool(value: str | None) -> bool:
    """Parse common boolean values from environment variables."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_csv_env(value: str | None) -> list[str]:
    """Parse comma-separated env/CLI values while ignoring blanks."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def is_secret_env_key(key: str) -> bool:
    """Keep secrets out of startup logs."""
    upper_key = key.upper()
    return (
        upper_key in SECRET_ENV_KEYS
        or upper_key.endswith("_PASSWORD")
        or upper_key.endswith("_TOKEN")
        or upper_key.endswith("_API_KEY")
    )


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
    parser.add_argument(
        "--allow-remote-http",
        action="store_true",
        default=parse_bool(os.environ.get("MCP_ALLOW_REMOTE_HTTP")),
        help=(
            "Allow HTTP transports to bind non-local hosts. Use only behind your "
            "own authentication, TLS, and network policy."
        ),
    )
    parser.add_argument(
        "--allowed-hosts",
        default=os.environ.get("MCP_ALLOWED_HOSTS", ""),
        help="Comma-separated Host header allowlist for HTTP transports.",
    )
    parser.add_argument(
        "--allowed-origins",
        default=os.environ.get("MCP_ALLOWED_ORIGINS", ""),
        help="Comma-separated Origin allowlist for HTTP transports.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Print non-secret runtime health JSON and exit.",
    )
    return parser.parse_args(argv)


def configure_mcp_runtime(args: argparse.Namespace) -> None:
    """Apply CLI/env runtime settings to the FastMCP instance."""
    if (
        args.transport in {"streamable-http", "sse"}
        and args.host not in LOCAL_HTTP_HOSTS
        and not args.allow_remote_http
    ):
        raise ValueError(
            "HTTP transports bind local hosts only by default. "
            "Use --allow-remote-http or MCP_ALLOW_REMOTE_HTTP=1 only behind "
            "external authentication, TLS, and network policy."
        )
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.log_level = args.log_level
    mcp.settings.streamable_http_path = args.path
    allowed_hosts = parse_csv_env(args.allowed_hosts)
    allowed_origins = parse_csv_env(args.allowed_origins)
    security = mcp.settings.transport_security
    if allowed_hosts or allowed_origins:
        if security is None:
            raise ValueError("FastMCP transport security settings are unavailable")
        if allowed_hosts:
            security.allowed_hosts = allowed_hosts
        if allowed_origins:
            security.allowed_origins = allowed_origins


def health_payload(args: argparse.Namespace) -> dict[str, object]:
    """Build a non-secret process/runtime health payload."""
    security = mcp.settings.transport_security
    if security is None:
        transport_security = None
    else:
        transport_security = {
            "dns_rebinding_protection": security.enable_dns_rebinding_protection,
            "allowed_hosts": security.allowed_hosts,
            "allowed_origins": security.allowed_origins,
        }
    return {
        "success": True,
        "transport": args.transport,
        "host": args.host,
        "port": args.port,
        "path": args.path,
        "log_level": args.log_level,
        "allow_remote_http": args.allow_remote_http,
        "transport_security": transport_security,
    }


def main() -> int:
    """
    Run the MCP server
    """
    try:
        args = parse_args()
        configure_mcp_runtime(args)
        if args.health:
            print(json.dumps(health_payload(args), sort_keys=True))
            return 0

        print("=== ODOO MCP SERVER STARTING ===", file=sys.stderr)
        print(f"Python version: {sys.version}", file=sys.stderr)
        print("Environment variables:", file=sys.stderr)
        for key, value in os.environ.items():
            if key.startswith(("ODOO_", "MCP_")):
                if is_secret_env_key(key):
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
