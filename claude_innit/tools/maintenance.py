"""Database maintenance tools."""

from claude_innit.db.database import MemoryDatabase


def check_integrity(db: MemoryDatabase, auto_repair: bool = True) -> dict:
    """Run integrity check on the database.

    Validates SQLite structure, FTS index sync, and orphaned embeddings.
    Auto-repairs issues by default (FTS rebuild, orphan cleanup).
    """
    return db.integrity_check(auto_repair=auto_repair)
