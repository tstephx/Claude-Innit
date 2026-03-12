"""Tests for embeddings module."""

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


# ---------------------------------------------------------------------------
# search_chunks tests (vault semantic search via EmbeddingStore)
# ---------------------------------------------------------------------------


def _setup_store_with_chunks(tmp_path, n_chunks=3):
    """Create a store with vault file + chunk embeddings loaded into matrix."""
    from unittest.mock import patch

    db = MemoryDatabase(tmp_path / "test.db")
    store = EmbeddingStore(db)

    # Insert a vault file
    db.upsert_vault_file("/vault/doc.md", "doc.md", "content", "hash1", module="notes")
    file_id = db.get_vault_file("/vault/doc.md")["file_id"]

    # Insert chunks with embeddings directly
    vec = np.ones(384, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    blob = vec.tobytes()

    for i in range(n_chunks):
        db.execute(
            """INSERT INTO vault_chunks
               (file_id, chunk_index, heading, content, char_offset, content_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_id, i, f"Section {i}", f"Chunk {i} content", i * 100, "hash1"),
        )
    db.commit()

    chunk_rows = db.get_chunks_for_file(file_id)
    for row in chunk_rows:
        db.execute(
            """INSERT INTO vault_chunk_embeddings
               (chunk_id, file_id, embedding, model) VALUES (?, ?, ?, ?)""",
            (row["chunk_id"], file_id, blob, "test-model"),
        )
    db.commit()

    # Load matrix
    store.load_matrix()

    return db, store, file_id


class TestSearchChunks:
    def test_returns_empty_when_no_db(self):
        """search_chunks returns [] when db is None."""
        store = EmbeddingStore(db=None)
        assert store.search_chunks("test") == []

    def test_returns_empty_when_matrix_empty(self, tmp_path):
        """search_chunks returns [] when matrix has no embeddings."""
        db = MemoryDatabase(tmp_path / "test.db")
        store = EmbeddingStore(db)
        store.load_matrix()  # No data -> empty matrix
        # Patch query_embedding to avoid needing the model
        store._matrix_loaded = True
        assert store.search_chunks("test") == []

    def test_returns_results_with_required_fields(self, tmp_path):
        """Results contain file_path, filename, module, similarity, snippet."""
        from unittest.mock import patch

        db, store, _ = _setup_store_with_chunks(tmp_path)

        with patch.object(store, "query_embedding", return_value=store._matrix[0]):
            results = store.search_chunks("test", limit=5)

        assert len(results) >= 1
        result = results[0]
        assert "file_path" in result
        assert "filename" in result
        assert "module" in result
        assert "similarity" in result
        assert isinstance(result["similarity"], float)

    def test_deduplicates_by_file(self, tmp_path):
        """Multiple chunks from the same file produce one result."""
        from unittest.mock import patch

        db, store, _ = _setup_store_with_chunks(tmp_path, n_chunks=5)

        with patch.object(store, "query_embedding", return_value=store._matrix[0]):
            results = store.search_chunks("test", limit=10)

        # All 5 chunks belong to the same file -> 1 result
        assert len(results) == 1

    def test_respects_min_similarity(self, tmp_path):
        """Results below min_similarity are excluded."""
        from unittest.mock import patch

        db, store, _ = _setup_store_with_chunks(tmp_path)

        # Use a near-orthogonal query vector
        orthogonal = np.zeros(384, dtype=np.float32)
        orthogonal[0] = 1.0

        with patch.object(store, "query_embedding", return_value=orthogonal):
            results = store.search_chunks("test", min_similarity=0.99)

        assert len(results) == 0

    def test_respects_limit(self, tmp_path):
        """Output respects the limit parameter."""
        from unittest.mock import patch

        db, store, _ = _setup_store_with_chunks(tmp_path)

        with patch.object(store, "query_embedding", return_value=store._matrix[0]):
            results = store.search_chunks("test", limit=0)

        assert len(results) == 0

    def test_file_filter_excludes_files(self, tmp_path):
        """file_filter=lambda returning False excludes matching files."""
        from unittest.mock import patch

        db, store, _ = _setup_store_with_chunks(tmp_path)

        with patch.object(store, "query_embedding", return_value=store._matrix[0]):
            results = store.search_chunks(
                "test",
                limit=10,
                file_filter=lambda f: f.get("module") == "nonexistent",
            )

        assert len(results) == 0

    def test_file_filter_includes_matching_files(self, tmp_path):
        """file_filter=lambda returning True includes matching files."""
        from unittest.mock import patch

        db, store, _ = _setup_store_with_chunks(tmp_path)

        with patch.object(store, "query_embedding", return_value=store._matrix[0]):
            results = store.search_chunks(
                "test",
                limit=10,
                file_filter=lambda f: f.get("module") == "notes",
            )

        assert len(results) == 1

    def test_includes_matched_heading(self, tmp_path):
        """Results include matched_heading from chunk metadata."""
        from unittest.mock import patch

        db, store, _ = _setup_store_with_chunks(tmp_path)

        with patch.object(store, "query_embedding", return_value=store._matrix[0]):
            results = store.search_chunks("test", limit=5)

        assert len(results) >= 1
        assert "matched_heading" in results[0]
