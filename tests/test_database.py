"""Tests for database module."""

import pytest
from pathlib import Path

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
