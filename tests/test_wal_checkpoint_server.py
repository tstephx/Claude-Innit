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


async def test_vault_index_with_a_real_vault_checkpoints_exactly_once(
    innit, monkeypatch, tmp_path
):
    """The indexing thread's own connection closes without a checkpoint; the
    single checkpoint comes after the whole batch."""
    from claude_innit.db.database import MemoryDatabase

    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    (vault / "Notes" / "one.md").write_text("# One\n\nsome text about checkpoints\n")
    calls = []
    real = MemoryDatabase.checkpoint_truncate

    def spy(self, *args, **kwargs):
        calls.append(self.db_path)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(MemoryDatabase, "checkpoint_truncate", spy)
    monkeypatch.setattr(innit, "embedding_store", None)  # never load the model
    result = await innit.call_tool("vault_index", {"vault_root": str(vault)})

    assert '"error"' not in result[0].text
    assert calls == [innit.db.db_path]


def _patch_stdio_main(monkeypatch, tmp_path, run):
    """Wire server.main() to a tmp_path database and fake stdio streams."""
    from contextlib import asynccontextmanager

    db_path = tmp_path / "stdio" / "test.db"
    created = []

    def config():
        return {
            "db_path": db_path,
            "memories_dir": tmp_path / "stdio" / "no-memories",
            "vault_root": None,
            "extra_index_paths": [],
            "exclude_index_patterns": [],
        }

    real_create = server_mod.create_server

    def create(**cfg):
        assert tmp_path in cfg["db_path"].parents  # never the repo's data/
        s = real_create(**cfg, warm_embeddings=False)
        monkeypatch.setattr(s.server, "run", run)
        s.checkpoint_calls = _spy(monkeypatch, s.db)
        created.append(s)
        return s

    @asynccontextmanager
    async def fake_stdio():
        yield object(), object()

    monkeypatch.setattr(server_mod, "_server_config_from_env", config)
    monkeypatch.setattr(server_mod, "create_server", create)
    monkeypatch.setattr(server_mod, "stdio_server", fake_stdio)
    return created


async def test_stdio_main_checkpoints_on_clean_shutdown(monkeypatch, tmp_path):
    async def run(*args, **kwargs):
        return None

    created = _patch_stdio_main(monkeypatch, tmp_path, run)
    await server_mod.main()
    assert len(created) == 1
    assert len(created[0].checkpoint_calls) == 1
    created[0].db.close()
    created[0].sync.db.close()


async def test_stdio_main_checkpoints_when_the_run_loop_raises(monkeypatch, tmp_path):
    async def run(*args, **kwargs):
        raise RuntimeError("stream broke")

    created = _patch_stdio_main(monkeypatch, tmp_path, run)
    with pytest.raises(RuntimeError):
        await server_mod.main()
    assert len(created[0].checkpoint_calls) == 1
    created[0].db.close()
    created[0].sync.db.close()
