"""Server-side WAL checkpoints: after write batches and at shutdown (Refs #6).

Every database here lives under pytest's tmp_path; no listener is started and
the embedding model is never loaded.
"""

import asyncio

import pytest

from claude_innit import server as server_mod
from claude_innit.server import build_http_app, create_server


@pytest.fixture
def innit(tmp_path):
    memories = tmp_path / "memories"
    memories.mkdir()
    s = create_server(tmp_path / "test.db", memories, warm_embeddings=False)
    assert tmp_path in s.db.db_path.parents
    yield s
    s.db.close()
    s.sync.db.close()


def _spy(monkeypatch, db):
    calls = []
    real = db.checkpoint_truncate

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "checkpoint_truncate", spy)
    return calls


@pytest.mark.parametrize("tool", sorted(server_mod._MAINTENANCE_TOOLS))
async def test_maintenance_tools_checkpoint_after_batch(innit, monkeypatch, tool):
    """Every large-write tool resets the WAL when its batch is done."""
    calls = _spy(monkeypatch, innit.db)
    await innit.call_tool(tool, {})
    assert len(calls) == 1


async def test_maintenance_tool_checkpoints_even_when_tool_raises(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.db)

    async def boom(name, arguments):
        raise RuntimeError("batch failed")

    monkeypatch.setattr(innit, "_call_tool", boom)
    with pytest.raises(RuntimeError):
        await innit.call_tool("admin_sync", {})
    assert len(calls) == 1


async def test_ordinary_tools_do_not_checkpoint(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.db)
    await innit.call_tool("list_memories", {})
    assert calls == []


async def test_background_sync_checkpoints_afterwards(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.sync.db)
    await server_mod._background_sync(innit.sync)
    assert len(calls) == 1


async def test_http_lifespan_checkpoints_on_shutdown(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.db)
    app = build_http_app(innit, run_background_sync=False, warm_in_background=False)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        assert calls == []
    assert len(calls) == 1
