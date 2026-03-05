"""Tests for MCP server."""

import pytest
import asyncio
import json
from pathlib import Path

from claude_innit.server import create_server


class TestServer:
    """Tests for MCP server creation."""

    def test_creates_server(self, tmp_path):
        """Server creates with valid configuration."""
        server = create_server(
            db_path=tmp_path / "test.db",
            memories_dir=tmp_path / "memories",
        )

        assert server is not None
        assert server.name == "claude-innit"

    def test_registers_tools(self, tmp_path):
        """Server registers all expected tools."""
        server = create_server(
            db_path=tmp_path / "test.db",
            memories_dir=tmp_path / "memories",
        )

        tool_names = [tool.name for tool in server.list_tools()]

        assert "get_context" in tool_names
        assert "search" in tool_names
        assert "remember" in tool_names
        assert "forget" in tool_names
        assert "save_session" in tool_names
        assert "sync" in tool_names


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_returns_error(tmp_path):
    """Unknown tool name returns error TextContent, does not raise."""
    server = create_server(tmp_path / "test.db", tmp_path / "memories")
    result = await server.call_tool("nonexistent_tool", {})
    assert len(result) == 1
    payload = json.loads(result[0].text)
    assert "error" in payload


@pytest.mark.asyncio
async def test_call_tool_bad_args_returns_error(tmp_path):
    """Tool called with missing required args returns error, does not raise."""
    server = create_server(tmp_path / "test.db", tmp_path / "memories")
    # forget requires memory_id; passing empty dict triggers KeyError
    result = await server.call_tool("forget", {})
    assert len(result) == 1
    payload = json.loads(result[0].text)
    assert "error" in payload
