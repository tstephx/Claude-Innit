"""Tests for MCP server."""

import pytest

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
