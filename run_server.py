#!/usr/bin/env python
"""
Standalone script to run the Odoo MCP server
Uses the same approach as in the official MCP SDK examples
"""

import datetime
import logging
import os
import sys

import anyio
from mcp.server.stdio import stdio_server

from odoo_mcp.server import mcp  # FastMCP instance from our code

SECRET_ENV_KEYS = {"ODOO_PASSWORD", "ODOO_API_KEY"}


def setup_logging():
    """Set up logging to both console and file"""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"mcp_server_{timestamp}.log")

    # Configure logging
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    # Format for both handlers
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


def main() -> int:
    """
    Run the MCP server based on the official examples
    """
    logger = setup_logging()

    try:
        logger.info("=== ODOO MCP SERVER STARTING ===")
        logger.info(f"Python version: {sys.version}")
        logger.info("Environment variables:")
        for key, value in os.environ.items():
            if key.startswith(("ODOO_", "MCP_")):
                if key in SECRET_ENV_KEYS:
                    logger.info(f"  {key}: ***hidden***")
                else:
                    logger.info(f"  {key}: {value}")

        logger.info(f"MCP object type: {type(mcp)}")

        # Run server in stdio mode like the official examples
        async def arun():
            logger.info("Starting Odoo MCP server with stdio transport...")
            async with stdio_server() as streams:
                logger.info("Stdio server initialized, running MCP server...")
                await mcp._mcp_server.run(
                    streams[0],
                    streams[1],
                    mcp._mcp_server.create_initialization_options(),
                )

        # Run server
        anyio.run(arun)
        logger.info("MCP server stopped normally")
        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
