"""Embedding generation and semantic search."""

from __future__ import annotations

import functools
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

from claude_innit.db.database import MemoryDatabase


class EmbeddingStore:
    """Generates and stores embeddings for semantic search."""

    MODEL_NAME = "all-MiniLM-L6-v2"
    FAISS_THRESHOLD = 50_000

    def __init__(self, db: Optional[MemoryDatabase] = None):
        """Initialize with optional database connection."""
        self.db = db
        self._model = None
        self._matrix = None  # (N, 384) pre-normalized float32
        self._recency_weights = None  # (N,) float32 — pre-computed per chunk
        self._chunk_meta = (
            None  # list of dicts: chunk_id, file_id, heading, chunk_index
        )
        self._file_meta = (
            None  # dict[file_id] -> {file_path, filename, module, modified_at}
        )
        self._matrix_loaded = False

    def _get_model(self):
        """Lazy-load the embedding model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            # Force CPU to avoid MPS issues
            self._model = SentenceTransformer(self.MODEL_NAME, device="cpu")
        return self._model

    def warm(self) -> None:
        """Pre-load the embedding model. Call at server startup to avoid timeout on first query."""
        self._get_model()

    def load_matrix(self, recency_weight: float = 0.1) -> int:
        """Pre-load all chunk embeddings into a numpy matrix.

        Also pre-computes recency weights as a numpy array.
        Call at server startup and after vault_index completes.
        Returns the number of embeddings loaded.
        """
        if self.db is None:
            return 0

        import logging
        from datetime import datetime

        # Try chunk embeddings first, fall back to legacy
        rows = self.db.execute("""
            SELECT vce.chunk_id, vce.file_id, vce.embedding,
                   vc.heading, vc.chunk_index,
                   substr(vc.content, 1, 200) as snippet
            FROM vault_chunk_embeddings vce
            JOIN vault_chunks vc ON vce.chunk_id = vc.chunk_id
        """).fetchall()

        is_chunked = len(rows) > 0
        if not rows:
            rows = self.db.execute("""
                SELECT ve.file_id, ve.embedding,
                       substr(vf.content, 1, 200) as snippet
                FROM vault_embeddings ve
                JOIN vault_files vf ON ve.file_id = vf.file_id
            """).fetchall()

        if not rows:
            self._matrix = None
            self._chunk_meta = []
            self._file_meta = {}
            self._recency_weights = None
            self._matrix_loaded = True
            return 0

        # Build file-level metadata (deduplicated)
        file_ids = set()
        for r in rows:
            file_ids.add(r["file_id"])

        self._file_meta = {}
        if file_ids:
            placeholders = ",".join("?" * len(file_ids))
            file_rows = self.db.execute(
                f"SELECT file_id, file_path, filename, module, modified_at "
                f"FROM vault_files WHERE file_id IN ({placeholders})",
                tuple(file_ids),
            ).fetchall()
            self._file_meta = {r["file_id"]: dict(r) for r in file_rows}

        # Build embedding matrix (pre-normalized)
        embeddings = np.array(
            [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        )
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        self._matrix = embeddings / norms

        # Build chunk-level metadata (compact)
        # Convert sqlite3.Row to dict for .get() access
        self._chunk_meta = []
        for r in rows:
            rd = dict(r)
            meta = {
                "file_id": rd["file_id"],
                "snippet": rd.get("snippet", ""),
            }
            if is_chunked:
                meta["chunk_id"] = rd["chunk_id"]
                meta["heading"] = rd.get("heading")
                meta["chunk_index"] = rd.get("chunk_index")
            self._chunk_meta.append(meta)

        # Pre-compute recency weights as numpy array
        now = datetime.now()
        recency = np.ones(len(rows), dtype=np.float32)
        if recency_weight > 0:
            for i, meta in enumerate(self._chunk_meta):
                file_info = self._file_meta.get(meta["file_id"], {})
                mod_at = file_info.get("modified_at")
                if mod_at:
                    try:
                        mod_dt = datetime.fromisoformat(mod_at)
                        days_ago = max(0, (now - mod_dt).days)
                        recency_factor = 1.0 / (1.0 + days_ago / 365.0)
                        recency[i] = 1.0 + recency_weight * recency_factor
                    except (ValueError, TypeError):
                        pass
        self._recency_weights = recency

        self._matrix_loaded = True

        if len(rows) > self.FAISS_THRESHOLD:
            logging.getLogger(__name__).warning(
                "Chunk count (%d) exceeds FAISS threshold (%d). "
                "Consider adding FAISS IVF index for faster search.",
                len(rows),
                self.FAISS_THRESHOLD,
            )

        return len(rows)

    @functools.lru_cache(maxsize=64)
    def _cached_query_embedding(self, query: str) -> tuple:
        """Cache query embeddings as tuples (hashable for LRU)."""
        embedding = self.generate(query)
        return tuple(embedding.tolist())

    def query_embedding(self, query: str):
        """Get normalized query embedding with LRU cache."""
        cached = self._cached_query_embedding(query)
        vec = np.array(cached, dtype=np.float32)
        vec /= np.linalg.norm(vec) + 1e-10
        return vec

    def invalidate_matrix(self) -> None:
        """Mark matrix as stale. Next search triggers reload."""
        self._matrix = None
        self._chunk_meta = None
        self._file_meta = None
        self._recency_weights = None
        self._matrix_loaded = False
        self._cached_query_embedding.cache_clear()

    def generate(self, text: str) -> np.ndarray:
        """Generate embedding for text."""
        if np is None:
            raise ImportError(
                "numpy is required for embeddings — install with: pip install claude-innit[embeddings]"
            )
        model = self._get_model()
        embedding = model.encode(text, convert_to_tensor=False)
        return np.array(embedding, dtype=np.float32)

    def store_embedding(self, memory_id: str, text: str) -> None:
        """Generate and store embedding for a memory."""
        if self.db is None:
            raise ValueError("Database required for storage")

        embedding = self.generate(text)
        blob = self._embedding_to_blob(embedding)

        self.db.execute(
            """
            INSERT OR REPLACE INTO embeddings (memory_id, embedding, model)
            VALUES (?, ?, ?)
            """,
            (memory_id, blob, self.MODEL_NAME),
        )
        self.db.commit()

    def get_embedding(self, memory_id: str) -> Optional[np.ndarray]:
        """Get embedding for a memory."""
        if self.db is None:
            return None

        row = self.db.execute(
            "SELECT embedding FROM embeddings WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()

        if row and row[0]:
            return self._blob_to_embedding(row[0])
        return None

    def semantic_search(
        self, query: str, limit: int = 10, min_similarity: float = 0.35
    ) -> list[dict]:
        """Search memories by semantic similarity."""
        if self.db is None:
            return []

        query_embedding = self.generate(query)

        rows = self.db.execute(
            """
            SELECT e.memory_id, e.embedding, m.*
            FROM embeddings e
            JOIN memories m ON e.memory_id = m.id
            """
        ).fetchall()

        results = []
        for row in rows:
            embedding = self._blob_to_embedding(row["embedding"])
            similarity = self._cosine_similarity(query_embedding, embedding)
            if similarity >= min_similarity:
                memory = dict(row)
                memory["similarity"] = float(similarity)
                results.append(memory)

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    def store_vault_embedding(self, file_id: int, text: str) -> None:
        """Generate and store embedding for a vault file."""
        if self.db is None:
            raise ValueError("Database required for storage")

        embedding = self.generate(text)
        blob = self._embedding_to_blob(embedding)

        self.db.execute(
            """
            INSERT OR REPLACE INTO vault_embeddings (file_id, embedding, model)
            VALUES (?, ?, ?)
            """,
            (file_id, blob, self.MODEL_NAME),
        )
        self.db.commit()

    def batch_store_vault_embeddings(self, limit: int = 0) -> dict:
        """Generate embeddings for vault files that don't have them yet.

        Args:
            limit: Max files to process (0 = all)

        Returns: {"generated": int, "skipped": int, "errors": int}
        """
        if self.db is None:
            raise ValueError("Database required for storage")

        query = """
            SELECT vf.file_id, vf.content, vf.filename
            FROM vault_files vf
            LEFT JOIN vault_embeddings ve ON vf.file_id = ve.file_id
            WHERE ve.file_id IS NULL
        """
        if limit > 0:
            query += f" LIMIT {limit}"

        rows = self.db.execute(query).fetchall()
        stats = {"generated": 0, "skipped": 0, "errors": 0}

        for row in rows:
            text = row["content"][:500] if row["content"] else ""
            if not text.strip():
                stats["skipped"] += 1
                continue
            try:
                self.store_vault_embedding(row["file_id"], text)
                stats["generated"] += 1
            except Exception:
                stats["errors"] += 1

        return stats

    def store_chunk_embedding(
        self, chunk_id: int, file_id: int, text: str, commit: bool = False
    ) -> None:
        """Generate and store embedding for a vault chunk.

        Args:
            commit: If True, commit immediately. Default False for batch use.
        """
        if self.db is None:
            raise ValueError("Database required for storage")
        embedding = self.generate(text)
        blob = self._embedding_to_blob(embedding)
        self.db.execute(
            """INSERT INTO vault_chunk_embeddings
               (chunk_id, file_id, embedding, model)
               VALUES (?, ?, ?, ?)""",
            (chunk_id, file_id, blob, self.MODEL_NAME),
        )
        if commit:
            self.db.commit()

    def batch_store_chunk_embeddings(self, limit: int = 0, force: bool = False) -> dict:
        """Generate chunk embeddings for vault files.

        Handles three cases:
        1. New files (no chunks yet)
        2. Changed files (content_hash differs from when chunks were made)
        3. Force mode (rechunk everything)

        Transaction strategy: commits every 100 embeddings. Both chunk
        rows and their embeddings are committed together — no window
        where chunks exist without embeddings.

        Returns: {"files_processed": int, "chunks_created": int,
                  "embeddings_generated": int, "rechunked": int, "errors": int}
        """
        if self.db is None:
            raise ValueError("Database required for storage")

        from claude_innit.utils_chunking import chunk_by_headings, get_config_dict

        # Check if chunking params have changed
        stored_config = self.db.get_chunk_config()
        current_config = get_config_dict()
        config_changed = (
            stored_config.get("max_chunk_chars") != current_config["max_chunk_chars"]
            or stored_config.get("min_chunk_chars") != current_config["min_chunk_chars"]
        )
        if config_changed:
            force = True

        if force:
            self.db.execute("DELETE FROM vault_chunk_embeddings")
            self.db.execute("DELETE FROM vault_chunks")
            self.db.commit()

        # Find files needing chunking:
        # - No chunks yet (new files)
        # - Content hash changed since last chunk (edited files)
        query = """
            SELECT vf.file_id, vf.content, vf.filename, vf.content_hash
            FROM vault_files vf
            LEFT JOIN (
                SELECT file_id, content_hash AS chunk_content_hash
                FROM vault_chunks
                WHERE chunk_index = 0
            ) vc ON vf.file_id = vc.file_id
            WHERE (vc.file_id IS NULL OR vc.chunk_content_hash != vf.content_hash)
              AND vf.content IS NOT NULL
              AND length(vf.content) > 0
        """
        if limit > 0:
            query += f" LIMIT {limit}"

        rows = self.db.execute(query).fetchall()
        stats = {
            "files_processed": 0,
            "chunks_created": 0,
            "embeddings_generated": 0,
            "rechunked": 0,
            "errors": 0,
        }

        # Track which files already had chunks (for rechunk counting)
        existing_chunk_file_ids = set()
        if not force:
            existing = self.db.execute(
                "SELECT DISTINCT file_id FROM vault_chunks"
            ).fetchall()
            existing_chunk_file_ids = {r[0] for r in existing}

        pending_commits = 0
        BATCH_SIZE = 100

        for row in rows:
            try:
                chunks = chunk_by_headings(row["content"])
                if not chunks:
                    continue

                is_rechunk = row["file_id"] in existing_chunk_file_ids

                # Store chunks (commit=False — batch loop controls commits)
                self.db.upsert_vault_chunks(
                    row["file_id"],
                    chunks,
                    content_hash=row["content_hash"],
                    commit=False,
                )

                # Get the stored chunk_ids
                stored = self.db.get_chunks_for_file(row["file_id"])
                for chunk_row in stored:
                    text = chunk_row["content"]
                    if not text.strip():
                        continue
                    self.store_chunk_embedding(
                        chunk_row["chunk_id"],
                        row["file_id"],
                        text,
                        commit=False,
                    )
                    stats["embeddings_generated"] += 1
                    pending_commits += 1

                    if pending_commits >= BATCH_SIZE:
                        self.db.commit()
                        pending_commits = 0

                stats["files_processed"] += 1
                stats["chunks_created"] += len(stored)
                if is_rechunk:
                    stats["rechunked"] += 1

            except Exception:
                # Rollback uncommitted writes for this file to prevent
                # partial state (chunks without embeddings)
                try:
                    self.db.execute("ROLLBACK")
                except Exception:
                    pass  # No active transaction to rollback
                pending_commits = 0
                stats["errors"] += 1

        # Final commit for remaining
        if pending_commits > 0:
            self.db.commit()

        # Store current config
        self.db.set_chunk_config(current_config)

        return stats

    def _embedding_to_blob(self, embedding: np.ndarray) -> bytes:
        """Convert numpy array to bytes."""
        return embedding.tobytes()

    def _blob_to_embedding(self, blob: bytes) -> np.ndarray:
        """Convert bytes to numpy array."""
        return np.frombuffer(blob, dtype=np.float32)

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
