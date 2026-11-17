"""Tests for MCP server."""

import pytest
import json

from claude_innit.server import create_server, exclude_patterns_from_env


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
        assert "list_memories" in tool_names
        assert "admin_sync" in tool_names
        assert "admin_check_integrity" in tool_names


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


def _note(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: reference\n---\nbody\n")
    return path


async def test_vault_index_tool_honors_the_vault_exclude_file(tmp_path, monkeypatch):
    """The server's own vault_index must skip what <vault>/.index-exclude lists,
    with EXCLUDE_INDEX_PATTERNS unset — the registration never sets it."""
    monkeypatch.delenv("EXCLUDE_INDEX_PATTERNS", raising=False)
    vault = tmp_path / "vault"
    hidden = _note(vault / "Private" / "People" / "a.md")
    kept = _note(vault / "Research" / "note.md")
    (vault / ".index-exclude").write_text("/Private/People/\n")
    server = create_server(
        tmp_path / "test.db",
        tmp_path / "memories",
        vault_root=str(vault),
        exclude_index_patterns=exclude_patterns_from_env(),
    )

    await server.call_tool("vault_index", {})

    assert server.db.get_vault_file(str(hidden)) is None
    assert server.db.get_vault_file(str(kept)) is not None


async def test_vault_index_tool_keeps_builtin_excludes_by_default(
    tmp_path, monkeypatch
):
    """With the env var unset and no vault file, the built-in list still applies.
    The old two-pattern default replaced it, so node_modules was indexed."""
    monkeypatch.delenv("EXCLUDE_INDEX_PATTERNS", raising=False)
    vault = tmp_path / "vault"
    dep = _note(vault / "node_modules" / "pkg" / "README.md")
    server = create_server(
        tmp_path / "test.db",
        tmp_path / "memories",
        vault_root=str(vault),
        exclude_index_patterns=exclude_patterns_from_env(),
    )

    await server.call_tool("vault_index", {})

    assert server.db.get_vault_file(str(dep)) is None


def test_exclude_patterns_from_env(monkeypatch):
    monkeypatch.delenv("EXCLUDE_INDEX_PATTERNS", raising=False)
    assert exclude_patterns_from_env() == []
    monkeypatch.setenv("EXCLUDE_INDEX_PATTERNS", "/a/:/b c/:")
    assert exclude_patterns_from_env() == ["/a/", "/b c/"]
