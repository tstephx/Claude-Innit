"""List memories tool."""

from typing import Optional

from claude_innit.db.database import MemoryDatabase


def list_memories(
    db: MemoryDatabase,
    category: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    List stored memories with ID and content preview.

    Use this to discover memory IDs before calling forget(), or to audit
    what is stored. Returns id, preview (first 80 chars), category, updated_at.

    Args:
        db: Database connection
        category: Filter by "personal", "project", or "session"
        project: Filter project memories by project name
        limit: Max results (default 100)

    Returns:
        List of dicts with id, preview, category, updated_at
    """
    if project:
        rows = db.execute(
            """
            SELECT id, content, category, updated_at FROM memories
            WHERE category = 'project'
            AND json_extract(metadata, '$.project') = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (project, limit),
        ).fetchall()
    elif category:
        rows = db.execute(
            """
            SELECT id, content, category, updated_at FROM memories
            WHERE category = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (category, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id, content, category, updated_at FROM memories
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results = []
    for row in rows:
        content = row["content"]
        preview = content[:80] + "..." if len(content) > 80 else content
        results.append(
            {
                "id": row["id"],
                "preview": preview,
                "category": row["category"],
                "updated_at": row["updated_at"],
            }
        )
    return results
