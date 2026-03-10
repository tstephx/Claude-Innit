"""SQLite database with FTS5 for memory storage."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MemoryDatabase:
    """SQLite database for storing and searching memories."""

    def __init__(self, db_path: Path):
        """Initialize database, creating tables if needed."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=30, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
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

            -- Vault file index for OBF unified search
            CREATE TABLE IF NOT EXISTS vault_files (
                file_id INTEGER PRIMARY KEY,
                file_path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                frontmatter JSON,
                module TEXT,
                file_size INTEGER,
                modified_at TIMESTAMP,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS vault_files_fts USING fts5(
                file_path,
                filename,
                content,
                content='vault_files',
                content_rowid='file_id',
                tokenize='porter unicode61'
            );

            CREATE TABLE IF NOT EXISTS vault_embeddings (
                file_id INTEGER PRIMARY KEY,
                embedding BLOB,
                model TEXT,
                FOREIGN KEY (file_id) REFERENCES vault_files(file_id)
            );

            -- Vault FTS sync triggers
            CREATE TRIGGER IF NOT EXISTS vault_files_ai AFTER INSERT ON vault_files BEGIN
                INSERT INTO vault_files_fts(file_path, filename, content)
                VALUES (new.file_path, new.filename, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS vault_files_ad AFTER DELETE ON vault_files BEGIN
                INSERT INTO vault_files_fts(vault_files_fts, file_path, filename, content)
                VALUES ('delete', old.file_path, old.filename, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS vault_files_au AFTER UPDATE ON vault_files BEGIN
                INSERT INTO vault_files_fts(vault_files_fts, file_path, filename, content)
                VALUES ('delete', old.file_path, old.filename, old.content);
                INSERT INTO vault_files_fts(file_path, filename, content)
                VALUES (new.file_path, new.filename, new.content);
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

    def delete_memory(self, id: str) -> None:
        """Delete a memory and its embedding by ID."""
        self._conn.execute("DELETE FROM embeddings WHERE memory_id = ?", (id,))
        self._conn.execute("DELETE FROM memories WHERE id = ?", (id,))
        self._conn.commit()

    def fts_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories using FTS5. Sanitizes query to prevent operator injection."""
        # Wrap in double-quotes to treat entire query as a phrase, escaping internal quotes
        safe_query = '"' + query.replace('"', '""') + '"'
        try:
            rows = self._conn.execute(
                """
                SELECT m.* FROM memories m
                JOIN memories_fts fts ON m.id = fts.id
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe_query, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            logger.debug("FTS search error for query %r", query, exc_info=True)
            return []

    # --- Vault file methods ---

    def upsert_vault_file(
        self,
        file_path: str,
        filename: str,
        content: str,
        content_hash: str,
        frontmatter: Optional[dict] = None,
        module: Optional[str] = None,
        file_size: int = 0,
        modified_at: Optional[str] = None,
    ) -> None:
        """Insert or update a vault file in the index.

        Note: INSERT OR REPLACE fires DELETE + INSERT triggers (ad then ai),
        not the UPDATE trigger (au). This is correct — SQLite guarantees both
        fire within the same implicit transaction, keeping FTS in sync.
        """
        self._conn.execute(
            """
            INSERT OR REPLACE INTO vault_files
                (file_path, filename, content, content_hash, frontmatter, module,
                 file_size, modified_at, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_path,
                filename,
                content,
                content_hash,
                json.dumps(frontmatter or {}),
                module,
                file_size,
                modified_at,
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def get_vault_file(self, file_path: str) -> Optional[dict]:
        """Get a vault file by path."""
        row = self._conn.execute(
            "SELECT * FROM vault_files WHERE file_path = ?", (file_path,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def delete_vault_file(self, file_path: str) -> None:
        """Delete a vault file from the index."""
        row = self._conn.execute(
            "SELECT file_id FROM vault_files WHERE file_path = ?", (file_path,)
        ).fetchone()
        if row:
            self._conn.execute(
                "DELETE FROM vault_embeddings WHERE file_id = ?", (row["file_id"],)
            )
        self._conn.execute("DELETE FROM vault_files WHERE file_path = ?", (file_path,))
        self._conn.commit()

    def vault_fts_search(
        self, query: str, limit: int = 20, module: Optional[str] = None
    ) -> list[dict]:
        """Search vault files using FTS5. Sanitizes query to prevent operator injection."""
        safe_query = '"' + query.replace('"', '""') + '"'
        try:
            if module:
                rows = self._conn.execute(
                    """
                    SELECT vf.* FROM vault_files vf
                    JOIN vault_files_fts fts ON vf.file_id = fts.rowid
                    WHERE vault_files_fts MATCH ?
                    AND vf.module = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, module, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT vf.* FROM vault_files vf
                    JOIN vault_files_fts fts ON vf.file_id = fts.rowid
                    WHERE vault_files_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (safe_query, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            logger.debug("Vault FTS search error for query %r", query, exc_info=True)
            return []

    def vault_file_count(self, module: Optional[str] = None) -> int:
        """Count vault files, optionally filtered by module."""
        if module:
            return self._conn.execute(
                "SELECT COUNT(*) FROM vault_files WHERE module = ?", (module,)
            ).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM vault_files").fetchone()[0]

    def vault_files_by_status(self) -> dict:
        """Count vault files grouped by frontmatter status."""
        rows = self._conn.execute(
            """
            SELECT json_extract(frontmatter, '$.status') as status, COUNT(*) as cnt
            FROM vault_files
            GROUP BY status
            """
        ).fetchall()
        return {row["status"] or "unknown": row["cnt"] for row in rows}

    def vault_files_by_module(self) -> dict:
        """Count vault files grouped by module."""
        rows = self._conn.execute(
            """
            SELECT COALESCE(module, 'unassigned') as mod, COUNT(*) as cnt
            FROM vault_files
            GROUP BY module
            """
        ).fetchall()
        return {row["mod"]: row["cnt"] for row in rows}

    def vault_stale_files(self, days: int = 30) -> list[dict]:
        """Find vault files not indexed in the given number of days."""
        rows = self._conn.execute(
            """
            SELECT file_path, filename, module, indexed_at
            FROM vault_files
            WHERE indexed_at < datetime('now', ?)
            ORDER BY indexed_at ASC
            """,
            (f"-{days} days",),
        ).fetchall()
        return [dict(row) for row in rows]

    def integrity_check(self, auto_repair: bool = True) -> dict:
        """Check database integrity and optionally repair issues.

        Checks:
        - SQLite structural integrity
        - Memories FTS index sync
        - Vault files FTS index sync
        - Orphaned embeddings (memories and vault)

        Returns dict with status, issues found, and repairs made.
        """
        issues = []
        repairs = []

        # 1. SQLite integrity check
        result = self._conn.execute("PRAGMA integrity_check").fetchone()
        sqlite_ok = result[0] == "ok"
        if not sqlite_ok:
            issues.append(f"SQLite integrity: {result[0]}")

        # 2. Memories FTS index sync check
        memory_count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fts_count = self._conn.execute(
            "SELECT COUNT(*) FROM memories_fts_docsize"
        ).fetchone()[0]

        if memory_count != fts_count:
            issues.append(
                f"Memories FTS out of sync: {fts_count} FTS entries vs {memory_count} memories"
            )
            if auto_repair:
                self._conn.execute(
                    "INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"
                )
                self._conn.commit()
                new_fts_count = self._conn.execute(
                    "SELECT COUNT(*) FROM memories_fts_docsize"
                ).fetchone()[0]
                repairs.append(
                    f"Memories FTS rebuilt: {fts_count} -> {new_fts_count} entries"
                )

        # 3. Vault files FTS index sync check
        vault_count = self._conn.execute("SELECT COUNT(*) FROM vault_files").fetchone()[
            0
        ]
        vault_fts_count = self._conn.execute(
            "SELECT COUNT(*) FROM vault_files_fts_docsize"
        ).fetchone()[0]

        if vault_count != vault_fts_count:
            issues.append(
                f"Vault FTS out of sync: {vault_fts_count} FTS entries vs {vault_count} vault files"
            )
            if auto_repair:
                self._conn.execute(
                    "INSERT INTO vault_files_fts(vault_files_fts) VALUES('rebuild')"
                )
                self._conn.commit()
                new_vault_fts_count = self._conn.execute(
                    "SELECT COUNT(*) FROM vault_files_fts_docsize"
                ).fetchone()[0]
                repairs.append(
                    f"Vault FTS rebuilt: {vault_fts_count} -> {new_vault_fts_count} entries"
                )

        # 4. Orphaned memory embeddings check
        orphaned = self._conn.execute(
            """SELECT e.memory_id FROM embeddings e
               LEFT JOIN memories m ON e.memory_id = m.id
               WHERE m.id IS NULL"""
        ).fetchall()

        if orphaned:
            orphan_ids = [row[0] for row in orphaned]
            issues.append(
                f"{len(orphan_ids)} orphaned memory embeddings: {orphan_ids[:5]}"
                + ("..." if len(orphan_ids) > 5 else "")
            )
            if auto_repair:
                self._conn.execute(
                    """DELETE FROM embeddings WHERE memory_id NOT IN
                       (SELECT id FROM memories)"""
                )
                self._conn.commit()
                repairs.append(f"Removed {len(orphan_ids)} orphaned memory embeddings")

        # 5. Orphaned vault embeddings check
        orphaned_vault = self._conn.execute(
            """SELECT ve.file_id FROM vault_embeddings ve
               LEFT JOIN vault_files vf ON ve.file_id = vf.file_id
               WHERE vf.file_id IS NULL"""
        ).fetchall()

        if orphaned_vault:
            orphan_file_ids = [row[0] for row in orphaned_vault]
            issues.append(f"{len(orphan_file_ids)} orphaned vault embeddings")
            if auto_repair:
                self._conn.execute(
                    """DELETE FROM vault_embeddings WHERE file_id NOT IN
                       (SELECT file_id FROM vault_files)"""
                )
                self._conn.commit()
                repairs.append(
                    f"Removed {len(orphan_file_ids)} orphaned vault embeddings"
                )

        return {
            "status": "healthy"
            if not issues
            else ("repaired" if repairs else "unhealthy"),
            "memories": memory_count,
            "memories_fts": fts_count if memory_count == fts_count else new_fts_count,
            "vault_files": vault_count,
            "vault_fts": vault_fts_count
            if vault_count == vault_fts_count
            else new_vault_fts_count,
            "embeddings": self._conn.execute(
                "SELECT COUNT(*) FROM embeddings"
            ).fetchone()[0],
            "vault_embeddings": self._conn.execute(
                "SELECT COUNT(*) FROM vault_embeddings"
            ).fetchone()[0],
            "issues": issues,
            "repairs": repairs,
        }

    def commit(self):
        """Commit the current transaction."""
        self._conn.commit()

    def close(self):
        """Close database connection."""
        self._conn.close()
