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


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "wal-test.db"
    assert tmp_path in path.parents  # never the repo's data/ directory
    return path


def test_new_connection_sets_journal_size_limit(db_path):
    """Every new connection carries the 64 MiB WAL size limit."""
    db = MemoryDatabase(db_path)
    row = db.execute("PRAGMA journal_size_limit").fetchone()
    assert database.JOURNAL_SIZE_LIMIT_BYTES == 64 * 1024 * 1024
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
    """checkpoint_truncate() leaves the -wal file at or under the limit."""
    db = MemoryDatabase(db_path)
    _grow_wal(db)
    assert _wal_size(db_path) > SMALL_LIMIT

    result = db.checkpoint_truncate()

    assert result is not None
    assert result[0] == 0  # not busy
    assert _wal_size(db_path) == 0
    assert _wal_size(db_path) <= database.JOURNAL_SIZE_LIMIT_BYTES
    db.close()


def test_busy_checkpoint_is_logged_and_does_not_raise(db_path, caplog):
    """A reader pinning the log makes the checkpoint busy: warn, don't raise."""
    db = MemoryDatabase(db_path)
    _grow_wal(db)

    reader = sqlite3.connect(str(db_path))
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM filler").fetchone()  # read snapshot held
        _grow_wal(db)  # frames the reader's snapshot does not include

        with caplog.at_level(logging.WARNING, logger=database.logger.name):
            result = db.checkpoint_truncate(timeout=0.1)

        assert result is not None
        assert result[0] == 1  # busy
        assert _wal_size(db_path) > 0  # not reset
        assert any(
            "busy" in rec.getMessage() and rec.levelno == logging.WARNING
            for rec in caplog.records
        )
    finally:
        reader.rollback()
        reader.close()

    # With the reader gone the same call resets the log.
    result = db.checkpoint_truncate(timeout=0.1)
    assert result is not None and result[0] == 0
    assert _wal_size(db_path) == 0
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


def test_close_truncates_wal_while_other_connections_stay_open(db_path):
    """close() resets the log even when it is not the last connection
    (SQLite only cleans the WAL up by itself on the *last* close)."""
    db = MemoryDatabase(db_path)
    other = sqlite3.connect(str(db_path))
    try:
        other.execute("SELECT COUNT(*) FROM memories").fetchall()
        _grow_wal(db)
        assert _wal_size(db_path) > SMALL_LIMIT

        db.close()

        assert _wal_size(db_path) == 0
    finally:
        other.close()


def test_close_with_open_transaction_does_not_wait_or_raise(db_path):
    """close() with uncommitted work skips the checkpoint and still closes."""
    db = MemoryDatabase(db_path)
    db.execute("CREATE TABLE filler (x BLOB)")
    db.commit()
    db.execute("INSERT INTO filler VALUES (randomblob(16))")  # no commit
    db.close()
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")


class TestIntegrityCheckReportsWal:
    def test_reports_wal_bytes(self, db_path):
        db = MemoryDatabase(db_path)
        _grow_wal(db)
        result = db.integrity_check(auto_repair=False)
        assert result["wal_bytes"] == _wal_size(db_path)
        assert result["wal_bytes"] > 0
        assert result["warnings"] == []
        assert result["status"] == "healthy"
        db.close()

    def test_warns_above_threshold_without_changing_status(self, db_path, monkeypatch):
        monkeypatch.setattr(database, "WAL_SIZE_WARN_BYTES", 1024, raising=False)
        db = MemoryDatabase(db_path)
        _grow_wal(db)
        result = db.integrity_check(auto_repair=False)
        assert len(result["warnings"]) == 1
        assert "WAL" in result["warnings"][0]
        assert result["status"] == "healthy"
        assert result["issues"] == []
        db.close()


def test_close_twice_is_a_noop(db_path):
    """A second close() stays silent, as it was before the checkpoint."""
    db = MemoryDatabase(db_path)
    db.close()
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
