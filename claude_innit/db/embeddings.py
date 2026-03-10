"""Embedding generation and semantic search."""

from __future__ import annotations

import struct
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

from claude_innit.db.database import MemoryDatabase


class EmbeddingStore:
    """Generates and stores embeddings for semantic search."""

    def __init__(self, db: Optional[MemoryDatabase] = None):
        """Initialize with optional database connection."""
        self.db = db
        self._model = None

    def _get_model(self):
        """Lazy-load the embedding model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            # Force CPU to avoid MPS issues
            self._model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
        return self._model

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
            (memory_id, blob, "all-MiniLM-L6-v2"),
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

    def _embedding_to_blob(self, embedding: np.ndarray) -> bytes:
        """Convert numpy array to bytes."""
        return embedding.tobytes()

    def _blob_to_embedding(self, blob: bytes) -> np.ndarray:
        """Convert bytes to numpy array."""
        return np.frombuffer(blob, dtype=np.float32)

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
