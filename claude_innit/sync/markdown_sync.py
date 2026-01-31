"""Sync markdown files to database."""

import re
from pathlib import Path
from typing import Optional

import yaml

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


class MarkdownSync:
    """Syncs markdown files to database with optional embeddings."""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

    def __init__(
        self,
        db_path: Path,
        memories_dir: Path,
        generate_embeddings: bool = False,
    ):
        """Initialize sync with database and memories directory."""
        self.db = MemoryDatabase(db_path)
        self.memories_dir = Path(memories_dir)
        self.generate_embeddings = generate_embeddings
        self._embedding_store: Optional[EmbeddingStore] = None

    def _get_embedding_store(self) -> EmbeddingStore:
        """Lazy-load embedding store."""
        if self._embedding_store is None:
            self._embedding_store = EmbeddingStore(self.db)
        return self._embedding_store

    def parse_markdown(self, file_path: Path) -> tuple[dict, str]:
        """Parse markdown file, extracting frontmatter and content."""
        text = file_path.read_text()

        frontmatter = {}
        content = text

        match = self.FRONTMATTER_PATTERN.match(text)
        if match:
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                pass
            content = text[match.end():]

        return frontmatter, content.strip()

    def detect_category(self, file_path: Path) -> str:
        """Detect category from file path."""
        rel_path = file_path.relative_to(self.memories_dir)
        parts = rel_path.parts

        if len(parts) > 0:
            first_dir = parts[0].lower()
            if first_dir in ("personal", "projects", "sessions"):
                # Normalize: projects -> project, sessions -> session
                if first_dir == "projects":
                    return "project"
                if first_dir == "sessions":
                    return "session"
                return first_dir

        return "unknown"

    def sync_file(self, file_path: Path) -> bool:
        """Sync a single markdown file to database."""
        try:
            rel_path = file_path.relative_to(self.memories_dir)
            memory_id = str(rel_path)

            frontmatter, content = self.parse_markdown(file_path)
            category = self.detect_category(file_path)

            self.db.insert_memory(
                id=memory_id,
                category=category,
                source_file=str(rel_path),
                content=content,
                metadata=frontmatter,
            )

            if self.generate_embeddings:
                store = self._get_embedding_store()
                store.store_embedding(memory_id, content)

            return True
        except Exception as e:
            print(f"Error syncing {file_path}: {e}")
            return False

    def sync_all(self) -> dict:
        """Sync all markdown files in memories directory."""
        stats = {"synced": 0, "errors": 0, "skipped": 0}

        for md_file in self.memories_dir.rglob("*.md"):
            # Skip files starting with underscore (templates, indexes)
            if md_file.name.startswith("_"):
                stats["skipped"] += 1
                continue

            if self.sync_file(md_file):
                stats["synced"] += 1
            else:
                stats["errors"] += 1

        return stats
