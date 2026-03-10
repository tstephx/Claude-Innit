"""Memory management tools."""

import logging
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

import yaml

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


def _write_memory_markdown(
    memories_dir: Path, memory_id: str, content: str, metadata: dict
) -> None:
    """Write a memory markdown file to the memories directory."""
    file_path = memories_dir / f"{memory_id}.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {}
    if metadata.get("category"):
        frontmatter["category"] = metadata["category"]
    if metadata.get("project"):
        frontmatter["project"] = metadata["project"]

    lines = ["---"]
    lines.append(yaml.dump(frontmatter, default_flow_style=False).rstrip())
    lines.append("---")
    lines.append("")
    lines.append(content)
    lines.append("")

    file_path.write_text("\n".join(lines))


def remember(
    db: MemoryDatabase,
    content: str,
    category: str,
    project: Optional[str] = None,
    generate_embedding: bool = True,
    memories_dir: Optional[Path] = None,
    embedding_store: Optional[EmbeddingStore] = None,
) -> dict:
    """
    Store a new memory.

    Args:
        db: Database connection
        content: The content to remember
        category: "personal", "project", or "session"
        project: Optional project name for project memories
        generate_embedding: Whether to generate embedding
        memories_dir: Optional path to memories directory for writing markdown files
        embedding_store: Optional pre-loaded EmbeddingStore — pass the server singleton to
                         avoid reloading the model on every call

    Returns:
        Dict with success status and memory_id
    """
    memory_id = f"{category}/{uuid.uuid4().hex[:8]}"
    metadata = {}

    if project:
        metadata["project"] = project

    try:
        db.insert_memory(
            id=memory_id,
            category=category,
            content=content,
            metadata=metadata,
        )

        if memories_dir:
            fm = {"category": category}
            if project:
                fm["project"] = project
            _write_memory_markdown(memories_dir, memory_id, content, fm)

        if generate_embedding:
            try:
                store = (
                    embedding_store
                    if embedding_store is not None
                    else EmbeddingStore(db)
                )
                store.store_embedding(memory_id, content)
            except Exception:
                logger.debug(
                    "Embedding generation failed for %s", memory_id, exc_info=True
                )

        return {"success": True, "memory_id": memory_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


def forget(
    db: MemoryDatabase, memory_id: str, memories_dir: Optional[Path] = None
) -> dict:
    """
    Remove a memory permanently.

    Args:
        db: Database connection
        memory_id: ID of memory to remove
        memories_dir: Path to memories directory — required for durable deletion.
                      Without it, the markdown file survives and sync will re-insert.

    Returns:
        Dict with success status
    """
    try:
        # Delete markdown file first — if this fails, abort before touching DB
        if memories_dir is not None:
            md_file = memories_dir / f"{memory_id}.md"
            if md_file.exists():
                md_file.unlink()

        db.delete_memory(memory_id)

        if memories_dir is None:
            return {
                "success": True,
                "warning": "memories_dir not provided; markdown file was not deleted and may be re-inserted on next sync",
            }
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
