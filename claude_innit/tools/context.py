"""Context loading tools."""

from typing import Optional

from claude_innit.db.database import MemoryDatabase


def get_context(
    db: MemoryDatabase,
    project: Optional[str] = None,
    include_sessions: bool = True,
    session_limit: int = 5,
) -> dict:
    """
    Load all relevant context for a session.

    Args:
        db: Database connection
        project: Optional project name to filter by
        include_sessions: Whether to include recent sessions
        session_limit: Max number of sessions to include

    Returns:
        Dictionary with personal, project, and session context
    """
    result = {
        "personal": [],
        "project": [],
        "recent_sessions": [],
    }

    # Load personal memories (always)
    personal_rows = db.execute(
        "SELECT * FROM memories WHERE category = 'personal'"
    ).fetchall()
    result["personal"] = [dict(row) for row in personal_rows]

    # Load project memories (filtered if project specified)
    if project:
        project_rows = db.execute(
            """
            SELECT * FROM memories
            WHERE category = 'project'
            AND json_extract(metadata, '$.name') = ?
            """,
            (project,),
        ).fetchall()
    else:
        project_rows = db.execute(
            "SELECT * FROM memories WHERE category = 'project'"
        ).fetchall()
    result["project"] = [dict(row) for row in project_rows]

    # Load recent sessions (filtered by project if specified)
    if include_sessions:
        if project:
            session_rows = db.execute(
                """
                SELECT * FROM memories
                WHERE category = 'session'
                AND json_extract(metadata, '$.project') = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (project, session_limit),
            ).fetchall()
            # Fall back to unfiltered if no project-specific sessions found
            if not session_rows:
                session_rows = db.execute(
                    """
                    SELECT * FROM memories
                    WHERE category = 'session'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (session_limit,),
                ).fetchall()
        else:
            session_rows = db.execute(
                """
                SELECT * FROM memories
                WHERE category = 'session'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (session_limit,),
            ).fetchall()
        result["recent_sessions"] = [dict(row) for row in session_rows]

    return result
