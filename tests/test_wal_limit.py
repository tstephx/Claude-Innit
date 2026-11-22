"""WAL size control: journal_size_limit + TRUNCATE checkpoints (Refs #6).

Every database here lives under pytest's tmp_path. Nothing in this file may
open the repo's data/ directory.
"""

import logging
import sqlite3
from pathlib import Path

import pytest

from claude_innit.db import database
from claude_innit.db.database import MemoryDatabase

SMALL_LIMIT = 256 * 1024
FILL_BYTES = 2 * 1024 * 1024


def _wal_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + "-wal")


def _wal_size(db_path: Path) -> int:
    wal = _wal_path(db_path)
    return wal.stat().st_size if wal.exists() else 0


def _grow_wal(db: MemoryDatabase, total_bytes: int = FILL_BYTES) -> None:
    """Write ``total_bytes`` of blobs in one transaction (one WAL burst)."""
    db.execute("CREATE TABLE IF NOT EXISTS filler (x BLOB)")
    rows = total_bytes // 4096
    db.execute(
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) "
        "INSERT INTO filler SELECT randomblob(4096) FROM n",
        (rows,),
    )
    db.commit()


def _record_checkpoint_sql(monkeypatch) -> list[str]:
    """Record every statement run on connections opened from now on."""
    statements: list[str] = []
    real_connect = sqlite3.connect

    class Recording:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            statements.append(sql)
            return self._conn.execute(sql, *args)

        def close(self):
            self._conn.close()

    def connect(*args, **kwargs):
        return Recording(real_connect(*args, **kwargs))

    monkeypatch.setattr(database.sqlite3, "connect", connect)
    return statements


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "wal-test.db"
    assert tmp_path in path.parents  # never the repo's data/ directory
    return path


def test_wal_constants():
    """The defaults the issue and the PR name."""
    assert database.JOURNAL_SIZE_LIMIT_BYTES == 64 * 1024 * 1024
    assert database.CHECKPOINT_BUSY_TIMEOUT_SECONDS == 2.0
    assert database.WAL_SIZE_WARN_BYTES == 1024 * 1024 * 1024


def test_new_connection_sets_journal_size_limit(db_path):
    """Every new connection carries the 64 MiB WAL size limit."""
    db = MemoryDatabase(db_path)
    row = db.execute("PRAGMA journal_size_limit").fetchone()
    assert row[0] == database.JOURNAL_SIZE_LIMIT_BYTES
    db.close()


def test_wal_is_cut_back_to_limit_after_reset(db_path, monkeypatch):
    """Once a checkpoint lets the log restart, the next commit truncates the
    -wal file to journal_size_limit instead of leaving it at its high-water mark.
    """
    monkeypatch.setattr(
        database, "JOURNAL_SIZE_LIMIT_BYTES", SMALL_LIMIT, raising=False
    )
    db = MemoryDatabase(db_path)
    _grow_wal(db)
    assert _wal_size(db_path) > SMALL_LIMIT  # the log really outgrew the limit

    # A complete (non-truncating) checkpoint, then one small commit that
    # restarts the log from the beginning.
    db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
    db.execute("INSERT INTO filler VALUES (randomblob(16))")
    db.commit()

    assert _wal_size(db_path) <= SMALL_LIMIT
    db.close()


def test_checkpoint_truncate_shrinks_wal_file(db_path):
    """checkpoint_truncate() leaves the -wal file at zero bytes."""
    db = MemoryDatabase(db_path)
    _grow_wal(db)
    assert _wal_size(db_path) > SMALL_LIMIT

    result = db.checkpoint_truncate()

    assert isinstance(result, tuple)
    assert all(isinstance(v, int) for v in result)
    assert result[0] == 0  # not busy
    assert _wal_size(db_path) == 0
    db.close()


def test_checkpoint_backfills_passively_before_truncating(db_path, monkeypatch):
    """The copy back into the database happens in a PASSIVE checkpoint (no
    write lock), so the TRUNCATE that follows has next to nothing to copy
    while it holds the write lock."""
    db = MemoryDatabase(db_path)
    _grow_wal(db)
    statements = _record_checkpoint_sql(monkeypatch)

    result = db.checkpoint_truncate()

    assert statements == [
        "PRAGMA wal_checkpoint(PASSIVE)",
        "PRAGMA wal_checkpoint(TRUNCATE)",
    ]
    assert result is not None and result[0] == 0
    assert _wal_size(db_path) == 0
    db.close()


def test_pinned_log_skips_truncate_and_is_logged(db_path, monkeypatch, caplog):
    """A reader holding an older snapshot pins the log. The checkpoint must
    then not take the write lock at all (no TRUNCATE): it reports busy, logs a
    warning and does not raise."""
    db = MemoryDatabase(db_path)
    _grow_wal(db)

    reader = sqlite3.connect(str(db_path))
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM filler").fetchone()  # snapshot held
        _grow_wal(db)  # frames the reader's snapshot does not include
        statements = _record_checkpoint_sql(monkeypatch)

        with caplog.at_level(logging.WARNING, logger=database.logger.name):
            result = db.checkpoint_truncate(timeout=0.1)

        assert statements == ["PRAGMA wal_checkpoint(PASSIVE)"]
        assert result is not None
        assert result[0] == 1  # busy
        assert result[2] < result[1]  # not every frame could be copied back
        assert _wal_size(db_path) > 0  # not reset
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "busy" in warnings[0].getMessage()
    finally:
        reader.rollback()
        reader.close()

    # With the reader gone the same call resets the log.
    result = db.checkpoint_truncate(timeout=0.1)
    assert result is not None and result[0] == 0
    assert _wal_size(db_path) == 0
    db.close()


def test_reader_on_latest_snapshot_makes_truncate_busy(db_path, monkeypatch, caplog):
    """A reader on the newest snapshot lets the backfill finish but blocks the
    reset: TRUNCATE runs, reports busy, which is logged and does not raise."""
    db = MemoryDatabase(db_path)
    _grow_wal(db)

    reader = sqlite3.connect(str(db_path))
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM filler").fetchone()
        statements = _record_checkpoint_sql(monkeypatch)

        with caplog.at_level(logging.WARNING, logger=database.logger.name):
            result = db.checkpoint_truncate(timeout=0.1)

        assert statements == [
            "PRAGMA wal_checkpoint(PASSIVE)",
            "PRAGMA wal_checkpoint(TRUNCATE)",
        ]
        assert result is not None
        assert result[0] == 1
        assert _wal_size(db_path) > 0
        assert any("busy" in r.getMessage() for r in caplog.records)
    finally:
        reader.rollback()
        reader.close()
    db.close()


def test_checkpoint_error_is_logged_and_does_not_raise(tmp_path, caplog):
    """An SQLite error during the checkpoint is swallowed and logged."""
    db = MemoryDatabase(tmp_path / "wal-test.db")
    # Point the checkpoint at something that is not a database.
    bogus = tmp_path / "not-a-db.db"
    bogus.write_bytes(b"this is not an sqlite file, just bytes " * 64)
    db.db_path = bogus

    with caplog.at_level(logging.WARNING, logger=database.logger.name):
        assert db.checkpoint_truncate(timeout=0.1) is None

    assert any("checkpoint" in rec.getMessage() for rec in caplog.records)
    db.db_path = tmp_path / "wal-test.db"
    db.close()


def test_checkpoint_path_error_is_logged_and_does_not_raise(tmp_path, caplog):
    """A path that cannot be resolved is logged too, not raised."""

    class Unresolvable:
        name = "x.db"

        def resolve(self):
            raise OSError("cannot resolve")

        def __str__(self):
            return "unresolvable"

    db = MemoryDatabase(tmp_path / "wal-test.db")
    db.db_path = Unresolvable()

    with caplog.at_level(logging.WARNING, logger=database.logger.name):
        assert db.checkpoint_truncate(timeout=0.1) is None
        assert db.wal_size_bytes() == 0

    assert any("checkpoint" in rec.getMessage() for rec in caplog.records)
    db.db_path = tmp_path / "wal-test.db"
    db.close()


def test_checkpoint_never_creates_a_missing_database(tmp_path, caplog):
    """If the database file is gone, the checkpoint logs and creates nothing."""
    db = MemoryDatabase(tmp_path / "wal-test.db")
    missing = tmp_path / "moved away" / "gone.db"
    missing.parent.mkdir()
    db.db_path = missing

    with caplog.at_level(logging.WARNING, logger=database.logger.name):
        assert db.checkpoint_truncate(timeout=0.1) is None

    assert not missing.exists()
    assert list(missing.parent.iterdir()) == []
    assert any("checkpoint" in rec.getMessage() for rec in caplog.records)
    db.db_path = tmp_path / "wal-test.db"
    db.close()


def test_wal_size_follows_a_symlinked_database(tmp_path):
    """SQLite keeps the -wal beside the real file, so the size is read there."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real = real_dir / "wal-test.db"
    db = MemoryDatabase(real)
    _grow_wal(db)
    link = tmp_path / "link.db"
    link.symlink_to(real)

    db.db_path = link
    assert db.wal_size_bytes() == _wal_size(real) > 0
    db.db_path = real
    db.close()


def test_close_does_not_checkpoint(db_path, monkeypatch):
    """close() only closes. Checkpoints run at the explicit call sites, so a
    close can never wait on locks or copy the log under the write lock."""
    db = MemoryDatabase(db_path)
    calls = []
    monkeypatch.setattr(db, "checkpoint_truncate", lambda *a, **k: calls.append(1))
    db.execute("CREATE TABLE filler (x BLOB)")
    db.execute("INSERT INTO filler VALUES (randomblob(16))")
    db.commit()
    db.close()
    assert calls == []
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    db.close()  # a second close stays a no-op


class TestIntegrityCheckReportsWal:
    def test_reports_wal_bytes(self, db_path):
        db = MemoryDatabase(db_path)
        _grow_wal(db)
        result = db.integrity_check(auto_repair=False)
        assert isinstance(result["wal_bytes"], int)
        assert isinstance(result["warnings"], list)
        assert result["wal_bytes"] == _wal_size(db_path)
        assert result["wal_bytes"] > 0
        assert result["warnings"] == []
        assert result["status"] == "healthy"
        db.close()

    def test_no_warning_at_exactly_the_threshold(self, db_path, monkeypatch):
        db = MemoryDatabase(db_path)
        _grow_wal(db)
        monkeypatch.setattr(database, "WAL_SIZE_WARN_BYTES", _wal_size(db_path))
        result = db.integrity_check(auto_repair=False)
        assert result["warnings"] == []
        db.close()

    def test_warns_above_threshold_without_changing_status(self, db_path, monkeypatch):
        db = MemoryDatabase(db_path)
        _grow_wal(db)
        monkeypatch.setattr(database, "WAL_SIZE_WARN_BYTES", _wal_size(db_path) - 1)
        result = db.integrity_check(auto_repair=False)
        assert len(result["warnings"]) == 1
        assert isinstance(result["warnings"][0], str)
        assert "WAL" in result["warnings"][0]
        assert result["status"] == "healthy"
        assert result["issues"] == []
        db.close()
