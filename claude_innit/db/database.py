"""SQLite database with FTS5 for memory storage."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class MemoryDatabase:
    """SQLite database for storing and searching memories."""

    def __init__(self, db_path: Path):
        """Initialize database, creating tables if needed."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create database tables."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                source_file TEXT,
                content TEXT NOT NULL,
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                id,
                content,
                category,
                content='memories',
                content_rowid='rowid'
            );

            CREATE TABLE IF NOT EXISTS embeddings (
                memory_id TEXT PRIMARY KEY,
                embedding BLOB,
                model TEXT,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(id, content, category)
                VALUES (new.id, new.content, new.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, id, content, category)
                VALUES ('delete', old.id, old.content, old.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, id, content, category)
                VALUES ('delete', old.id, old.content, old.category);
                INSERT INTO memories_fts(id, content, category)
                VALUES (new.id, new.content, new.category);
            END;
        """)
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute raw SQL."""
        return self._conn.execute(sql, params)

    def insert_memory(
        self,
        id: str,
        category: str,
        content: str,
        source_file: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Insert or update a memory."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO memories (id, category, source_file, content, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                category,
                source_file,
                content,
                json.dumps(metadata or {}),
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def get_memory(self, id: str) -> Optional[dict]:
        """Get a memory by ID."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (id,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def fts_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories using FTS5."""
        rows = self._conn.execute(
            """
            SELECT m.* FROM memories m
            JOIN memories_fts fts ON m.id = fts.id
            WHERE memories_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self):
        """Close database connection."""
        self._conn.close()
