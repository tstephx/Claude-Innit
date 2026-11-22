"""Server-side WAL checkpoints: after write batches and at shutdown (Refs #6).

Every database here lives under pytest's tmp_path; no listener is started and
the embedding model is never loaded.
"""

import asyncio
import logging
import threading

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
    """Record the thread of every checkpoint_truncate call, then run it."""
    calls = []
    real = db.checkpoint_truncate

    def spy(*args, **kwargs):
        calls.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "checkpoint_truncate", spy)
    return calls


def _off_the_event_loop(calls) -> bool:
    """The tests' event loop runs on the main thread."""
    return all(t is not threading.main_thread() for t in calls)


async def _wait_for(event: threading.Event, seconds: float = 2.0) -> None:
    """Wait on the loop for a worker thread's event; fail, never hang, if it
    does not come."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not event.is_set():
        assert loop.time() < deadline, "the worker thread never got there"
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("tool", sorted(server_mod._MAINTENANCE_TOOLS))
async def test_maintenance_tools_checkpoint_after_batch(innit, monkeypatch, tool):
    """Every large-write tool resets the WAL when its batch is done."""
    calls = _spy(monkeypatch, innit.db)
    await innit.call_tool(tool, {})
    await innit.drain_checkpoints()
    assert len(calls) == 1
    assert _off_the_event_loop(calls)


async def test_maintenance_tool_checkpoints_even_when_tool_raises(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.db)

    async def boom(name, arguments):
        raise RuntimeError("batch failed")

    monkeypatch.setattr(innit, "_call_tool", boom)
    with pytest.raises(RuntimeError):
        await innit.call_tool("admin_sync", {})
    await innit.drain_checkpoints()
    assert len(calls) == 1


async def test_maintenance_tool_result_is_not_held_behind_the_checkpoint(
    innit, monkeypatch
):
    """The checkpoint can take long (its PASSIVE step has no time bound), so
    the tool's result and the maintenance lock must not wait for it: a client
    timeout during the checkpoint would lose the result of a finished batch."""
    release = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    def slow_checkpoint(*args, **kwargs):
        started.set()
        release.wait(timeout=2)
        finished.set()

    monkeypatch.setattr(innit.db, "checkpoint_truncate", slow_checkpoint)
    try:
        result = await asyncio.wait_for(innit.call_tool("admin_sync", {}), timeout=1)

        assert result  # the tool's own answer came back
        assert not finished.is_set()  # ...while the checkpoint was still running
        assert not innit._maintenance_lock.locked()
    finally:
        release.set()
    await innit.drain_checkpoints()
    assert started.is_set() and finished.is_set()
    assert innit._checkpoint_tasks == set()  # finished tasks are not kept


async def test_http_shutdown_waits_for_a_background_checkpoint(innit, monkeypatch):
    """Shutdown lets a running background checkpoint finish before it starts
    its own, so the two never overlap."""
    order = []
    release = threading.Event()
    started = threading.Event()

    def checkpoint(*args, **kwargs):
        first = not started.is_set()
        started.set()
        if first:
            release.wait(timeout=2)
        order.append("background" if first else "shutdown")

    monkeypatch.setattr(innit.db, "checkpoint_truncate", checkpoint)
    app = build_http_app(innit, run_background_sync=False, warm_in_background=False)
    try:
        async with app.router.lifespan_context(app):
            await innit.call_tool("admin_sync", {})
            await _wait_for(started)
            asyncio.get_running_loop().call_later(0.2, release.set)
        assert order == ["background", "shutdown"]
    finally:
        release.set()
        await innit.drain_checkpoints()


async def test_drain_checkpoints_with_nothing_pending_returns(innit):
    await asyncio.wait_for(innit.drain_checkpoints(), timeout=1)


async def test_ordinary_tools_do_not_checkpoint(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.db)
    await innit.call_tool("list_memories", {})
    await innit.drain_checkpoints()
    assert calls == []


async def test_background_sync_checkpoints_afterwards(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.sync.db)
    await server_mod._background_sync(innit.sync)
    assert len(calls) == 1
    assert _off_the_event_loop(calls)


async def test_background_sync_checkpoints_when_the_sync_fails(innit, monkeypatch):
    """A failed startup sync may still have written: checkpoint anyway."""
    calls = _spy(monkeypatch, innit.sync.db)

    def boom():
        raise RuntimeError("sync failed")

    monkeypatch.setattr(innit.sync, "sync_all", boom)
    await server_mod._background_sync(innit.sync)  # the failure is swallowed
    assert len(calls) == 1


async def test_background_sync_checkpoints_when_cancelled(innit, monkeypatch):
    """HTTP shutdown cancels the startup sync task; its checkpoint still runs."""
    calls = _spy(monkeypatch, innit.sync.db)
    release = threading.Event()
    entered = threading.Event()

    def slow_sync():
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(innit.sync, "sync_all", slow_sync)
    task = asyncio.create_task(server_mod._background_sync(innit.sync))
    try:
        await _wait_for(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert len(calls) == 1


async def test_http_lifespan_checkpoints_on_shutdown(innit, monkeypatch):
    calls = _spy(monkeypatch, innit.db)
    app = build_http_app(innit, run_background_sync=False, warm_in_background=False)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        assert calls == []
    assert len(calls) == 1
    assert _off_the_event_loop(calls)


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
    await innit.drain_checkpoints()

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
    assert _off_the_event_loop(created[0].checkpoint_calls)
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


async def test_stdio_shutdown_waits_for_a_background_checkpoint(monkeypatch, tmp_path):
    """As in HTTP mode: a checkpoint a tool started finishes before the
    shutdown checkpoint starts."""
    order = []
    release = threading.Event()
    started = threading.Event()

    def checkpoint(*args, **kwargs):
        first = not started.is_set()
        started.set()
        if first:
            release.wait(timeout=2)
        order.append("background" if first else "shutdown")

    async def run(*args, **kwargs):
        monkeypatch.setattr(created[0].db, "checkpoint_truncate", checkpoint)
        await created[0].call_tool("admin_sync", {})
        await _wait_for(started)
        asyncio.get_running_loop().call_later(0.2, release.set)

    created = _patch_stdio_main(monkeypatch, tmp_path, run)
    try:
        await server_mod.main()
        assert order == ["background", "shutdown"]
    finally:
        release.set()
    created[0].db.close()
    created[0].sync.db.close()


async def test_failing_background_checkpoint_is_logged_and_does_not_break_drain(
    innit, monkeypatch, caplog
):
    """checkpoint_truncate() never raises, but if the worker thread did, the
    failure is logged, the tool result is unaffected and drain returns."""

    def boom(*args, **kwargs):
        raise RuntimeError("checkpoint thread failed")

    monkeypatch.setattr(innit.db, "checkpoint_truncate", boom)
    with caplog.at_level(logging.WARNING, logger=server_mod.logger.name):
        result = await innit.call_tool("admin_sync", {})
        await innit.drain_checkpoints()
        await asyncio.sleep(0)  # let the done callback run

    assert result
    assert innit._checkpoint_tasks == set()
    assert any("WAL checkpoint task failed" in r.getMessage() for r in caplog.records)
