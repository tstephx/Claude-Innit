"""Session management tools."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from claude_innit.db.database import MemoryDatabase


def _write_session_markdown(memories_dir: Path, session_id: str, summary: str, metadata: dict) -> None:
    """Write a session markdown file to the memories directory."""
    file_path = memories_dir / f"{session_id}.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {}
    if "date" in metadata:
        frontmatter["date"] = metadata["date"]
    if metadata.get("project"):
        frontmatter["project"] = metadata["project"]
    if metadata.get("topics"):
        frontmatter["topics"] = metadata["topics"]

    lines = ["---"]
    lines.append(yaml.dump(frontmatter, default_flow_style=False).rstrip())
    lines.append("---")
    lines.append("")
    lines.append(summary)
    lines.append("")

    file_path.write_text("\n".join(lines))


def save_session(
    db: MemoryDatabase,
    summary: str,
    topics: Optional[list[str]] = None,
    project: Optional[str] = None,
    memories_dir: Optional[Path] = None,
) -> dict:
    """
    Save a session summary.

    Args:
        db: Database connection
        summary: Summary of what happened in the session
        topics: Optional list of topics covered
        project: Optional project name
        memories_dir: Optional path to memories directory for writing markdown files

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

        if memories_dir:
            _write_session_markdown(memories_dir, session_id, summary, metadata)

        return {"success": True, "session_id": session_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
