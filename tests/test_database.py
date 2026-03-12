"""Tests for database module."""

import pytest

from claude_innit.db.database import MemoryDatabase


class TestMemoryDatabase:
    """Tests for MemoryDatabase."""

    def test_creates_tables(self, tmp_path):
        """Database creates required tables on init."""
        db_path = tmp_path / "test.db"
        db = MemoryDatabase(db_path)

        # Verify tables exist
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {row[0] for row in tables}

        assert "memories" in table_names
        assert "memories_fts" in table_names
        assert "embeddings" in table_names

    def test_insert_memory(self, tmp_path):
        """Can insert and retrieve a memory."""
        db_path = tmp_path / "test.db"
        db = MemoryDatabase(db_path)

        db.insert_memory(
            id="test-1",
            category="personal",
            source_file="personal/identity.md",
            content="My name is Taylor",
            metadata={"type": "identity"},
        )

        memory = db.get_memory("test-1")
        assert memory["content"] == "My name is Taylor"
        assert memory["category"] == "personal"

    def test_fts_search(self, tmp_path):
        """Full-text search finds matching memories."""
        db_path = tmp_path / "test.db"
        db = MemoryDatabase(db_path)

        db.insert_memory(
            id="test-1",
            category="personal",
            source_file="personal/identity.md",
            content="My name is Taylor Stephens",
            metadata={},
        )
        db.insert_memory(
            id="test-2",
            category="project",
            source_file="projects/test.md",
            content="Working on book processing",
            metadata={},
        )

        results = db.fts_search("Taylor")
        assert len(results) == 1
        assert results[0]["id"] == "test-1"


def test_wal_mode_enabled(tmp_path):
    """Database uses WAL journal mode."""
    db = MemoryDatabase(tmp_path / "test.db")
    row = db._conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_delete_memory(tmp_path):
    """delete_memory removes record and embeddings atomically."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(
        id="test/abc", category="personal", content="to delete", metadata={}
    )
    # Insert a fake embedding to verify it gets cleaned up too
    db._conn.execute(
        "INSERT INTO embeddings (memory_id, embedding, model) VALUES (?, ?, ?)",
        ("test/abc", b"\x00" * 10, "test"),
    )
    db._conn.commit()

    db.delete_memory("test/abc")

    assert db.get_memory("test/abc") is None
    embedding_row = db._conn.execute(
        "SELECT * FROM embeddings WHERE memory_id = ?", ("test/abc",)
    ).fetchone()
    assert embedding_row is None


def test_delete_memory_nonexistent_is_noop(tmp_path):
    """Deleting a nonexistent memory does not raise."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.delete_memory("does/not/exist")  # should not raise


def test_cleanup_orphan_vault_embeddings(tmp_path):
    """Should remove vault_embeddings rows with no matching vault_file."""
    import numpy as np

    db = MemoryDatabase(tmp_path / "test.db")

    # Insert a vault file and embedding
    db.upsert_vault_file("/test/real.md", "real.md", "content", "hash1", module="test")
    real_id = db.get_vault_file("/test/real.md")["file_id"]
    blob = np.zeros(384, dtype=np.float32).tobytes()
    db.execute(
        "INSERT INTO vault_embeddings (file_id, embedding, model) VALUES (?, ?, ?)",
        (real_id, blob, "test-model"),
    )

    # Create an orphan: commit pending work, then use a raw connection with FK off
    db.commit()
    import sqlite3

    raw_conn = sqlite3.connect(str(tmp_path / "test.db"))
    raw_conn.execute("PRAGMA foreign_keys = OFF")
    raw_conn.execute(
        "INSERT INTO vault_embeddings (file_id, embedding, model) VALUES (?, ?, ?)",
        (9999, blob, "test-model"),
    )
    raw_conn.commit()
    raw_conn.close()

    # Verify orphan exists
    count = db.execute("SELECT COUNT(*) FROM vault_embeddings").fetchone()[0]
    assert count == 2

    # Clean up
    removed = db.cleanup_orphan_vault_embeddings()
    assert removed == 1

    # Only real embedding remains
    count = db.execute("SELECT COUNT(*) FROM vault_embeddings").fetchone()[0]
    assert count == 1


def test_vault_embedding_stats(tmp_path):
    """vault_embedding_stats returns correct metrics."""
    import numpy as np

    db = MemoryDatabase(tmp_path / "test.db")

    # Empty state
    stats = db.vault_embedding_stats()
    assert stats["total_files"] == 0
    assert stats["legacy_embeddings"] == 0
    assert stats["chunk_embeddings"] == 0
    assert stats["model"] is None
    assert stats["mode"] == "legacy"

    # Add file + legacy embedding
    db.upsert_vault_file("/a.md", "a.md", "content", "h1")
    file_id = db.get_vault_file("/a.md")["file_id"]
    blob = np.zeros(384, dtype=np.float32).tobytes()
    db.execute(
        "INSERT INTO vault_embeddings (file_id, embedding, model) VALUES (?, ?, ?)",
        (file_id, blob, "all-MiniLM-L6-v2"),
    )

    # Add file without embedding
    db.upsert_vault_file("/b.md", "b.md", "content2", "h2")
    db.commit()

    stats = db.vault_embedding_stats()
    assert stats["total_files"] == 2
    assert stats["legacy_embeddings"] == 1
    assert stats["model"] == "all-MiniLM-L6-v2"
    assert stats["mode"] == "legacy"


@pytest.mark.parametrize(
    "bad_query",
    [
        '"unclosed quote',
        "OR AND NOT",
        "term*wildcard",
        "hello OR",
    ],
)
def test_fts_search_handles_special_chars(tmp_path, bad_query):
    """fts_search does not raise on FTS5 operator characters."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(
        id="test/1", category="personal", content="normal content", metadata={}
    )

    # Should not raise sqlite3.OperationalError
    result = db.fts_search(bad_query)
    assert isinstance(result, list)
