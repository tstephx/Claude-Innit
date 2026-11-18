"""Tests for the shared streamable-HTTP server mode.

These start a real uvicorn listener on an ephemeral loopback port and talk to
it with the MCP SDK's HTTP client, so they exercise the same path Claude Code
uses with ``type: http``. The embedding model is never loaded: warm-up is
disabled and every search uses the text (FTS) path.
"""

import asyncio
import json

import pytest
import uvicorn
from mcp.client import Client

from claude_innit import server as server_mod
from claude_innit.server import (
    _http_settings_from_env,
    build_http_app,
    create_server,
)


def _seed_memories(innit):
    innit.db.insert_memory(
        id="project/aaaa1111",
        category="project",
        content="shared http transport keeps one embedding model resident",
    )
    innit.db.insert_memory(
        id="personal/bbbb2222",
        category="personal",
        content="prefers worktrees over committing to main",
    )


async def _start(app):
    """Run ``app`` on 127.0.0.1:<ephemeral>; return (uvicorn server, task, url)."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    for _ in range(200):
        if srv.started:
            break
        if task.done():
            task.result()  # surface the startup error
        await asyncio.sleep(0.025)
    assert srv.started, "uvicorn did not start"
    port = srv.servers[0].sockets[0].getsockname()[1]
    return srv, task, f"http://127.0.0.1:{port}/mcp"


async def _stop(srv, task):
    srv.should_exit = True
    await asyncio.wait_for(task, timeout=10)


@pytest.fixture
def innit(tmp_path):
    memories = tmp_path / "memories"
    memories.mkdir()
    s = create_server(tmp_path / "test.db", memories, warm_embeddings=False)
    _seed_memories(s)
    yield s
    s.db.close()


def _texts(result):
    return [json.loads(c.text) for c in result.content]


async def test_http_server_starts_and_lists_tools(innit):
    app = build_http_app(innit, run_background_sync=False, warm_in_background=False)
    srv, task, url = await _start(app)
    try:
        async with Client(url) as client:
            tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert {"get_context", "search", "remember", "vault_search"} <= names
        assert names == {t.name for t in innit.list_tools()}
    finally:
        await _stop(srv, task)


async def test_two_concurrent_clients_search(innit):
    app = build_http_app(innit, run_background_sync=False, warm_in_background=False)
    srv, task, url = await _start(app)

    async def session(query, rounds=5):
        async with Client(url) as client:
            out = []
            for _ in range(rounds):
                res = await client.call_tool(
                    "search", {"query": query, "method": "text"}
                )
                assert not res.is_error
                out.append(_texts(res)[0])
            return out

    try:
        a, b = await asyncio.gather(session("embedding"), session("worktrees"))
    finally:
        await _stop(srv, task)

    assert all(any(r["id"] == "project/aaaa1111" for r in hits) for hits in a)
    assert all(any(r["id"] == "personal/bbbb2222" for r in hits) for hits in b)
    # Each client sees only its own query's hits, not the other's.
    assert not any(r["id"] == "personal/bbbb2222" for hits in a for r in hits)


async def test_write_from_one_client_visible_to_other(innit):
    app = build_http_app(innit, run_background_sync=False, warm_in_background=False)
    srv, task, url = await _start(app)
    try:
        async with Client(url) as writer, Client(url) as reader:
            res = await writer.call_tool(
                "remember",
                {
                    "content": "zebracorn sentinel fact",
                    "category": "project",
                    "project": "http-test",
                },
            )
            assert not res.is_error, res
            hits = _texts(
                await reader.call_tool(
                    "search", {"query": "zebracorn", "method": "text"}
                )
            )[0]
    finally:
        await _stop(srv, task)
    assert any("zebracorn" in h["content"] for h in hits)


async def test_background_sync_runs_once_per_process(innit, monkeypatch):
    calls = []

    async def fake_sync(sync):
        calls.append(sync)

    monkeypatch.setattr(server_mod, "_background_sync", fake_sync)
    app = build_http_app(innit, run_background_sync=True, warm_in_background=False)
    srv, task, url = await _start(app)
    try:
        for _ in range(3):
            async with Client(url) as client:
                await client.list_tools()
        await asyncio.sleep(0)
    finally:
        await _stop(srv, task)
    assert len(calls) == 1


async def test_http_mode_does_not_preload_model(tmp_path):
    s = create_server(tmp_path / "t.db", tmp_path / "m", warm_embeddings=False)
    try:
        assert s.embedding_store._model is None
    finally:
        s.db.close()


async def test_maintenance_tools_serialized(innit, monkeypatch):
    active = 0
    peak = 0

    async def slow(name, arguments):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return []

    monkeypatch.setattr(innit, "_call_tool", slow)
    await asyncio.gather(
        innit.call_tool("vault_index", {}),
        innit.call_tool("vault_rechunk", {}),
        innit.call_tool("admin_sync", {}),
    )
    assert peak == 1
    # Ordinary tools are not serialized behind the lock.
    peak = 0
    await asyncio.gather(
        innit.call_tool("search", {}), innit.call_tool("get_context", {})
    )
    assert peak == 2


def test_http_settings_default_to_loopback(monkeypatch):
    for k in (
        "CLAUDE_INNIT_HOST",
        "CLAUDE_INNIT_PORT",
        "CLAUDE_INNIT_SESSION_IDLE_TIMEOUT",
    ):
        monkeypatch.delenv(k, raising=False)
    s = _http_settings_from_env()
    assert s["host"] == "127.0.0.1"
    assert s["port"] == server_mod.DEFAULT_HTTP_PORT
    assert s["session_idle_timeout"] is None


def test_http_settings_refuse_non_loopback(monkeypatch):
    monkeypatch.setenv("CLAUDE_INNIT_HOST", "0.0.0.0")
    with pytest.raises(SystemExit):
        _http_settings_from_env()


def test_main_sync_dispatches_on_transport(monkeypatch):
    ran = []

    async def fake_stdio():
        ran.append("stdio")

    async def fake_http():
        ran.append("http")

    monkeypatch.setattr(server_mod, "main", fake_stdio)
    monkeypatch.setattr(server_mod, "main_http", fake_http)
    monkeypatch.delenv("CLAUDE_INNIT_TRANSPORT", raising=False)
    server_mod.main_sync()
    monkeypatch.setenv("CLAUDE_INNIT_TRANSPORT", "http")
    server_mod.main_sync()
    assert ran == ["stdio", "http"]
