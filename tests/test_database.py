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


# ---------------------------------------------------------------------------
# Chunk DB method tests
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_file(tmp_path):
    """DB with a single vault file pre-inserted; yields (db, file_id)."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.upsert_vault_file("/vault/doc.md", "doc.md", "content here", "hash1")
    file_id = db.get_vault_file("/vault/doc.md")["file_id"]
    return db, file_id


def _make_chunks(n: int) -> list[dict]:
    """Build n minimal chunk dicts in order."""
    return [
        {
            "chunk_index": i,
            "heading": f"Section {i}",
            "content": f"Content for chunk {i}.",
            "char_offset": i * 100,
        }
        for i in range(n)
    ]


class TestUpsertVaultChunks:
    def test_round_trip_insert_and_read(self, db_with_file):
        """upsert_vault_chunks stores chunks; get_chunks_for_file reads them back."""
        db, file_id = db_with_file
        chunks = _make_chunks(3)
        db.upsert_vault_chunks(file_id, chunks, content_hash="abc")

        rows = db.get_chunks_for_file(file_id)
        assert len(rows) == 3
        assert rows[0]["heading"] == "Section 0"
        assert rows[1]["content"] == "Content for chunk 1."
        assert rows[2]["char_offset"] == 200

    def test_content_hash_stored_on_each_chunk(self, db_with_file):
        """content_hash argument is applied to every inserted chunk row."""
        db, file_id = db_with_file
        db.upsert_vault_chunks(file_id, _make_chunks(2), content_hash="deadbeef")
        rows = db.get_chunks_for_file(file_id)
        assert all(r["content_hash"] == "deadbeef" for r in rows)

    def test_replaces_existing_chunks(self, db_with_file):
        """Second upsert deletes old chunks and inserts new ones."""
        db, file_id = db_with_file
        db.upsert_vault_chunks(file_id, _make_chunks(5), content_hash="v1")
        assert len(db.get_chunks_for_file(file_id)) == 5

        db.upsert_vault_chunks(file_id, _make_chunks(2), content_hash="v2")
        rows = db.get_chunks_for_file(file_id)
        assert len(rows) == 2
        assert all(r["content_hash"] == "v2" for r in rows)

    def test_commit_false_does_not_auto_commit(self, tmp_path):
        """commit=False leaves data uncommitted; a new connection sees nothing."""
        import sqlite3

        db = MemoryDatabase(tmp_path / "test.db")
        db.upsert_vault_file("/vault/f.md", "f.md", "c", "h")
        file_id = db.get_vault_file("/vault/f.md")["file_id"]

        db.upsert_vault_chunks(file_id, _make_chunks(2), commit=False)

        # Data is visible within the same connection (pre-commit)
        rows = db.get_chunks_for_file(file_id)
        assert len(rows) == 2

        # Roll back to confirm no commit happened
        db._conn.rollback()
        rows_after_rollback = db.get_chunks_for_file(file_id)
        assert len(rows_after_rollback) == 0

    def test_empty_chunk_list_clears_existing(self, db_with_file):
        """Upserting an empty list removes all existing chunks for that file."""
        db, file_id = db_with_file
        db.upsert_vault_chunks(file_id, _make_chunks(3))
        db.upsert_vault_chunks(file_id, [])
        assert db.get_chunks_for_file(file_id) == []

    def test_fk_constraint_rejects_invalid_file_id(self, tmp_path):
        """Inserting chunks for a non-existent file_id raises an IntegrityError."""
        import sqlite3

        db = MemoryDatabase(tmp_path / "test.db")
        with pytest.raises(sqlite3.IntegrityError):
            db.upsert_vault_chunks(9999, _make_chunks(1), commit=False)


class TestGetChunksForFile:
    def test_ordered_by_chunk_index(self, db_with_file):
        """get_chunks_for_file returns rows ordered by chunk_index ascending."""
        db, file_id = db_with_file
        # Insert in reverse order to confirm ordering is not insertion order
        reversed_chunks = list(reversed(_make_chunks(4)))
        for chunk in reversed_chunks:
            db._conn.execute(
                """INSERT INTO vault_chunks
                   (file_id, chunk_index, heading, content, char_offset, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    file_id,
                    chunk["chunk_index"],
                    chunk.get("heading"),
                    chunk["content"],
                    chunk.get("char_offset", 0),
                    "",
                ),
            )
        db.commit()

        rows = db.get_chunks_for_file(file_id)
        indices = [r["chunk_index"] for r in rows]
        assert indices == sorted(indices)

    def test_returns_empty_list_for_unknown_file(self, db_with_file):
        """get_chunks_for_file returns [] when no chunks exist for file_id."""
        db, _ = db_with_file
        assert db.get_chunks_for_file(9999) == []

    def test_returns_list_of_dicts(self, db_with_file):
        """Each returned item is a dict with expected keys."""
        db, file_id = db_with_file
        db.upsert_vault_chunks(file_id, _make_chunks(1))
        rows = db.get_chunks_for_file(file_id)
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)
        for key in (
            "chunk_id",
            "file_id",
            "chunk_index",
            "heading",
            "content",
            "char_offset",
        ):
            assert key in rows[0]

    def test_isolated_to_file_id(self, tmp_path):
        """get_chunks_for_file only returns chunks for the requested file_id."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.upsert_vault_file("/vault/a.md", "a.md", "content", "h1")
        db.upsert_vault_file("/vault/b.md", "b.md", "content", "h2")
        fid_a = db.get_vault_file("/vault/a.md")["file_id"]
        fid_b = db.get_vault_file("/vault/b.md")["file_id"]

        db.upsert_vault_chunks(fid_a, _make_chunks(2))
        db.upsert_vault_chunks(fid_b, _make_chunks(3))

        assert len(db.get_chunks_for_file(fid_a)) == 2
        assert len(db.get_chunks_for_file(fid_b)) == 3


class TestChunkConfig:
    def test_round_trip(self, tmp_path):
        """set_chunk_config stores values; get_chunk_config retrieves them."""
        db = MemoryDatabase(tmp_path / "test.db")
        config = {"max_chunk_size": "500", "overlap": "50", "model": "all-MiniLM-L6-v2"}
        db.set_chunk_config(config)

        retrieved = db.get_chunk_config()
        assert retrieved["max_chunk_size"] == "500"
        assert retrieved["overlap"] == "50"
        assert retrieved["model"] == "all-MiniLM-L6-v2"

    def test_empty_db_returns_empty_dict(self, tmp_path):
        """get_chunk_config returns {} when no config has been stored."""
        db = MemoryDatabase(tmp_path / "test.db")
        assert db.get_chunk_config() == {}

    def test_upsert_overwrites_existing_key(self, tmp_path):
        """Calling set_chunk_config twice with the same key updates the value."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.set_chunk_config({"max_chunk_size": "200"})
        db.set_chunk_config({"max_chunk_size": "800"})

        result = db.get_chunk_config()
        assert result["max_chunk_size"] == "800"
        # Only one row for that key
        count = db.execute(
            "SELECT COUNT(*) FROM chunk_config WHERE key = 'max_chunk_size'"
        ).fetchone()[0]
        assert count == 1

    def test_values_stored_as_strings(self, tmp_path):
        """set_chunk_config coerces all values to str."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.set_chunk_config({"int_val": 42, "float_val": 3.14})
        result = db.get_chunk_config()
        assert isinstance(result["int_val"], str)
        assert isinstance(result["float_val"], str)


# ---------------------------------------------------------------------------
# vault_stale_files tests
# ---------------------------------------------------------------------------


class TestVaultStaleFiles:
    def test_returns_stale_files(self, tmp_path):
        """Files with indexed_at older than threshold appear in results."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.upsert_vault_file("/vault/old.md", "old.md", "old content", "h1")
        # Backdate indexed_at by 60 days
        db.execute(
            "UPDATE vault_files SET indexed_at = datetime('now', '-60 days') "
            "WHERE file_path = '/vault/old.md'"
        )
        db.commit()

        results = db.vault_stale_files(days=30)
        assert len(results) >= 1
        paths = [r["file_path"] for r in results]
        assert "/vault/old.md" in paths

    def test_excludes_fresh_files(self, tmp_path):
        """Recently indexed files do not appear in stale results."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.upsert_vault_file("/vault/fresh.md", "fresh.md", "new content", "h2")

        results = db.vault_stale_files(days=30)
        paths = [r["file_path"] for r in results]
        assert "/vault/fresh.md" not in paths

    def test_returns_expected_fields(self, tmp_path):
        """Each result has file_path, filename, module, indexed_at."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.upsert_vault_file("/vault/old.md", "old.md", "content", "h1", module="notes")
        db.execute(
            "UPDATE vault_files SET indexed_at = datetime('now', '-60 days') "
            "WHERE file_path = '/vault/old.md'"
        )
        db.commit()

        results = db.vault_stale_files(days=30)
        assert len(results) >= 1
        row = results[0]
        assert isinstance(row, dict)
        for key in ("file_path", "filename", "module", "indexed_at"):
            assert key in row

    def test_empty_db_returns_empty(self, tmp_path):
        """Empty database returns empty list."""
        db = MemoryDatabase(tmp_path / "test.db")
        assert db.vault_stale_files(days=1) == []

    def test_days_parameter_respected(self, tmp_path):
        """A file 10 days old is stale for days=5 but not for days=15."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.upsert_vault_file("/vault/mid.md", "mid.md", "content", "h1")
        db.execute(
            "UPDATE vault_files SET indexed_at = datetime('now', '-10 days') "
            "WHERE file_path = '/vault/mid.md'"
        )
        db.commit()

        assert len(db.vault_stale_files(days=5)) >= 1
        assert len(db.vault_stale_files(days=15)) == 0


# ---------------------------------------------------------------------------
# integrity_check(auto_repair=False) tests
# ---------------------------------------------------------------------------


class TestIntegrityCheckReadOnly:
    def test_reports_issues_without_repairing(self, tmp_path):
        """auto_repair=False reports FTS desync but does not rebuild."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory("test/1", "personal", "some content", metadata={})

        # Manually corrupt FTS by deleting from docsize (simulates desync)
        db.execute("DELETE FROM memories_fts_docsize")
        db.commit()

        result = db.integrity_check(auto_repair=False)
        assert result["status"] == "unhealthy"
        assert len(result["issues"]) > 0
        assert len(result["repairs"]) == 0

    def test_auto_repair_true_fixes_issues(self, tmp_path):
        """auto_repair=True repairs the same desync."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory("test/1", "personal", "some content", metadata={})

        db.execute("DELETE FROM memories_fts_docsize")
        db.commit()

        result = db.integrity_check(auto_repair=True)
        assert result["status"] == "repaired"
        assert len(result["repairs"]) > 0

    def test_healthy_db_returns_healthy(self, tmp_path):
        """A clean database returns status='healthy'."""
        db = MemoryDatabase(tmp_path / "test.db")
        result = db.integrity_check(auto_repair=False)
        assert result["status"] == "healthy"
        assert result["issues"] == []
        assert result["repairs"] == []

    def test_return_type_and_fields(self, tmp_path):
        """Return dict has all expected keys with correct types."""
        db = MemoryDatabase(tmp_path / "test.db")
        result = db.integrity_check(auto_repair=False)
        assert isinstance(result, dict)
        for key in (
            "status",
            "memories",
            "memories_fts",
            "vault_files",
            "vault_fts",
            "embeddings",
            "issues",
            "repairs",
        ):
            assert key in result
        assert isinstance(result["memories"], int)
        assert isinstance(result["issues"], list)
        assert isinstance(result["repairs"], list)
