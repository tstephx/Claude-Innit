"""Tests for embeddings module."""

import pytest
import numpy as np

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


class TestEmbeddingStore:
    """Tests for EmbeddingStore."""

    def test_generates_embedding(self):
        """Can generate embedding for text."""
        store = EmbeddingStore()
        embedding = store.generate("Hello world")

        assert isinstance(embedding, np.ndarray)
        assert embedding.shape == (384,)  # MiniLM dimension

    def test_stores_and_retrieves_embedding(self, tmp_path):
        """Can store and retrieve embeddings."""
        db = MemoryDatabase(tmp_path / "test.db")
        store = EmbeddingStore(db)

        # Insert a memory first
        db.insert_memory(
            id="test-1",
            category="personal",
            content="My name is Taylor",
            metadata={},
        )

        # Generate and store embedding
        store.store_embedding("test-1", "My name is Taylor")

        # Retrieve it
        embedding = store.get_embedding("test-1")
        assert embedding is not None
        assert embedding.shape == (384,)

    def test_semantic_search(self, tmp_path):
        """Semantic search finds similar content."""
        db = MemoryDatabase(tmp_path / "test.db")
        store = EmbeddingStore(db)

        # Insert memories
        db.insert_memory(
            id="m1",
            category="personal",
            content="I love Python programming",
            metadata={},
        )
        db.insert_memory(
            id="m2",
            category="personal",
            content="The weather is nice today",
            metadata={},
        )
        db.insert_memory(
            id="m3",
            category="personal",
            content="JavaScript and TypeScript are fun",
            metadata={},
        )

        # Generate embeddings
        store.store_embedding("m1", "I love Python programming")
        store.store_embedding("m2", "The weather is nice today")
        store.store_embedding("m3", "JavaScript and TypeScript are fun")

        # Search for programming-related content
        results = store.semantic_search("coding languages")

        # Programming-related should rank higher
        assert len(results) >= 2
        result_ids = [r["id"] for r in results]
        # m1 and m3 should be in top results
        assert "m1" in result_ids[:2] or "m3" in result_ids[:2]


def test_warm_loads_model(tmp_path):
    """EmbeddingStore.warm() should pre-load the model."""
    db = MemoryDatabase(tmp_path / "test.db")
    store = EmbeddingStore(db)
    assert store._model is None  # Not loaded yet

    store.warm()
    assert store._model is not None  # Loaded after warm()


def test_semantic_search_filters_low_similarity(tmp_path):
    """Results below min_similarity threshold are excluded."""
    from unittest.mock import patch
    import numpy as np
    from claude_innit.db.database import MemoryDatabase
    from claude_innit.db.embeddings import EmbeddingStore

    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(
        id="mem/1", category="personal", content="Python programming", metadata={}
    )

    store = EmbeddingStore(db)

    # Store a fixed embedding: uniform vector (normalized)
    fixed_embedding = np.ones(384, dtype=np.float32)
    fixed_embedding /= np.linalg.norm(fixed_embedding)
    blob = fixed_embedding.tobytes()
    db.execute(
        "INSERT INTO embeddings (memory_id, embedding, model) VALUES (?, ?, ?)",
        ("mem/1", blob, "test"),
    )
    db._conn.commit()

    # Query with orthogonal vector (cosine similarity close to 0)
    orthogonal = np.zeros(384, dtype=np.float32)
    orthogonal[0] = 1.0

    with patch.object(store, "generate", return_value=orthogonal):
        results = store.semantic_search("anything", limit=10, min_similarity=0.5)

    assert len(results) == 0  # filtered out due to low similarity


def test_semantic_search_returns_high_similarity(tmp_path):
    """Results above min_similarity are included."""
    from unittest.mock import patch
    import numpy as np
    from claude_innit.db.database import MemoryDatabase
    from claude_innit.db.embeddings import EmbeddingStore

    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(
        id="mem/1", category="personal", content="Python programming", metadata={}
    )

    store = EmbeddingStore(db)

    fixed_embedding = np.ones(384, dtype=np.float32)
    fixed_embedding /= np.linalg.norm(fixed_embedding)
    blob = fixed_embedding.tobytes()
    db.execute(
        "INSERT INTO embeddings (memory_id, embedding, model) VALUES (?, ?, ?)",
        ("mem/1", blob, "test"),
    )
    db._conn.commit()

    with patch.object(store, "generate", return_value=fixed_embedding.copy()):
        results = store.semantic_search("anything", limit=10, min_similarity=0.5)

    assert len(results) == 1
    assert results[0]["similarity"] >= 0.5
