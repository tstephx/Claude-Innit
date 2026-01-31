"""Session management tools."""

from datetime import datetime
from typing import Optional

from claude_innit.db.database import MemoryDatabase


def save_session(
    db: MemoryDatabase,
    summary: str,
    topics: Optional[list[str]] = None,
    project: Optional[str] = None,
) -> dict:
    """
    Save a session summary.

    Args:
        db: Database connection
        summary: Summary of what happened in the session
        topics: Optional list of topics covered
        project: Optional project name

    Returns:
        Dict with success status and session_id
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    session_id = f"sessions/{date_str}-{datetime.now().strftime('%H%M%S')}"

    metadata = {
        "date": date_str,
        "topics": topics or [],
    }
    if project:
        metadata["project"] = project

    try:
        db.insert_memory(
            id=session_id,
            category="session",
            content=summary,
            metadata=metadata,
        )

        return {"success": True, "session_id": session_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
