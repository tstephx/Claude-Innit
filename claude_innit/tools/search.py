"""Search tools."""

from typing import Optional

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


def search(
    db: MemoryDatabase,
    query: str,
    method: str = "auto",
    limit: int = 10,
    embedding_store: Optional[EmbeddingStore] = None,
) -> list[dict]:
    """
    Search memories using FTS or semantic search.

    Args:
        db: Database connection
        query: Search query
        method: "auto", "text", or "semantic"
        limit: Maximum results
        embedding_store: Optional embedding store for semantic search

    Returns:
        List of matching memories
    """
    if method == "auto":
        # Short queries (1-3 words) use FTS
        word_count = len(query.split())
        if word_count <= 3:
            method = "text"
        else:
            method = "semantic"

    if method == "text":
        return db.fts_search(query, limit=limit)
    elif method == "semantic":
        if embedding_store is None:
            embedding_store = EmbeddingStore(db)
        return embedding_store.semantic_search(query, limit=limit)
    else:
        raise ValueError(f"Unknown search method: {method}")
