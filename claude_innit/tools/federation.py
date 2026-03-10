"""Federated search across vault, book-library, and session memory."""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from claude_innit.db.database import MemoryDatabase


# Default database paths
BOOK_LIBRARY_DB = (
    Path.home() / "Library" / "Application Support" / "book-library" / "library.db"
)


def _search_books(
    query: str, db_path: Path = BOOK_LIBRARY_DB, limit: int = 10
) -> list[dict]:
    """Search book-library chapters_fts via direct read-only SQLite access.

    The book-library DB has:
    - books (id, title, author, ...)
    - chapters (id, book_id, title, content, ...)
    - chapters_fts USING fts5(chapter_id, title, content)
    """
    if not db_path.exists():
        return []

    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        # Read-only: avoid blocking the book-library MCP
        conn.execute("PRAGMA query_only = ON")

        safe_query = '"' + query.replace('"', '""') + '"'
        rows = conn.execute(
            """
            SELECT
                c.id as chapter_id,
                c.title as chapter_title,
                b.title as book_title,
                b.author as book_author,
                snippet(chapters_fts, 2, '<mark>', '</mark>', '...', 40) as snippet,
                rank
            FROM chapters_fts fts
            JOIN chapters c ON c.id = fts.chapter_id
            JOIN books b ON b.id = c.book_id
            WHERE chapters_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (safe_query, limit),
        ).fetchall()

        return [
            {
                "source": "books",
                "chapter_id": row["chapter_id"],
                "title": f"{row['book_title']} — {row['chapter_title']}",
                "author": row["book_author"],
                "snippet": row["snippet"],
                "score": 1.0 - (i * 0.03),
            }
            for i, row in enumerate(rows)
        ]
    except Exception:
        logger.debug("Error searching book-library at %s", db_path, exc_info=True)
        return []
    finally:
        if conn is not None:
            conn.close()


def _search_sessions(db: MemoryDatabase, query: str, limit: int = 10) -> list[dict]:
    """Search session memories via the existing memories_fts index."""
    results = db.fts_search(query, limit=limit)
    return [
        {
            "source": "sessions",
            "memory_id": r["id"],
            "category": r["category"],
            "snippet": r["content"][:200],
            "score": 1.0 - (i * 0.03),
        }
        for i, r in enumerate(results)
    ]


def _search_vault(
    db: MemoryDatabase, query: str, limit: int = 10, module: Optional[str] = None
) -> list[dict]:
    """Search vault files via vault_files_fts."""
    results = db.vault_fts_search(query, limit=limit, module=module)
    return [
        {
            "source": "vault",
            "file_path": r["file_path"],
            "filename": r["filename"],
            "module": r.get("module"),
            "snippet": r["content"][:200],
            "score": 1.0 - (i * 0.03),
        }
        for i, r in enumerate(results)
    ]


def _reciprocal_rank_fusion(
    result_lists: list[list[dict]],
    weights: Optional[dict[str, float]] = None,
    k: int = 60,
) -> list[dict]:
    """Merge multiple result lists using weighted Reciprocal Rank Fusion.

    RRF score = sum(weight / (k + rank)) for each list the item appears in.
    """
    if weights is None:
        weights = {"vault": 1.0, "books": 0.8, "sessions": 0.7, "portfolio": 0.9}

    scored = {}
    for results in result_lists:
        for rank, item in enumerate(results):
            source = item.get("source", "unknown")
            weight = weights.get(source, 0.5)

            # Use a unique key per item
            if source == "vault":
                key = f"vault:{item.get('file_path', '')}"
            elif source == "books":
                key = f"books:{item.get('chapter_id', '')}"
            elif source == "sessions":
                key = f"sessions:{item.get('memory_id', '')}"
            else:
                key = f"{source}:{rank}"

            rrf_score = weight / (k + rank)
            if key in scored:
                scored[key]["rrf_score"] += rrf_score
            else:
                scored[key] = {**item, "rrf_score": rrf_score}

    merged = sorted(scored.values(), key=lambda x: x["rrf_score"], reverse=True)
    return merged


def federated_search(
    db: MemoryDatabase,
    query: str,
    sources: Optional[list[str]] = None,
    limit: int = 30,
    book_db_path: Optional[Path] = None,
    weights: Optional[dict[str, float]] = None,
) -> dict:
    """Search across vault, book-library, and session memory.

    Args:
        db: Claude-innit database
        query: Search query
        sources: Which sources to search. Default: ["vault", "books", "sessions"]
        limit: Max results per source and in merged
        book_db_path: Override path to book-library SQLite
        weights: RRF weights per source

    Returns:
        {"vault": [...], "books": [...], "sessions": [...], "merged": [...]}
    """
    if sources is None:
        sources = ["vault", "books", "sessions"]

    results = {}
    result_lists = []

    if "vault" in sources:
        vault_results = _search_vault(db, query, limit=limit)
        results["vault"] = vault_results
        result_lists.append(vault_results)

    if "portfolio" in sources:
        # Portfolio docs are materialized into vault with module='portfolio'
        portfolio_results = _search_vault(db, query, limit=limit, module="portfolio")
        # Re-tag source for RRF weighting
        for r in portfolio_results:
            r["source"] = "portfolio"
        results["portfolio"] = portfolio_results
        result_lists.append(portfolio_results)

    if "books" in sources:
        bp = book_db_path or BOOK_LIBRARY_DB
        book_results = _search_books(query, db_path=bp, limit=limit)
        results["books"] = book_results
        result_lists.append(book_results)

    if "sessions" in sources:
        session_results = _search_sessions(db, query, limit=limit)
        results["sessions"] = session_results
        result_lists.append(session_results)

    results["merged"] = _reciprocal_rank_fusion(result_lists, weights=weights)[:limit]
    return results
