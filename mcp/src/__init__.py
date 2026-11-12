"""Hookshot workbench MCP operations."""
import os

# FastMCP otherwise loads cwd/.env during import. This server never does.
os.environ["FASTMCP_ENV_FILE"] = os.devnull
